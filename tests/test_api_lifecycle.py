from k8s_upgrade_advisor.analysis.api_lifecycle import (
    detect_api_removal_findings,
    detect_behavior_findings,
    removals_in_range,
)
from k8s_upgrade_advisor.models import (
    ClusterSnapshot,
    CommandResult,
    KubeVersion,
    Severity,
)

V = KubeVersion.parse


def snapshot_serving(api_versions: str, psp_stdout: str = "") -> ClusterSnapshot:
    return ClusterSnapshot(
        kubectl={
            "api_versions": CommandResult(stdout=api_versions, returncode=0),
            "psp": CommandResult(stdout=psp_stdout, returncode=0 if psp_stdout else 1),
            "deployments": CommandResult(stdout="", returncode=0),
            "daemonsets": CommandResult(stdout="", returncode=0),
        }
    )


class TestRemovalTable:
    def test_1_28_to_1_29_catches_flowcontrol_v1beta2(self):
        hits = removals_in_range(V("1.28"), V("1.29"))
        assert any(r.group_version == "flowcontrol.apiserver.k8s.io/v1beta2" for r in hits)

    def test_1_24_to_1_25_catches_psp_and_batch(self):
        gvs = {r.group_version for r in removals_in_range(V("1.24"), V("1.25"))}
        assert "policy/v1beta1" in gvs and "batch/v1beta1" in gvs

    def test_range_is_exclusive_of_source(self):
        # PSP removed in 1.25 must NOT appear for a 1.25→1.27 path.
        gvs = {r.group_version for r in removals_in_range(V("1.25"), V("1.27"))}
        assert "policy/v1beta1" not in gvs

    def test_no_removals_in_quiet_window(self):
        assert removals_in_range(V("1.30"), V("1.31")) == []


class TestDetection:
    def test_served_gv_produces_finding(self):
        snap = snapshot_serving("apps/v1\nflowcontrol.apiserver.k8s.io/v1beta2")
        findings = detect_api_removal_findings(snap, V("1.28"), V("1.29"))
        assert len(findings) == 1
        f = findings[0]
        assert f.severity is Severity.HIGH and not f.blocking
        assert "flowcontrol" in f.title

    def test_unserved_gv_is_silent(self):
        snap = snapshot_serving("apps/v1\nflowcontrol.apiserver.k8s.io/v1")
        assert detect_api_removal_findings(snap, V("1.28"), V("1.29")) == []

    def test_psp_objects_make_blocking_critical(self):
        snap = snapshot_serving("policy/v1beta1", psp_stdout="NAME\neks.privileged")
        findings = detect_api_removal_findings(snap, V("1.24"), V("1.26"))
        psp = [f for f in findings if "PodSecurityPolicy" in f.title]
        assert psp and psp[0].blocking and psp[0].severity is Severity.CRITICAL


class TestBehaviorChanges:
    def test_dockershim_triggers_on_docker_runtime(self):
        snap = snapshot_serving("apps/v1")
        findings = detect_behavior_findings(
            snap, V("1.23"), V("1.24"), node_runtimes=["docker://20.10.7"]
        )
        assert any(f.id == "behavior-dockershim-removal" and f.blocking for f in findings)

    def test_dockershim_silent_on_containerd(self):
        snap = snapshot_serving("apps/v1")
        findings = detect_behavior_findings(
            snap, V("1.23"), V("1.24"), node_runtimes=["containerd://1.6.0"]
        )
        assert not any(f.id == "behavior-dockershim-removal" for f in findings)

    def test_skew_widening_reported_crossing_1_28(self):
        snap = snapshot_serving("apps/v1")
        findings = detect_behavior_findings(snap, V("1.27"), V("1.29"), node_runtimes=[])
        assert any(f.id == "behavior-kubelet-skew-n3" for f in findings)

    def test_outside_window_is_silent(self):
        snap = snapshot_serving("apps/v1")
        findings = detect_behavior_findings(
            snap, V("1.29"), V("1.30"), node_runtimes=["docker://20.10.7"]
        )
        assert not any(f.id == "behavior-dockershim-removal" for f in findings)
