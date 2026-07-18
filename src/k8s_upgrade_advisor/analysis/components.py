"""Addon/operator detection with version resolution.

Version evidence quality, strongest first:
  1. Helm release app_version / chart_version
  2. image tag parsed from workload listings (-o wide)
  3. presence only (CRDs / names) — version unknown

Every entry here is an upgrade-compatibility concern: CNIs, CSIs, meshes,
GitOps controllers, autoscalers, admission-webhook operators. The registry is
data, so adding a component is a table row, not code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ClusterSnapshot, DetectedComponent
from ..observability import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    display: str
    kind: str  # cni | csi | mesh | gitops | autoscaler | operator | ingress | dns | runtime
    helm_charts: tuple[str, ...] = ()
    image_markers: tuple[str, ...] = ()  # substrings found in image refs
    crd_markers: tuple[str, ...] = ()  # substrings found in `kubectl get crd`
    text_markers: tuple[str, ...] = ()  # fallback substrings in workload names


REGISTRY: tuple[ComponentSpec, ...] = (
    # ── CNI ──────────────────────────────────────────────────────────────
    ComponentSpec(
        "cilium",
        "Cilium",
        "cni",
        ("cilium",),
        ("cilium/cilium", "quay.io/cilium"),
        ("ciliumnetworkpolicies.cilium.io",),
    ),
    ComponentSpec(
        "calico",
        "Calico",
        "cni",
        ("tigera-operator", "calico"),
        ("calico/node", "calico/cni"),
        ("ippools.crd.projectcalico.org", "felixconfigurations"),
    ),
    ComponentSpec(
        "aws-vpc-cni",
        "AWS VPC CNI",
        "cni",
        ("aws-vpc-cni",),
        ("amazon-k8s-cni",),
        (),
        ("aws-node",),
    ),
    ComponentSpec("flannel", "Flannel", "cni", ("flannel",), ("flannel/flannel", "flannelcni"), ()),
    # ── CSI ──────────────────────────────────────────────────────────────
    ComponentSpec(
        "ebs-csi",
        "AWS EBS CSI Driver",
        "csi",
        ("aws-ebs-csi-driver",),
        ("aws-ebs-csi-driver",),
        (),
        ("ebs-csi-controller",),
    ),
    ComponentSpec(
        "efs-csi",
        "AWS EFS CSI Driver",
        "csi",
        ("aws-efs-csi-driver",),
        ("aws-efs-csi-driver",),
        (),
        ("efs-csi-controller",),
    ),
    ComponentSpec(
        "gce-pd-csi",
        "GCE PD CSI Driver",
        "csi",
        (),
        ("gcp-compute-persistent-disk-csi-driver",),
        (),
    ),
    ComponentSpec(
        "azuredisk-csi",
        "Azure Disk CSI Driver",
        "csi",
        ("azuredisk-csi-driver",),
        ("azuredisk-csi",),
        (),
    ),
    ComponentSpec(
        "azurefile-csi",
        "Azure File CSI Driver",
        "csi",
        ("azurefile-csi-driver",),
        ("azurefile-csi",),
        (),
    ),
    # ── Service mesh ─────────────────────────────────────────────────────
    ComponentSpec(
        "istio",
        "Istio",
        "mesh",
        ("istiod", "istio-base", "istio-ingress"),
        ("istio/pilot", "istio/proxyv2"),
        ("virtualservices.networking.istio.io",),
    ),
    ComponentSpec(
        "linkerd",
        "Linkerd",
        "mesh",
        ("linkerd-control-plane", "linkerd2"),
        ("linkerd/controller", "linkerd/proxy"),
        ("serviceprofiles.linkerd.io",),
    ),
    # ── GitOps ───────────────────────────────────────────────────────────
    ComponentSpec(
        "argocd",
        "Argo CD",
        "gitops",
        ("argo-cd", "argocd"),
        ("argoproj/argocd",),
        ("applications.argoproj.io",),
    ),
    ComponentSpec(
        "flux",
        "Flux CD",
        "gitops",
        ("flux2", "flux"),
        ("fluxcd/source-controller", "fluxcd/kustomize-controller"),
        ("kustomizations.kustomize.toolkit.fluxcd.io", "gitrepositories.source.toolkit.fluxcd.io"),
    ),
    # ── Autoscaling ──────────────────────────────────────────────────────
    ComponentSpec(
        "karpenter",
        "Karpenter",
        "autoscaler",
        ("karpenter",),
        ("karpenter/controller", "public.ecr.aws/karpenter"),
        ("nodepools.karpenter.sh", "provisioners.karpenter.sh"),
    ),
    ComponentSpec(
        "cluster-autoscaler",
        "Cluster Autoscaler",
        "autoscaler",
        ("cluster-autoscaler",),
        ("cluster-autoscaler",),
        (),
    ),
    ComponentSpec(
        "keda", "KEDA", "autoscaler", ("keda",), ("kedacore/keda",), ("scaledobjects.keda.sh",)
    ),
    # ── Ingress / DNS / core addons ──────────────────────────────────────
    ComponentSpec(
        "ingress-nginx",
        "ingress-nginx",
        "ingress",
        ("ingress-nginx",),
        ("ingress-nginx/controller",),
        (),
        ("ingress-nginx-controller",),
    ),
    ComponentSpec("coredns", "CoreDNS", "dns", ("coredns",), ("coredns/coredns", "coredns:"), ()),
    ComponentSpec(
        "metrics-server",
        "metrics-server",
        "operator",
        ("metrics-server",),
        ("metrics-server/metrics-server", "metrics-server:"),
        (),
    ),
    ComponentSpec(
        "external-dns",
        "ExternalDNS",
        "operator",
        ("external-dns",),
        ("external-dns/external-dns",),
        (),
    ),
    # ── Admission-webhook operators (upgrade-order sensitive) ────────────
    ComponentSpec(
        "cert-manager",
        "cert-manager",
        "operator",
        ("cert-manager",),
        ("jetstack/cert-manager",),
        ("certificates.cert-manager.io",),
    ),
    ComponentSpec(
        "kyverno",
        "Kyverno",
        "operator",
        ("kyverno",),
        ("kyverno/kyverno",),
        ("clusterpolicies.kyverno.io",),
    ),
    ComponentSpec(
        "gatekeeper",
        "OPA Gatekeeper",
        "operator",
        ("gatekeeper",),
        ("openpolicyagent/gatekeeper",),
        ("constrainttemplates.templates.gatekeeper.sh",),
    ),
    ComponentSpec(
        "external-secrets",
        "External Secrets Operator",
        "operator",
        ("external-secrets",),
        ("external-secrets/external-secrets",),
        ("externalsecrets.external-secrets.io",),
    ),
    # ── Observability / misc operators ───────────────────────────────────
    ComponentSpec(
        "prometheus-operator",
        "Prometheus Operator",
        "operator",
        ("kube-prometheus-stack", "prometheus-operator"),
        ("prometheus-operator/prometheus-operator",),
        ("prometheuses.monitoring.coreos.com",),
    ),
    ComponentSpec(
        "velero", "Velero", "operator", ("velero",), ("velero/velero",), ("backups.velero.io",)
    ),
)

_TAG_RE = r"[:@]v?(\d+\.\d+[\w.\-]*)"


def _version_from_images(marker: str, haystack: str) -> str | None:
    """Find 'marker...:tag' in workload image listings and return the tag."""
    m = re.search(re.escape(marker) + r"[^\s,]*" + _TAG_RE, haystack)
    if not m:
        return None
    tag = m.group(1)
    return None if tag.startswith(("sha256", "latest")) else tag


def detect_components(snapshot: ClusterSnapshot) -> list[DetectedComponent]:
    workloads_text = "\n".join(
        snapshot.stdout(key) for key in ("deployments", "daemonsets", "statefulsets")
    )
    crds_text = snapshot.stdout("crds")

    detected: list[DetectedComponent] = []
    for spec in REGISTRY:
        signals: list[str] = []
        version: str | None = None
        version_source = "presence"
        namespace: str | None = None

        # 1. Helm — best evidence
        for release in snapshot.helm_releases:
            if release.chart_name in spec.helm_charts:
                signals.append(f"helm release '{release.name}' chart {release.chart}")
                version = (release.app_version or release.chart_version).lstrip("v") or None
                version_source = "helm"
                namespace = release.namespace
                break

        # 2. Image markers — detection + version fallback
        for marker in spec.image_markers:
            if marker in workloads_text:
                signals.append(f"image '{marker}' in workloads")
                if version is None:
                    version = _version_from_images(marker, workloads_text)
                    if version:
                        version_source = "image"
                break

        # 3. CRD markers — presence
        for marker in spec.crd_markers:
            if marker in crds_text:
                signals.append(f"CRD '{marker}'")
                break

        # 4. Name fallbacks
        if not signals:
            for marker in spec.text_markers:
                if marker in workloads_text:
                    signals.append(f"workload name '{marker}'")
                    break

        if signals:
            detected.append(
                DetectedComponent(
                    key=spec.key,
                    display_name=spec.display,
                    version=version,
                    version_source=version_source if version else "presence",
                    namespace=namespace,
                    signals=signals,
                )
            )

    log.info(
        "components_detected",
        count=len(detected),
        with_versions=sum(1 for c in detected if c.version),
    )
    return detected


def spec_for(key: str) -> ComponentSpec | None:
    return next((s for s in REGISTRY if s.key == key), None)
