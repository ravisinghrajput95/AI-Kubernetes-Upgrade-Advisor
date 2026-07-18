from k8s_upgrade_advisor.analysis.risk import compute_readiness
from k8s_upgrade_advisor.analysis.skew import allowed_kubelet_skew, skew_findings
from k8s_upgrade_advisor.models import (
    Evidence,
    EvidenceMetrics,
    Finding,
    FindingCategory,
    FindingOrigin,
    KubeVersion,
    NodeInfo,
    Severity,
    Verdict,
)

V = KubeVersion.parse


def node(name: str, kubelet: str) -> NodeInfo:
    return NodeInfo(name=name, kubelet_version=kubelet)


def finding(severity: Severity, blocking: bool = False) -> Finding:
    return Finding(
        id=f"f-{severity.value}-{blocking}",
        title="t",
        category=FindingCategory.OBSERVATION,
        severity=severity,
        origin=FindingOrigin.DETERMINISTIC,
        description="d",
        blocking=blocking,
        evidence=[Evidence(kind="static-table", detail="x")],
    )


class TestSkew:
    def test_policy_widens_at_1_28(self):
        assert allowed_kubelet_skew(V("1.27")) == 2
        assert allowed_kubelet_skew(V("1.28")) == 3

    def test_violation_flagged_at_correct_hop(self):
        # kubelet 1.26 + n-3 policy: fine at CP 1.29, violates at CP 1.30.
        nodes = [node("a", "v1.26.15")]
        findings = skew_findings(nodes, V("1.27"), V("1.30"))
        skews = [f for f in findings if f.category is FindingCategory.VERSION_SKEW]
        assert len(skews) == 1 and skews[0].blocking
        assert "1.30" in skews[0].title

    def test_no_violation_within_window(self):
        nodes = [node("a", "v1.28.4")]
        findings = skew_findings(nodes, V("1.28"), V("1.29"))
        assert not any(f.category is FindingCategory.VERSION_SKEW for f in findings)

    def test_multi_hop_planning_finding(self):
        findings = skew_findings([], V("1.27"), V("1.30"))
        assert any(f.id == "upgrade-path-multi-hop" for f in findings)
        assert not any(
            f.id == "upgrade-path-multi-hop" for f in skew_findings([], V("1.28"), V("1.29"))
        )


def full_metrics(**overrides) -> EvidenceMetrics:
    values = {
        "commands_ok": 10,
        "commands_total": 10,
        "critical_ok": 9,
        "critical_total": 9,
        "components_detected": 2,
        "components_with_versions": 2,
        "kb_chunks_retrieved": 5,
    }
    values.update(overrides)
    return EvidenceMetrics(**values)


class TestReadiness:
    def test_clean_cluster_ready(self):
        readiness = compute_readiness([], full_metrics())
        assert readiness.verdict is Verdict.READY and readiness.score >= 85

    def test_blocking_finding_gives_blocked_verdict(self):
        readiness = compute_readiness([finding(Severity.CRITICAL, blocking=True)], full_metrics())
        assert readiness.verdict is Verdict.BLOCKED

    def test_severity_penalties_stack(self):
        findings = [finding(Severity.HIGH), finding(Severity.HIGH), finding(Severity.MEDIUM)]
        readiness = compute_readiness(findings, full_metrics())
        assert readiness.score == 71  # 100 - 12 - 12 - 5

    def test_missing_critical_data_caps_at_60(self):
        readiness = compute_readiness([], full_metrics(critical_ok=5))
        assert readiness.cap == 60 and readiness.score <= 60
        assert "not fully observable" in readiness.cap_reason

    def test_unknowns_cap_at_95(self):
        metrics = full_metrics()
        metrics.unknown_risks.append("no canary testing")
        readiness = compute_readiness([], metrics)
        assert readiness.cap == 95 and readiness.score <= 95

    def test_missing_data_never_raises_score(self):
        low_info = compute_readiness([], full_metrics(critical_ok=5))
        full_info = compute_readiness([], full_metrics())
        assert low_info.score <= full_info.score
        assert low_info.confidence < full_info.confidence
