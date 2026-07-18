"""Regressions for the Kubernetes-domain gaps flagged in the SIG review:
in-tree cloud staging, SA-token lifecycle, webhook mechanics, CRD storage
versions, Helm manifest scanning, kube-proxy skew, matrix confidence."""

import json

from conftest import base_kubectl, make_nodes_json
from k8s_upgrade_advisor.analysis.api_lifecycle import (
    detect_api_removal_findings,
    detect_behavior_findings,
    removals_in_range,
)
from k8s_upgrade_advisor.analysis.compatibility import compatibility_findings
from k8s_upgrade_advisor.analysis.crds import crd_findings
from k8s_upgrade_advisor.analysis.helm_compat import helm_release_findings
from k8s_upgrade_advisor.analysis.skew import kube_proxy_findings
from k8s_upgrade_advisor.analysis.webhooks import webhook_findings
from k8s_upgrade_advisor.models import (
    ClusterSnapshot,
    CommandResult,
    DetectedComponent,
    KubeVersion,
    Severity,
)

V = KubeVersion.parse


def _ok(stdout: str) -> CommandResult:
    return CommandResult(stdout=stdout, returncode=0)


class TestInTreeCloudStaging:
    """The review's factual correction: AWS breaks at 1.27, not 1.31."""

    def _snapshot(self) -> ClusterSnapshot:
        return ClusterSnapshot(
            kubectl={
                "deployments": _ok("NS NAME IMAGES\napp web nginx:1.25"),
                "daemonsets": _ok(""),
            }
        )

    def test_aws_breaks_at_1_27(self):
        findings = detect_behavior_findings(
            self._snapshot(),
            V("1.26"),
            V("1.27"),
            provider_ids=["aws:///us-east-1a/i-1"],
        )
        aws = [f for f in findings if f.id == "behavior-in-tree-cloud-removal-aws"]
        assert aws and aws[0].blocking and aws[0].severity is Severity.CRITICAL

    def test_aws_managed_eks_not_flagged(self):
        findings = detect_behavior_findings(
            self._snapshot(),
            V("1.26"),
            V("1.27"),
            provider_ids=["aws:///us-east-1a/i-1"],
            managed_control_plane=True,
        )
        assert not any("in-tree" in f.id for f in findings)

    def test_aws_with_external_ccm_not_flagged(self):
        snapshot = ClusterSnapshot(
            kubectl={
                "deployments": _ok("kube-system aws-cloud-controller-manager img:v1.27.1"),
                "daemonsets": _ok(""),
            }
        )
        findings = detect_behavior_findings(
            snapshot, V("1.26"), V("1.27"), provider_ids=["aws:///us-east-1a/i-1"]
        )
        assert not any("in-tree" in f.id for f in findings)

    def test_azure_flagged_at_1_29_not_1_27(self):
        for target, expect in (("1.28", False), ("1.29", True)):
            findings = detect_behavior_findings(
                self._snapshot(),
                V("1.27"),
                V(target),
                provider_ids=["azure:///subscriptions/x/vm0"],
            )
            hit = any(f.id == "behavior-in-tree-cloud-disabled-azure-gce-vsphere" for f in findings)
            assert hit is expect, target

    def test_gce_provider_not_matched_by_aws_entry(self):
        findings = detect_behavior_findings(
            self._snapshot(), V("1.26"), V("1.27"), provider_ids=["gce://proj/zone/vm"]
        )
        assert not any("aws" in f.id for f in findings)


class TestServiceAccountTokens:
    def test_1_24_autogeneration_change_fires(self):
        snapshot = ClusterSnapshot(kubectl={"deployments": _ok(""), "daemonsets": _ok("")})
        findings = detect_behavior_findings(snapshot, V("1.23"), V("1.25"))
        assert any(f.id == "behavior-sa-token-no-autogeneration" for f in findings)

    def test_1_29_cleanup_is_informational(self):
        snapshot = ClusterSnapshot(kubectl={"deployments": _ok(""), "daemonsets": _ok("")})
        findings = detect_behavior_findings(snapshot, V("1.28"), V("1.29"))
        cleanup = [f for f in findings if f.id == "behavior-sa-legacy-token-cleanup"]
        assert cleanup and cleanup[0].severity is Severity.INFO


class TestRegistryFreezeTimeBased:
    def test_fires_outside_its_version_window(self):
        # 1.28→1.29 does not cross 1.27, but the freeze is calendar-based.
        snapshot = ClusterSnapshot(
            kubectl={
                "deployments": _ok("NS NAME IMAGES\nkube-system dns k8s.gcr.io/coredns:1.8.6"),
                "daemonsets": _ok(""),
            }
        )
        findings = detect_behavior_findings(snapshot, V("1.28"), V("1.29"))
        assert any(f.id == "behavior-legacy-registry-freeze" for f in findings)


class TestTableCompletions:
    def test_1_22_includes_apiregistration_and_auth_apis(self):
        gvs = {r.group_version for r in removals_in_range(V("1.21"), V("1.22"))}
        assert "apiregistration.k8s.io/v1beta1" in gvs
        assert "authentication.k8s.io/v1beta1" in gvs
        assert "authorization.k8s.io/v1beta1" in gvs

    def test_1_16_includes_networkpolicy(self):
        kinds = {
            kind for removal in removals_in_range(V("1.15"), V("1.16")) for kind in removal.kinds
        }
        assert "NetworkPolicy" in kinds and "PodSecurityPolicy" in kinds


class TestPsaLabelVerification:
    def _snapshot(self, ns_stdout: str) -> ClusterSnapshot:
        return ClusterSnapshot(
            kubectl={
                "api_versions": _ok("policy/v1beta1"),
                "psp": _ok("NAME\nrestricted"),
                "namespaces": _ok(ns_stdout),
                "deployments": _ok(""),
                "daemonsets": _ok(""),
            }
        )

    def test_missing_psa_labels_adds_evidence(self):
        snapshot = self._snapshot("NAME LABELS\ndefault <none>")
        findings = detect_api_removal_findings(snapshot, V("1.24"), V("1.25"))
        psp = next(f for f in findings if "PodSecurityPolicy" in f.title)
        assert any("pod-security.kubernetes.io" in e.detail for e in psp.evidence)

    def test_present_psa_labels_no_extra_evidence(self):
        snapshot = self._snapshot("NAME LABELS\nprod pod-security.kubernetes.io/enforce=baseline")
        findings = detect_api_removal_findings(snapshot, V("1.24"), V("1.25"))
        psp = next(f for f in findings if "PodSecurityPolicy" in f.title)
        assert not any("uncontrolled" in e.detail for e in psp.evidence)


class TestWebhookMechanics:
    def _config(self, webhooks_list) -> str:
        return json.dumps({"items": [{"metadata": {"name": "cfg"}, "webhooks": webhooks_list}]})

    def test_unscoped_fail_policy_flagged(self):
        snapshot = ClusterSnapshot(
            kubectl={
                "validating_webhooks_json": _ok(
                    self._config(
                        [
                            {"name": "gate.example.com", "failurePolicy": "Fail"},
                        ]
                    )
                ),
            }
        )
        findings = webhook_findings(snapshot)
        assert findings[0].id == "webhook-fail-policy-unscoped"
        assert findings[0].severity is Severity.HIGH

    def test_default_failure_policy_treated_as_fail(self):
        snapshot = ClusterSnapshot(
            kubectl={
                "mutating_webhooks_json": _ok(self._config([{"name": "defaulted.example.com"}])),
            }
        )
        assert any(f.id == "webhook-fail-policy-unscoped" for f in webhook_findings(snapshot))

    def test_scoped_or_ignore_webhooks_pass(self):
        snapshot = ClusterSnapshot(
            kubectl={
                "validating_webhooks_json": _ok(
                    self._config(
                        [
                            {
                                "name": "scoped",
                                "failurePolicy": "Fail",
                                "namespaceSelector": {"matchLabels": {"env": "prod"}},
                            },
                            {"name": "soft", "failurePolicy": "Ignore"},
                        ]
                    )
                ),
            }
        )
        assert not any(f.id == "webhook-fail-policy-unscoped" for f in webhook_findings(snapshot))

    def test_long_timeout_noted(self):
        snapshot = ClusterSnapshot(
            kubectl={
                "validating_webhooks_json": _ok(
                    self._config(
                        [
                            {"name": "slow", "failurePolicy": "Ignore", "timeoutSeconds": 30},
                        ]
                    )
                ),
            }
        )
        assert any(f.id == "webhook-long-timeouts" for f in webhook_findings(snapshot))


class TestCrdStorageVersions:
    def _crd(self, name, versions, stored):
        return {
            "metadata": {"name": name},
            "spec": {"versions": versions},
            "status": {"storedVersions": stored},
        }

    def test_legacy_stored_version_flagged(self):
        crd = self._crd(
            "widgets.example.com",
            [
                {"name": "v1", "served": True, "storage": True},
                {"name": "v1beta1", "served": False, "storage": False},
            ],
            ["v1beta1", "v1"],
        )
        snapshot = ClusterSnapshot(kubectl={"crds_json": _ok(json.dumps({"items": [crd]}))})
        findings = crd_findings(snapshot)
        assert findings[0].id == "crd-storage-version-migration"
        assert "widgets.example.com" in findings[0].affected_objects[0]

    def test_clean_crd_silent(self):
        crd = self._crd("ok.example.com", [{"name": "v1", "served": True, "storage": True}], ["v1"])
        snapshot = ClusterSnapshot(kubectl={"crds_json": _ok(json.dumps({"items": [crd]}))})
        assert crd_findings(snapshot) == []

    def test_served_deprecated_version_noted(self):
        crd = self._crd(
            "old.example.com",
            [
                {"name": "v1", "served": True, "storage": True},
                {"name": "v1beta1", "served": True, "deprecated": True},
            ],
            ["v1"],
        )
        snapshot = ClusterSnapshot(kubectl={"crds_json": _ok(json.dumps({"items": [crd]}))})
        assert any(f.id == "crd-deprecated-versions-served" for f in crd_findings(snapshot))


class TestHelmManifestScan:
    def test_release_with_removed_api_flagged(self):
        snapshot = ClusterSnapshot(
            helm_manifests={
                "ingress/legacy-app": "apiVersion: policy/v1beta1\nkind: PodDisruptionBudget\n",
                "kube-system/fine-app": "apiVersion: apps/v1\nkind: Deployment\n",
            }
        )
        findings = helm_release_findings(snapshot, V("1.24"), V("1.25"))
        assert len(findings) == 1
        assert "ingress/legacy-app" in findings[0].title
        assert "mapkubeapis" in findings[0].remediation

    def test_outside_window_silent(self):
        snapshot = ClusterSnapshot(
            helm_manifests={"ns/app": "apiVersion: policy/v1beta1\nkind: PodDisruptionBudget\n"}
        )
        assert helm_release_findings(snapshot, V("1.25"), V("1.26")) == []


class TestKubeProxySkew:
    def _snapshot(self, tag: str) -> ClusterSnapshot:
        return ClusterSnapshot(
            kubectl={"daemonsets": _ok(f"kube-system kube-proxy registry.k8s.io/kube-proxy:{tag}")}
        )

    def test_newer_than_apiserver_is_blocking(self):
        findings = kube_proxy_findings(self._snapshot("v1.29.2"), V("1.28"), V("1.29"))
        assert findings and findings[0].blocking
        assert "newer" in findings[0].title

    def test_within_window_silent(self):
        assert kube_proxy_findings(self._snapshot("v1.28.4"), V("1.28"), V("1.29")) == []

    def test_falls_out_of_skew_on_long_path(self):
        findings = kube_proxy_findings(self._snapshot("v1.26.9"), V("1.26"), V("1.30"))
        assert findings and findings[0].blocking and "1.30" in findings[0].title


class TestMatrixConfidence:
    def test_inferred_matrix_downgraded_and_never_gates(self):
        argo = DetectedComponent(
            key="argocd", display_name="Argo CD", version="2.5.0", version_source="helm"
        )
        entries, findings = compatibility_findings([argo], V("1.29"))
        assert findings and findings[0].severity is Severity.MEDIUM
        assert not findings[0].blocking
        assert "inferred" in entries[0].notes

    def test_published_matrix_keeps_severity(self):
        cm = DetectedComponent(
            key="cert-manager", display_name="cert-manager", version="1.11.0", version_source="helm"
        )
        _entries, findings = compatibility_findings([cm], V("1.29"))
        assert findings[0].severity is Severity.HIGH


class TestManagedFixtureUnaffected:
    def test_eks_fixture_gets_no_in_tree_finding(self, eks_snapshot):
        from k8s_upgrade_advisor.analysis import run_deterministic_analysis

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        assert not any("in-tree" in f.id for f in report.findings)


def _unused_make_helpers():  # keep conftest helpers imported for future cases
    return base_kubectl, make_nodes_json
