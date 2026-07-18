"""Regression tests for the production-readiness review fixes."""

import json

import pytest
import requests

from conftest import base_kubectl, make_nodes_json
from k8s_upgrade_advisor.analysis import run_deterministic_analysis
from k8s_upgrade_advisor.analysis.api_lifecycle import (
    HORIZON_FINDING_ID,
    KNOWLEDGE_HORIZON,
    horizon_findings,
)
from k8s_upgrade_advisor.analysis.planner import _addon_requirements
from k8s_upgrade_advisor.analysis.profile import detect_flavour
from k8s_upgrade_advisor.config import KnowledgeSettings
from k8s_upgrade_advisor.knowledge.fetcher import DocumentFetcher, RawDocument
from k8s_upgrade_advisor.knowledge.sources import DocSource
from k8s_upgrade_advisor.models import (
    ClusterFlavour,
    ClusterProfileSummary,
    ClusterSnapshot,
    DetectedComponent,
    KubeVersion,
)

V = KubeVersion.parse


class TestKnowledgeHorizon:
    def test_beyond_horizon_emits_finding(self):
        findings = horizon_findings(V(KNOWLEDGE_HORIZON), V("1.35"))
        assert len(findings) == 1
        assert findings[0].id == HORIZON_FINDING_ID
        assert not findings[0].blocking

    def test_within_horizon_silent(self):
        assert horizon_findings(V("1.28"), V(KNOWLEDGE_HORIZON)) == []

    def test_readiness_capped_at_70(self, eks_snapshot):
        report = run_deterministic_analysis(eks_snapshot, V("1.33"), V("1.35"))
        assert any(f.id == HORIZON_FINDING_ID for f in report.findings)
        assert report.readiness.cap <= 70
        assert "horizon" in report.readiness.cap_reason

    def test_within_horizon_not_capped_by_horizon(self, eks_snapshot):
        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        assert "horizon" not in report.readiness.cap_reason


class TestAwsDisambiguation:
    def test_self_managed_on_aws_is_not_eks(self):
        nodes = make_nodes_json(2, "v1.28.4", "aws:///us-east-1a/i-0abc")
        snapshot = ClusterSnapshot(kubectl=base_kubectl("v1.28.4", nodes))
        flavour, evidence = detect_flavour(snapshot)
        assert flavour is not ClusterFlavour.EKS
        assert any("self-managed" in e for e in evidence)

    def test_eks_labels_still_win_without_gitversion(self):
        nodes = make_nodes_json(
            2,
            "v1.28.4",
            "aws:///us-east-1a/i-0abc",
            {"eks.amazonaws.com/nodegroup": "workers"},
        )
        snapshot = ClusterSnapshot(kubectl=base_kubectl("v1.28.4", nodes))
        assert detect_flavour(snapshot)[0] is ClusterFlavour.EKS


class TestPlannerAddonRequirements:
    def test_lockstep_versions_in_addon_step(self):
        profile = ClusterProfileSummary(
            components=[
                DetectedComponent(key="cluster-autoscaler", display_name="Cluster Autoscaler"),
                DetectedComponent(key="cilium", display_name="Cilium"),
            ]
        )
        text = _addon_requirements(profile, V("1.29"))
        assert "Cluster Autoscaler → >=1.29" in text
        assert "Cilium → >=1.15" in text

    def test_no_components_no_text(self):
        assert _addon_requirements(ClusterProfileSummary(), V("1.29")) == ""


class _FakeResponse:
    def __init__(self, status_code=200, text="# Doc\n\ncontent " * 30, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"Content-Type": "text/plain"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class TestFetcher:
    SOURCE = DocSource("doc-a", "Doc A", "https://example.invalid/a", "component-docs", "istio")

    def _fetcher(self, tmp_path, responses):
        fetcher = DocumentFetcher(KnowledgeSettings(fetch_retries=1), tmp_path / "raw")
        calls = {"n": 0}

        def fake_get(url, timeout=None, headers=None):
            calls["n"] += 1
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        fetcher.session.get = fake_get
        return fetcher, calls

    def test_fetch_and_cache(self, tmp_path):
        fetcher, calls = self._fetcher(tmp_path, [_FakeResponse()])
        doc = fetcher.fetch(self.SOURCE)
        assert doc is not None and "content" in doc.content
        # Second call served from cache — no HTTP.
        doc2 = fetcher.fetch(self.SOURCE)
        assert doc2 is not None and calls["n"] == 1

    def test_retry_then_success(self, tmp_path):
        fetcher, calls = self._fetcher(
            tmp_path, [requests.ConnectionError("boom"), _FakeResponse()]
        )
        doc = fetcher.fetch(self.SOURCE)
        assert doc is not None and calls["n"] == 2

    def test_stale_cache_beats_nothing(self, tmp_path):
        raw_dir = tmp_path / "raw"
        stale = RawDocument(
            key="doc-a",
            title="Doc A",
            url="u",
            kind="component-docs",
            component="istio",
            k8s_version=None,
            content="old content",
            fetched_at="2020-01-01T00:00:00+00:00",
        )
        stale.save(raw_dir)
        fetcher, _calls = self._fetcher(
            tmp_path, [requests.ConnectionError("down"), requests.ConnectionError("down")]
        )
        doc = fetcher.fetch(self.SOURCE)  # cache too old → fetch → fails → stale returned
        assert doc is not None and doc.content == "old content"

    def test_total_failure_returns_none(self, tmp_path):
        fetcher, _calls = self._fetcher(
            tmp_path, [requests.ConnectionError("down"), requests.ConnectionError("down")]
        )
        assert fetcher.fetch(self.SOURCE) is None


class TestFixtureRealism:
    def test_psp_absent_on_1_26_fixture(self, eks_snapshot):
        assert not eks_snapshot.command("psp").ok


class TestTokenUsage:
    def test_usage_captured_from_provider(self, eks_snapshot):
        from k8s_upgrade_advisor.llm.advisor import run_llm_analysis

        class UsageProvider:
            name, model = "fake", "fake-1"
            last_usage = {"prompt_tokens": 1234, "completion_tokens": 567}

            def complete_json(self, system, user):
                return json.dumps({"executive_summary": "ok", "citations_used": []})

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        merged = run_llm_analysis(report, "", [], UsageProvider())
        assert merged.llm.prompt_tokens == 1234
        assert merged.llm.completion_tokens == 567


@pytest.mark.integration
class TestApiFixes:
    def _client(self, settings):
        from fastapi.testclient import TestClient

        from k8s_upgrade_advisor.api.app import create_app

        return TestClient(create_app(settings))

    def test_frontend_served_from_package(self, settings):
        response = self._client(settings).get("/")
        assert response.status_code == 200
        assert "k8s-upgrade-advisor" in response.text

    def test_snapshot_size_limit_enforced(self, settings, eks_snapshot):
        settings.server.max_snapshot_bytes = 100
        body = {
            "source_version": "1.26",
            "target_version": "1.29",
            "dry_run": True,
            "snapshot": json.loads(eks_snapshot.model_dump_json()),
        }
        response = self._client(settings).post("/api/v1/assessments", json=body)
        assert response.status_code == 413
        assert "exceeds limit" in response.json()["detail"]

    def test_report_survives_server_restart(self, settings, eks_snapshot):
        body = {
            "source_version": "1.26",
            "target_version": "1.29",
            "dry_run": True,
            "snapshot": json.loads(eks_snapshot.model_dump_json()),
        }
        report = self._client(settings).post("/api/v1/assessments", json=body).json()
        # Fresh app instance = simulated restart; memory store is empty,
        # retrieval must fall back to the persisted JSON artifact.
        restarted = self._client(settings)
        fetched = restarted.get(f"/api/v1/assessments/{report['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["readiness"]["verdict"] == report["readiness"]["verdict"]
        assert restarted.get(f"/api/v1/assessments/{report['id']}/html").status_code == 200

    def test_traversal_shaped_id_is_404(self, settings):
        response = self._client(settings).get("/api/v1/assessments/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 404

    def test_readyz_reports_kb_from_manifest(self, settings, kb_store, tmp_path):
        settings.paths.kb_dir = tmp_path / "kb"
        body = self._client(settings).get("/readyz").json()
        assert body["kb_loaded"] is True
        assert body["kb_chunks"] == kb_store.manifest.chunk_count


class TestCitationHonesty:
    def test_no_citations_used_means_empty_sources(self, eks_snapshot):
        from k8s_upgrade_advisor.llm.advisor import run_llm_analysis
        from k8s_upgrade_advisor.models import Citation

        class NoCiteProvider:
            name, model = "fake", "fake-1"

            def complete_json(self, system, user):
                return json.dumps({"executive_summary": "ungrounded", "citations_used": []})

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        retrieved = [Citation(ref=1, title="doc", url="https://x")]
        merged = run_llm_analysis(report, "ctx", retrieved, NoCiteProvider())
        assert merged.citations == []
