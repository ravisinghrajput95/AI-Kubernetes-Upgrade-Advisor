"""Documentation source registry.

Everything the RAG layer knows comes from here: which documents to fetch,
what metadata to stamp on them (component, k8s_version), and therefore what
the retriever can filter on. Adding a source is a data change.

URLs prefer raw upstream markdown (GitHub CHANGELOGs, docs repos) over
rendered HTML — stabler to parse and friendlier to upstream servers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import KubeVersion


@dataclass(frozen=True)
class DocSource:
    key: str  # cache key / doc_id
    title: str
    url: str
    kind: str  # release-notes | deprecation | skew | provider | component-docs
    component: str | None = None  # canonical component key, when applicable
    k8s_version: str | None = None  # stamped when the doc is version-specific


_K8S_CORE: tuple[DocSource, ...] = (
    DocSource(
        "k8s-deprecation-guide",
        "Kubernetes Deprecated API Migration Guide",
        "https://kubernetes.io/docs/reference/using-api/deprecation-guide/",
        "deprecation",
    ),
    DocSource(
        "k8s-skew-policy",
        "Kubernetes Version Skew Policy",
        "https://kubernetes.io/releases/version-skew-policy/",
        "skew",
    ),
    DocSource(
        "k8s-upgrade-kubeadm",
        "Upgrading kubeadm clusters",
        "https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-upgrade/",
        "component-docs",
        component="kubeadm",
    ),
)

_PROVIDERS: tuple[DocSource, ...] = (
    DocSource(
        "eks-versions",
        "Amazon EKS Kubernetes versions",
        "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html",
        "provider",
        component="eks",
    ),
    DocSource(
        "eks-update-cluster",
        "Amazon EKS — Updating a cluster",
        "https://docs.aws.amazon.com/eks/latest/userguide/update-cluster.html",
        "provider",
        component="eks",
    ),
    DocSource(
        "gke-release-notes",
        "GKE release notes",
        "https://cloud.google.com/kubernetes-engine/docs/release-notes",
        "provider",
        component="gke",
    ),
    DocSource(
        "gke-upgrades",
        "GKE cluster upgrades",
        "https://cloud.google.com/kubernetes-engine/docs/concepts/cluster-upgrades",
        "provider",
        component="gke",
    ),
    DocSource(
        "aks-supported-versions",
        "AKS supported Kubernetes versions",
        "https://learn.microsoft.com/en-us/azure/aks/supported-kubernetes-versions",
        "provider",
        component="aks",
    ),
    DocSource(
        "aks-upgrade",
        "AKS — Upgrade a cluster",
        "https://learn.microsoft.com/en-us/azure/aks/upgrade-cluster",
        "provider",
        component="aks",
    ),
    DocSource(
        "openshift-updating",
        "OpenShift — Updating clusters overview",
        "https://docs.openshift.com/container-platform/latest/updating/index.html",
        "provider",
        component="openshift",
    ),
    DocSource(
        "rke2-upgrade",
        "RKE2 upgrade documentation",
        "https://docs.rke2.io/upgrades/manual_upgrade",
        "provider",
        component="rke2",
    ),
)

_COMPONENTS: tuple[DocSource, ...] = (
    DocSource(
        "cert-manager-supported",
        "cert-manager supported releases",
        "https://cert-manager.io/docs/releases/",
        "component-docs",
        "cert-manager",
    ),
    DocSource(
        "ingress-nginx-support",
        "ingress-nginx supported versions table",
        "https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/README.md",
        "component-docs",
        "ingress-nginx",
    ),
    DocSource(
        "istio-support-status",
        "Istio supported releases & version compatibility",
        "https://istio.io/latest/docs/releases/supported-releases/",
        "component-docs",
        "istio",
    ),
    DocSource(
        "cilium-k8s-compat",
        "Cilium Kubernetes compatibility",
        "https://docs.cilium.io/en/stable/network/kubernetes/compatibility/",
        "component-docs",
        "cilium",
    ),
    DocSource(
        "calico-requirements",
        "Calico system requirements",
        "https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements",
        "component-docs",
        "calico",
    ),
    DocSource(
        "karpenter-compat",
        "Karpenter compatibility matrix",
        "https://karpenter.sh/docs/upgrading/compatibility/",
        "component-docs",
        "karpenter",
    ),
    DocSource(
        "cluster-autoscaler-readme",
        "Cluster Autoscaler README (version policy)",
        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/cluster-autoscaler/README.md",
        "component-docs",
        "cluster-autoscaler",
    ),
    DocSource(
        "argocd-compat",
        "Argo CD tested Kubernetes versions",
        "https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/",
        "component-docs",
        "argocd",
    ),
    DocSource(
        "flux-prerequisites",
        "Flux prerequisites & supported versions",
        "https://fluxcd.io/flux/installation/",
        "component-docs",
        "flux",
    ),
    DocSource(
        "keda-compat",
        "KEDA Kubernetes compatibility",
        "https://keda.sh/docs/latest/operate/cluster/",
        "component-docs",
        "keda",
    ),
    DocSource(
        "kyverno-compat",
        "Kyverno compatibility matrix",
        "https://kyverno.io/docs/installation/",
        "component-docs",
        "kyverno",
    ),
    DocSource(
        "gatekeeper-docs",
        "OPA Gatekeeper docs",
        "https://open-policy-agent.github.io/gatekeeper/website/docs/install/",
        "component-docs",
        "gatekeeper",
    ),
    DocSource(
        "external-secrets-docs",
        "External Secrets Operator — getting started",
        "https://external-secrets.io/latest/introduction/getting-started/",
        "component-docs",
        "external-secrets",
    ),
    DocSource(
        "prometheus-operator-compat",
        "Prometheus Operator compatibility",
        "https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/main/Documentation/compatibility.md",
        "component-docs",
        "prometheus-operator",
    ),
    DocSource(
        "ebs-csi-readme",
        "AWS EBS CSI driver README",
        "https://raw.githubusercontent.com/kubernetes-sigs/aws-ebs-csi-driver/master/README.md",
        "component-docs",
        "ebs-csi",
    ),
    DocSource(
        "metrics-server-readme",
        "metrics-server README (compatibility matrix)",
        "https://raw.githubusercontent.com/kubernetes-sigs/metrics-server/master/README.md",
        "component-docs",
        "metrics-server",
    ),
    DocSource(
        "coredns-k8s-versions",
        "CoreDNS version in Kubernetes",
        "https://raw.githubusercontent.com/coredns/deployment/master/kubernetes/CoreDNS-k8s_version.md",
        "component-docs",
        "coredns",
    ),
    DocSource(
        "helm-version-support",
        "Helm version support policy",
        "https://helm.sh/docs/topics/version_skew/",
        "component-docs",
        "helm",
    ),
    DocSource(
        "linkerd-releases",
        "Linkerd supported releases",
        "https://linkerd.io/releases/",
        "component-docs",
        "linkerd",
    ),
    DocSource(
        "velero-compat",
        "Velero compatibility matrix",
        "https://raw.githubusercontent.com/vmware-tanzu/velero/main/README.md",
        "component-docs",
        "velero",
    ),
)


def release_note_sources(source: KubeVersion, target: KubeVersion) -> list[DocSource]:
    """Official CHANGELOG for every version in the upgrade window, source
    version included (context for what the cluster runs today)."""
    versions = [source, *source.minors_until(target)]
    return [
        DocSource(
            key=f"k8s-changelog-{v.minor_str.replace('.', '-')}",
            title=f"Kubernetes {v.minor_str} CHANGELOG",
            url=(
                "https://raw.githubusercontent.com/kubernetes/kubernetes/"
                f"master/CHANGELOG/CHANGELOG-{v.minor_str}.md"
            ),
            kind="release-notes",
            k8s_version=v.minor_str,
        )
        for v in versions
    ]


def all_sources(
    source: KubeVersion, target: KubeVersion, components: list[str] | None = None
) -> list[DocSource]:
    """Full fetch list for an upgrade window. When ``components`` is given,
    component docs are limited to those detected in the cluster (plus core
    and provider docs, which are always relevant)."""
    docs = [*release_note_sources(source, target), *_K8S_CORE, *_PROVIDERS]
    for src in _COMPONENTS:
        if components is None or src.component in components:
            docs.append(src)
    return docs
