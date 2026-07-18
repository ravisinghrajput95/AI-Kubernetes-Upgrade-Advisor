"""Production-hardening sprint regressions: retrieval golden set, load
shedding, retention, and stage observability."""

import json

import pytest

from k8s_upgrade_advisor.config import RetrievalSettings
from k8s_upgrade_advisor.knowledge.chunker import chunk_documents
from k8s_upgrade_advisor.knowledge.embeddings import HashingEmbedder
from k8s_upgrade_advisor.knowledge.fetcher import RawDocument
from k8s_upgrade_advisor.knowledge.store import KnowledgeStore
from k8s_upgrade_advisor.models import AssessmentReport, ReadinessScore, Verdict
from k8s_upgrade_advisor.reporting import prune_reports, save_reports
from k8s_upgrade_advisor.retrieval import HybridRetriever

# ── Retrieval golden set ─────────────────────────────────────────────────────
# A fixed corpus and query→expected-doc pairs. If chunking, embedding, BM25,
# fusion, or filtering regress, these fail. Deliberately includes near-miss
# distractors (wrong version, wrong component).

GOLDEN_CORPUS = [
    (
        "k8s-changelog-1-29",
        "Kubernetes 1.29 CHANGELOG",
        None,
        "1.29",
        "release-notes",
        "## API removals\n\nThe flowcontrol.apiserver.k8s.io/v1beta2 API version of FlowSchema "
        "and PriorityLevelConfiguration is no longer served in v1.29. Migrate to the v1 API.",
    ),
    (
        "k8s-changelog-1-28",
        "Kubernetes 1.28 CHANGELOG",
        None,
        "1.28",
        "release-notes",
        "## Changes\n\nKubernetes 1.28 widens supported kubelet version skew to three minor "
        "versions behind kube-apiserver under KEP-3935. Node upgrade cadence can relax.",
    ),
    (
        "k8s-changelog-1-27",
        "Kubernetes 1.27 CHANGELOG",
        None,
        "1.27",
        "release-notes",
        "## Deprecations\n\nThe k8s.gcr.io registry is frozen; all images must be pulled from "
        "registry.k8s.io from Kubernetes 1.27 onward. Update manifests referencing old paths.",
    ),
    (
        "cert-manager-supported",
        "cert-manager supported releases",
        "cert-manager",
        None,
        "component-docs",
        "## Supported releases\n\ncert-manager 1.14 supports Kubernetes 1.24 through 1.29. "
        "Upgrade cert-manager before the control plane; its webhook gates admissions.",
    ),
    (
        "istio-support",
        "Istio supported releases",
        "istio",
        None,
        "component-docs",
        "## Support status\n\nIstio 1.20 is tested on Kubernetes 1.25 to 1.29. Upgrade the "
        "Istio control plane before data plane sidecars during cluster upgrades.",
    ),
    (
        "karpenter-compat",
        "Karpenter compatibility",
        "karpenter",
        None,
        "component-docs",
        "## Compatibility\n\nKarpenter 0.34 requires Kubernetes 1.29 or newer. Migrate "
        "v1alpha5 Provisioner resources to NodePool custom resources before upgrading.",
    ),
    (
        "skew-policy",
        "Kubernetes Version Skew Policy",
        None,
        None,
        "skew",
        "## Skew policy\n\nkube-apiserver must be upgraded one minor at a time. kubelet may "
        "be up to three minors older than kube-apiserver since 1.28, two before that.",
    ),
    (
        "eks-versions",
        "Amazon EKS Kubernetes versions",
        "eks",
        None,
        "provider",
        "## EKS calendar\n\nAmazon EKS supports each Kubernetes minor for 14 months of "
        "standard support. Extended support adds 12 months with additional pricing.",
    ),
]

GOLDEN_QUERIES = [
    ("flowcontrol v1beta2 FlowSchema removed", "k8s-changelog-1-29"),
    ("kubelet version skew three minors apiserver", "skew-policy"),
    ("cert-manager kubernetes 1.29 compatibility webhook", "cert-manager-supported"),
    ("karpenter NodePool v1alpha5 Provisioner migration", "karpenter-compat"),
    ("istio control plane upgrade order data plane", "istio-support"),
    ("EKS support calendar extended support months", "eks-versions"),
    ("registry.k8s.io image registry frozen migration", "k8s-changelog-1-27"),
]


@pytest.fixture(scope="module")
def golden_retriever(tmp_path_factory):
    docs = [
        RawDocument(key, title, f"https://example.test/{key}", kind, component, version, content)
        for key, title, component, version, kind, content in GOLDEN_CORPUS
    ]
    chunks = chunk_documents(docs, 700, 100)
    embedder = HashingEmbedder()
    store = KnowledgeStore.build(
        chunks,
        embedder,
        tmp_path_factory.mktemp("golden") / "kb",
        "1.27",
        "1.29",
        700,
        100,
        len(docs),
    )
    return HybridRetriever(store, embedder, RetrievalSettings(top_k=3))


class TestRetrievalGoldens:
    @pytest.mark.parametrize(("query", "expected_doc"), GOLDEN_QUERIES)
    def test_expected_doc_in_top3(self, golden_retriever, query, expected_doc):
        bundle = golden_retriever.retrieve(
            [query],
            allowed_versions={"1.27", "1.28", "1.29"},
            installed_components={"cert-manager", "istio", "karpenter"},
        )
        keys = [entry.chunk.doc_key for entry in bundle.entries]
        assert expected_doc in keys, f"{query!r} → {keys}"

    def test_version_filter_holds_on_golden_corpus(self, golden_retriever):
        bundle = golden_retriever.retrieve(
            ["kubelet skew widened 1.28"],
            allowed_versions={"1.29"},  # excludes the 1.28-stamped changelog
            installed_components=set(),
        )
        assert all(entry.chunk.k8s_version != "1.28" for entry in bundle.entries)


# ── Retention ────────────────────────────────────────────────────────────────


def _fake_report(i: int) -> AssessmentReport:
    return AssessmentReport(
        id=f"assess-20260718-{i:06d}-abc{i:03d}",
        source_version="1.28",
        target_version="1.29",
        readiness=ReadinessScore(score=90, confidence=90, verdict=Verdict.READY),
    )


class TestRetention:
    def test_prune_keeps_newest(self, tmp_path):
        for i in range(5):
            save_reports(_fake_report(i), tmp_path)
        removed = prune_reports(tmp_path, keep=2)
        assert removed == 3
        stems = {p.stem for p in tmp_path.iterdir()}
        assert stems == {_fake_report(3).id, _fake_report(4).id}
        # All three artifact kinds pruned together.
        assert len(list(tmp_path.iterdir())) == 6

    def test_zero_disables_pruning(self, tmp_path):
        for i in range(3):
            save_reports(_fake_report(i), tmp_path, keep=0)
        assert len(list(tmp_path.iterdir())) == 9

    def test_save_reports_applies_retention(self, tmp_path):
        for i in range(4):
            save_reports(_fake_report(i), tmp_path, keep=2)
        assert len({p.stem for p in tmp_path.iterdir()}) == 2


# ── Load shedding ────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestLoadShedding:
    def test_saturated_capacity_returns_503(self, settings, eks_snapshot):
        from fastapi.testclient import TestClient

        from k8s_upgrade_advisor.api.app import create_app

        app = create_app(settings)
        client = TestClient(app)
        body = {
            "source_version": "1.26",
            "target_version": "1.29",
            "dry_run": True,
            "snapshot": json.loads(eks_snapshot.model_dump_json()),
        }
        # Deterministically saturate every slot, as concurrent requests would.
        acquired = 0
        while app.state.assessment_slots.acquire(blocking=False):
            acquired += 1
        assert acquired == settings.server.max_concurrent_assessments
        try:
            response = client.post("/api/v1/assessments", json=body)
            assert response.status_code == 503
            assert response.headers.get("retry-after") == "30"
            assert "saturated" in response.json()["detail"]
        finally:
            for _ in range(acquired):
                app.state.assessment_slots.release()
        # Capacity released → next request succeeds.
        assert client.post("/api/v1/assessments", json=body).status_code == 200


# ── Stage observability ──────────────────────────────────────────────────────


class TestStageMetrics:
    def test_stage_histogram_and_inflight_recorded(self, settings, eks_snapshot):
        from k8s_upgrade_advisor.observability import metrics
        from k8s_upgrade_advisor.service import assess

        def stage_count(stage: str) -> float:
            for family in metrics.registry.collect():
                if family.name == "advisor_assessment_stage_seconds":
                    for sample in family.samples:
                        if sample.name.endswith("_count") and sample.labels.get("stage") == stage:
                            return sample.value
            return 0.0

        before = {s: stage_count(s) for s in ("deterministic", "retrieval")}
        assess(eks_snapshot, "1.26", "1.29", settings, dry_run=True)
        for stage in ("deterministic", "retrieval"):
            assert stage_count(stage) == before[stage] + 1
        # In-flight gauge returns to zero after completion.
        for family in metrics.registry.collect():
            if family.name == "advisor_assessments_in_flight":
                assert family.samples[0].value == 0
