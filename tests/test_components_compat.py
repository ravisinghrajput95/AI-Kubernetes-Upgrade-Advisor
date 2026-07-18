from k8s_upgrade_advisor.analysis.compatibility import (
    compatibility_findings,
    evaluate_component,
)
from k8s_upgrade_advisor.analysis.components import detect_components
from k8s_upgrade_advisor.models import (
    CompatibilityStatus,
    DetectedComponent,
    KubeVersion,
    Severity,
)

V = KubeVersion.parse


class TestDetection:
    def test_helm_beats_image_tag(self, helm_snapshot):
        detected = {c.key: c for c in detect_components(helm_snapshot)}
        cm = detected["cert-manager"]
        assert cm.version == "1.14.4"  # helm app_version, not image 1.99.0
        assert cm.version_source == "helm"
        assert cm.namespace == "cert-manager"

    def test_image_tag_fallback(self, eks_snapshot):
        detected = {c.key: c for c in detect_components(eks_snapshot)}
        assert detected["karpenter"].version == "0.31.0"
        assert detected["karpenter"].version_source == "image"

    def test_absent_component_not_detected(self, gke_snapshot):
        keys = {c.key for c in detect_components(gke_snapshot)}
        assert "istio" not in keys and "cilium" not in keys


class TestCompatibility:
    def test_upgrade_required(self):
        component = DetectedComponent(
            key="cert-manager", display_name="cert-manager", version="1.11.0", version_source="helm"
        )
        entry = evaluate_component(component, V("1.29"))
        assert entry.status is CompatibilityStatus.UPGRADE_REQUIRED
        assert entry.minimum_version == "1.14"

    def test_compatible(self):
        component = DetectedComponent(
            key="cert-manager", display_name="cert-manager", version="1.14.4", version_source="helm"
        )
        assert evaluate_component(component, V("1.29")).status is CompatibilityStatus.COMPATIBLE

    def test_unknown_version(self):
        component = DetectedComponent(
            key="cert-manager", display_name="cert-manager", version=None, signals=["CRD"]
        )
        entry = evaluate_component(component, V("1.29"))
        assert entry.status is CompatibilityStatus.UNKNOWN

    def test_untracked_component_stays_unknown(self):
        component = DetectedComponent(key="velero", display_name="Velero", version="1.13.0")
        assert evaluate_component(component, V("1.29")).status is CompatibilityStatus.UNKNOWN

    def test_cni_incompat_is_blocking_critical(self):
        cilium = DetectedComponent(
            key="cilium", display_name="Cilium", version="1.13.0", version_source="helm"
        )
        _entries, findings = compatibility_findings([cilium], V("1.31"))
        assert findings and findings[0].blocking
        assert findings[0].severity is Severity.CRITICAL

    def test_operator_incompat_not_blocking(self):
        cm = DetectedComponent(
            key="cert-manager", display_name="cert-manager", version="1.11.0", version_source="helm"
        )
        _entries, findings = compatibility_findings([cm], V("1.29"))
        assert findings and not findings[0].blocking
        assert findings[0].severity is Severity.HIGH

    def test_unknown_version_emits_medium_finding(self):
        cm = DetectedComponent(
            key="cert-manager",
            display_name="cert-manager",
            version=None,
            signals=["CRD 'certificates.cert-manager.io'"],
        )
        _entries, findings = compatibility_findings([cm], V("1.29"))
        assert findings[0].severity is Severity.MEDIUM
