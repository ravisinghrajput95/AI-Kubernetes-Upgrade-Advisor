"""The trust boundary between deterministic truth and LLM output."""

import json

import pytest

from k8s_upgrade_advisor.analysis import run_deterministic_analysis
from k8s_upgrade_advisor.errors import LLMResponseInvalid
from k8s_upgrade_advisor.llm.advisor import run_llm_analysis
from k8s_upgrade_advisor.models import Citation, FindingOrigin, KubeVersion, Severity

V = KubeVersion.parse


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, system: str, user: str) -> str:
        self.calls += 1
        return self.responses.pop(0)


def analysis_payload(**overrides) -> str:
    payload = {
        "executive_summary": "The cluster requires component upgrades before proceeding.",
        "risk_narrative": "cert-manager lags behind [DOC 1].",
        "additional_findings": [
            {
                "id": "llm-guess",
                "title": "Model-invented critical blocker",
                "category": "observation",
                "severity": "critical",
                "origin": "deterministic",  # model lies about origin
                "description": "should be demoted",
                "blocking": True,  # model tries to block
            }
        ],
        "citations_used": [1, 99],  # 99 does not exist
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def report(eks_snapshot):
    return run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))


CITATIONS = [Citation(ref=1, title="doc one", url="https://x/1")]


class TestTrustBoundary:
    def test_ungrounded_llm_finding_demoted_to_low(self, report):
        # The payload's finding cites nothing — speculation lands at LOW.
        provider = FakeProvider([analysis_payload()])
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        added = [f for f in merged.findings if f.id == "llm-guess"]
        assert added[0].origin is FindingOrigin.LLM
        assert added[0].blocking is False
        assert added[0].severity is Severity.LOW
        assert added[0].description.startswith("[ungrounded")

    def test_grounded_llm_finding_capped_at_high(self, report):
        provider = FakeProvider(
            [
                analysis_payload(
                    additional_findings=[
                        {
                            "id": "llm-grounded",
                            "title": "Document-backed critical claim",
                            "category": "observation",
                            "severity": "critical",
                            "origin": "llm",
                            "description": "supported by a retrieved doc",
                            "blocking": True,
                            "evidence": [
                                {"kind": "kb-document", "detail": "see doc", "citation_refs": [1]}
                            ],
                        }
                    ]
                )
            ]
        )
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        added = [f for f in merged.findings if f.id == "llm-grounded"]
        assert added[0].severity is Severity.HIGH  # demoted from critical, kept HIGH
        assert added[0].blocking is False
        assert "[ungrounded" not in added[0].description

    def test_scores_unchanged_by_llm(self, report):
        before = (report.readiness.score, report.readiness.verdict)
        provider = FakeProvider([analysis_payload()])
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        assert (merged.readiness.score, merged.readiness.verdict) == before

    def test_invalid_citation_refs_dropped(self, report):
        provider = FakeProvider([analysis_payload()])
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        assert [c.ref for c in merged.citations] == [1]

    def test_duplicate_finding_ids_skipped(self, report):
        existing = report.findings[0].id
        provider = FakeProvider(
            [
                analysis_payload(
                    additional_findings=[
                        {
                            "id": existing,
                            "title": "dupe",
                            "category": "observation",
                            "severity": "low",
                            "origin": "llm",
                            "description": "dupe",
                        }
                    ]
                )
            ]
        )
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        assert sum(1 for f in merged.findings if f.id == existing) == 1

    def test_repair_roundtrip_on_invalid_json(self, report):
        provider = FakeProvider(["not json at all", analysis_payload()])
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        assert provider.calls == 2
        assert merged.executive_summary.startswith("The cluster requires")

    def test_two_failures_raise(self, report):
        provider = FakeProvider(["nope", "still nope"])
        with pytest.raises(LLMResponseInvalid):
            run_llm_analysis(report, "ctx", CITATIONS, provider)

    def test_dry_run_never_calls_provider(self, report):
        provider = FakeProvider([])
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider, dry_run=True)
        assert provider.calls == 0
        assert merged.llm.dry_run and "[dry-run]" in merged.executive_summary

    def test_checklists_merge_is_append_only(self, report):
        baseline = list(report.plan.pre_upgrade_checklist)
        provider = FakeProvider(
            [
                analysis_payload(
                    plan={
                        "strategy": "s",
                        "steps": [{"order": 1, "phase": "control-plane", "title": "hop"}],
                        "pre_upgrade_checklist": ["extra item"],
                    },
                )
            ]
        )
        merged = run_llm_analysis(report, "ctx", CITATIONS, provider)
        for item in baseline:
            assert item in merged.plan.pre_upgrade_checklist
        assert "extra item" in merged.plan.pre_upgrade_checklist
        assert merged.plan.rollback  # deterministic rollback preserved
