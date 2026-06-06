"""
k8s_assess/cluster_profile.py

Detects cluster type (Kind, Docker Desktop, EKS, GKE, AKS, RKE2, k3s, …)
from kubectl output and returns a ClusterProfile that tells the rest of the
tool:

  - Which missing kubectl commands are expected (not a risk gap)
  - Which components are managed by the cloud provider
  - Which upgrade path applies (in-place, blue/green, managed API)
  - What the correct risk framing is for this cluster type
  - Context for the LLM prompt
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ── Cluster flavour ───────────────────────────────────────────────────────────

class ClusterFlavour(str, Enum):
    # Local / dev
    KIND            = "kind"
    DOCKER_DESKTOP  = "docker-desktop"
    MINIKUBE        = "minikube"
    K3S             = "k3s"
    K3D             = "k3d"
    MICROK8S        = "microk8s"
    RANCHER_DESKTOP = "rancher-desktop"

    # Managed cloud
    EKS             = "eks"      # AWS Elastic Kubernetes Service
    GKE             = "gke"      # Google Kubernetes Engine
    AKS             = "aks"      # Azure Kubernetes Service
    OKE             = "oke"      # Oracle Container Engine
    DOKS            = "doks"     # DigitalOcean Kubernetes
    CIVO            = "civo"
    LINODE          = "linode"

    # Self-managed / on-prem
    KUBEADM         = "kubeadm"
    RKE             = "rke"
    RKE2            = "rke2"
    OPENSHIFT       = "openshift"
    TANZU           = "tanzu"
    GENERIC         = "generic"


# ── Detection signals ─────────────────────────────────────────────────────────

# Each entry: (flavour, list-of-signal-strings)
# Signals are substring-matched against combined lowercase kubectl stdout.
# Order matters: more specific flavours listed first.
_DETECTION_RULES: list[tuple[ClusterFlavour, list[str]]] = [
    # ── Local ────────────────────────────────────────────────────────────
    (ClusterFlavour.KIND, [
        "kind-",                       # node name prefix
        "kindnet",                     # CNI pod
        "kind.x-k8s.io",              # node label
        "ingress-nginx-controller.*kind",
    ]),
    (ClusterFlavour.DOCKER_DESKTOP, [
        "docker-desktop",              # node name / context
        "docker.io/kindest",
        "storage-provisioner.*docker",
        "vpnkit",
    ]),
    (ClusterFlavour.MINIKUBE, [
        "minikube",                    # node name
        "storage-provisioner.*minikube",
        "minikube.sigs.k8s.io",
    ]),
    (ClusterFlavour.K3D, [
        "k3d-",                        # node name prefix
        "k3s.io.*k3d",
    ]),
    (ClusterFlavour.K3S, [
        "k3s",
        "k3s.io",
        "k3s-server",
        "k3s-agent",
    ]),
    (ClusterFlavour.MICROK8S, [
        "microk8s",
        "microk8s.io",
    ]),
    (ClusterFlavour.RANCHER_DESKTOP, [
        "rancher-desktop",
        "rdx-proxy",
    ]),

    # ── Managed cloud ────────────────────────────────────────────────────
    (ClusterFlavour.EKS, [
        "eks.amazonaws.com",
        "alpha.eksctl.io",
        "ec2.internal",                # AWS internal node DNS
        "compute.internal",            # AWS EC2 hostname pattern
        "aws-node",                    # VPC CNI daemonset
        "kube-proxy.*eks",
        "eks-",                        # node name prefix on Fargate/managed nodes
        "amazonaws.com",
    ]),
    (ClusterFlavour.GKE, [
        "gke-",                        # node name prefix
        "cloud.google.com",
        "gke.io",
        "fluentbit-gke",
        "pdcsi-node",                  # GKE PD CSI
        "gke-metrics-agent",
        "kube-dns.*gke",
    ]),
    (ClusterFlavour.AKS, [
        "aks-",                        # node name prefix
        "azure.com",
        "kubernetes.azure.com",
        "omsagent",                    # Azure Monitor
        "azure-ip-masq-agent",
        "azuredisk-csi",
        "cloud-node-manager",          # Azure cloud-node-manager daemonset
    ]),
    (ClusterFlavour.OKE, [
        "oke-",
        "oraclecloud.com",
        "oracle.com/oke",
    ]),
    (ClusterFlavour.DOKS, [
        "digitalocean.com",
        "doks-",
    ]),
    (ClusterFlavour.CIVO, [
        "civo.com",
        "k3s.*civo",
    ]),
    (ClusterFlavour.LINODE, [
        "linode.com",
        "lke-",
    ]),

    # ── Self-managed ─────────────────────────────────────────────────────
    (ClusterFlavour.OPENSHIFT, [
        "openshift",
        "openshift.io",
        "machine.openshift.io",
    ]),
    (ClusterFlavour.RKE2, [
        "rke2",
        "rke.cattle.io/rke2",
    ]),
    (ClusterFlavour.RKE, [
        "cattle.io",
        "rke.cattle.io",
        "rancher",
    ]),
    (ClusterFlavour.KUBEADM, [
        "kubeadm.kubernetes.io",
        "node-role.kubernetes.io/control-plane",   # kubeadm label
        "node.kubernetes.io/exclude-from-external-load-balancers",
    ]),
    (ClusterFlavour.TANZU, [
        "vsphere.local",
        "tkg.tanzu.vmware.com",
        "capv.vmware.com",
    ]),
]


# ── Per-flavour metadata ──────────────────────────────────────────────────────

@dataclass
class FlavourMeta:
    display:             str
    managed:             bool       # cloud provider controls the control plane
    upgrade_mechanism:   str        # "managed-api" | "in-place" | "rolling-replace" | "blue-green"
    metrics_expected:    bool       # is metrics-server typically present?
    psp_expected:        bool       # PSP likely before removal version?
    managed_components:  list[str]  # components the provider manages (model should know)
    upgrade_notes:       str        # key sentence for the LLM prompt context
    missing_ok:          list[str]  # kubectl commands expected to fail on this cluster type


_FLAVOUR_META: dict[ClusterFlavour, FlavourMeta] = {
    ClusterFlavour.KIND: FlavourMeta(
        display="Kind (Kubernetes-in-Docker)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "Kind clusters are typically ephemeral dev/test environments. "
            "Upgrade is usually done by recreating the cluster with a newer Kind image. "
            "Resource pressure metrics (kubectl top) are not available unless "
            "metrics-server is explicitly installed. "
            "Missing CRDs/webhooks are expected in a vanilla Kind cluster."
        ),
        missing_ok=["top_nodes", "top_pods", "pod_security"],
    ),
    ClusterFlavour.DOCKER_DESKTOP: FlavourMeta(
        display="Docker Desktop (local single-node)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "Docker Desktop provides a single-node Kubernetes cluster intended for "
            "local development only. Upgrades are performed via the Docker Desktop UI "
            "settings panel, not kubectl. Resource pressure metrics require manually "
            "installing metrics-server. There are no production workloads or HA concerns."
        ),
        missing_ok=["top_nodes", "top_pods", "pod_security", "pvs"],
    ),
    ClusterFlavour.MINIKUBE: FlavourMeta(
        display="Minikube (local single-node)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,   # minikube addons enable metrics-server
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "Minikube is a local single-node dev cluster. Upgrade is performed via "
            "'minikube start --kubernetes-version=<target>'. The metrics-server addon "
            "may or may not be enabled."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.K3S: FlavourMeta(
        display="k3s (lightweight Kubernetes)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,   # k3s bundles metrics-server by default
        psp_expected=False,
        managed_components=["metrics-server", "coredns", "traefik"],
        upgrade_notes=(
            "k3s is a lightweight Kubernetes distribution that bundles metrics-server, "
            "CoreDNS, and Traefik by default. Upgrade via 'curl -sfL https://get.k3s.io "
            "| INSTALL_K3S_VERSION=<target> sh -'. Some PSP/RBAC differences from "
            "upstream may apply. Traefik ingress controller compatibility should be verified."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.K3D: FlavourMeta(
        display="k3d (k3s in Docker)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["metrics-server", "coredns", "traefik"],
        upgrade_notes=(
            "k3d runs k3s in Docker containers. Upgrade is done by recreating the "
            "cluster with a newer k3s image tag. Inherits k3s bundled components."
        ),
        missing_ok=["top_nodes", "top_pods", "pod_security"],
    ),
    ClusterFlavour.MICROK8S: FlavourMeta(
        display="MicroK8s (Canonical)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["metrics-server", "coredns"],
        upgrade_notes=(
            "MicroK8s upgrades via 'sudo snap refresh microk8s --channel=<target>/stable'. "
            "Snap channels control the Kubernetes version. Addon compatibility should be "
            "verified before upgrading."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.RANCHER_DESKTOP: FlavourMeta(
        display="Rancher Desktop (local)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "Rancher Desktop is a local dev Kubernetes environment. "
            "Upgrade via the Rancher Desktop UI preferences panel."
        ),
        missing_ok=["top_nodes", "top_pods", "pod_security"],
    ),
    ClusterFlavour.EKS: FlavourMeta(
        display="Amazon EKS (managed)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=[
            "control-plane", "etcd", "kube-apiserver", "kube-scheduler",
            "kube-controller-manager", "coredns", "kube-proxy", "vpc-cni (aws-node)",
        ],
        upgrade_notes=(
            "EKS is a managed Kubernetes service. Control plane upgrade is performed via "
            "the AWS Console, CLI ('aws eks update-cluster-version'), or Terraform. "
            "Worker nodes must be upgraded separately (managed node groups, self-managed "
            "nodes, or Fargate). Key EKS-specific concerns: VPC CNI (aws-node) version "
            "compatibility, EKS add-on versions (CoreDNS, kube-proxy, EBS CSI driver), "
            "launch template AMI compatibility, EKS Fargate profile compatibility. "
            "EKS enforces one minor version increment at a time."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.GKE: FlavourMeta(
        display="Google Kubernetes Engine (managed)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=[
            "control-plane", "etcd", "kube-apiserver", "kube-scheduler",
            "kube-controller-manager", "coredns", "kube-proxy",
            "fluentbit-gke", "gke-metrics-agent", "pdcsi-node",
        ],
        upgrade_notes=(
            "GKE is a managed Kubernetes service. Upgrades can be manual or via "
            "GKE release channels (rapid/regular/stable). Control plane upgrades "
            "are initiated via Google Cloud Console or 'gcloud container clusters upgrade'. "
            "Node pools upgrade separately and support surge upgrades and blue/green "
            "node pool strategies. Key GKE-specific concerns: Autopilot vs Standard mode, "
            "Workload Identity compatibility, GKE Dataplane V2 (eBPF/Cilium), "
            "GKE add-on versions (ConfigConnector, Cloud Logging, etc)."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.AKS: FlavourMeta(
        display="Azure Kubernetes Service (managed)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=[
            "control-plane", "etcd", "kube-apiserver", "kube-scheduler",
            "kube-controller-manager", "coredns", "kube-proxy",
            "azure-ip-masq-agent", "cloud-node-manager", "omsagent",
        ],
        upgrade_notes=(
            "AKS is a managed Kubernetes service. Control plane upgrade via Azure Portal, "
            "CLI ('az aks upgrade'), or Terraform. Node pools upgrade separately and support "
            "surge settings. Key AKS-specific concerns: Azure CNI vs kubenet compatibility, "
            "Azure Disk/File CSI driver add-on versions, Azure Policy add-on, "
            "AAD Pod Identity vs Workload Identity migration, "
            "node image version separate from Kubernetes version. "
            "AKS enforces one minor version upgrade at a time."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.OKE: FlavourMeta(
        display="Oracle Container Engine for Kubernetes (managed)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["control-plane", "etcd", "coredns", "kube-proxy"],
        upgrade_notes=(
            "OKE is Oracle's managed Kubernetes service. Upgrade via OCI Console or CLI. "
            "Virtual nodes differ from managed nodes in upgrade behaviour."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.DOKS: FlavourMeta(
        display="DigitalOcean Kubernetes (managed)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["control-plane", "etcd", "coredns"],
        upgrade_notes=(
            "DOKS is DigitalOcean's managed Kubernetes. Upgrade via doctl or the "
            "DigitalOcean Console. Node pools drain and replace automatically."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.KUBEADM: FlavourMeta(
        display="kubeadm (self-managed)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "kubeadm-managed cluster. Upgrade procedure: "
            "1) upgrade kubeadm, 2) kubeadm upgrade plan, 3) kubeadm upgrade apply, "
            "4) drain and upgrade each node's kubelet and kubectl. "
            "Control plane HA topology must be considered. "
            "etcd upgrade must be co-ordinated with API server version."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.RKE2: FlavourMeta(
        display="RKE2 (Rancher Kubernetes Engine 2)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["coredns", "metrics-server", "rke2-ingress-nginx"],
        upgrade_notes=(
            "RKE2 upgrade via 'curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=<v> sh -' "
            "on each node, server nodes first. RKE2 bundles CoreDNS, metrics-server, "
            "and an ingress-nginx variant. Verify Rancher Manager compatibility matrix "
            "if Rancher is installed on top of RKE2."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.RKE: FlavourMeta(
        display="RKE (Rancher Kubernetes Engine)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "RKE upgrade via 'rke up' with an updated cluster.yml. "
            "Verify Rancher Manager compatibility if installed. "
            "Rancher and RKE have their own supported Kubernetes version matrix."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.OPENSHIFT: FlavourMeta(
        display="OpenShift (Red Hat)",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["openshift-apiserver", "etcd", "coredns", "prometheus"],
        upgrade_notes=(
            "OpenShift uses its own upgrade mechanism (Cluster Version Operator / CVO). "
            "Upgrade via 'oc adm upgrade'. OpenShift has its own API compatibility layer "
            "and many upstream Kubernetes APIs are wrapped or replaced. "
            "PodSecurityPolicy was replaced by SCCs (Security Context Constraints) in OpenShift. "
            "Operator Lifecycle Manager (OLM) manages operator upgrades separately."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.TANZU: FlavourMeta(
        display="VMware Tanzu (TKG)",
        managed=True,
        upgrade_mechanism="managed-api",
        metrics_expected=True,
        psp_expected=False,
        managed_components=["control-plane", "etcd", "coredns"],
        upgrade_notes=(
            "Tanzu Kubernetes Grid upgrade via Tanzu CLI or Tanzu Mission Control. "
            "TKG has its own supported Kubernetes version matrix tied to TKG releases."
        ),
        missing_ok=["pod_security"],
    ),
    ClusterFlavour.GENERIC: FlavourMeta(
        display="Generic / Unknown Kubernetes",
        managed=False,
        upgrade_mechanism="in-place",
        metrics_expected=False,
        psp_expected=False,
        managed_components=[],
        upgrade_notes=(
            "Cluster type could not be determined from available data. "
            "Applying generic self-managed upgrade assumptions."
        ),
        missing_ok=[],
    ),
}


# ── Detection ─────────────────────────────────────────────────────────────────

@dataclass
class ClusterProfile:
    flavour:     ClusterFlavour
    meta:        FlavourMeta
    signals:     list[str] = field(default_factory=list)   # which signals fired
    confidence:  float = 0.0                               # 0-1 detection confidence

    @property
    def display(self) -> str:
        return self.meta.display

    @property
    def is_managed(self) -> bool:
        return self.meta.managed

    @property
    def is_local_dev(self) -> bool:
        return self.flavour in {
            ClusterFlavour.KIND, ClusterFlavour.DOCKER_DESKTOP,
            ClusterFlavour.MINIKUBE, ClusterFlavour.K3S,
            ClusterFlavour.K3D, ClusterFlavour.MICROK8S,
            ClusterFlavour.RANCHER_DESKTOP,
        }

    def command_expected(self, kubectl_key: str) -> bool:
        """Return True if this command is expected to succeed on this cluster type."""
        return kubectl_key not in self.meta.missing_ok

    def unknown_risk_for(self, kubectl_key: str, default_message: str) -> str | None:
        """
        Return an unknown risk message for a missing kubectl command, or None
        if this cluster type makes the absence expected and non-risky.
        """
        if kubectl_key in self.meta.missing_ok:
            return None    # expected to be missing — not a risk
        return default_message

    def prompt_context(self) -> str:
        """Return a block injected into the LLM prompt explaining this cluster."""
        lines = [
            "### Cluster Type",
            f"  Detected : {self.display}",
            f"  Managed  : {'YES — cloud provider controls the control plane' if self.is_managed else 'NO — self-managed'}",
            f"  Upgrade  : {self.meta.upgrade_mechanism}",
            f"  Signals  : {', '.join(self.signals[:4]) if self.signals else 'none (low confidence)'}",
            f"  Detection confidence : {self.confidence:.0%}",
            "",
            "### Cluster-Type Upgrade Notes",
            self.meta.upgrade_notes,
        ]
        if self.meta.managed_components:
            lines += [
                "",
                "### Provider-Managed Components",
                "The following are managed by the cloud/platform provider.",
                "Do NOT flag them as risks unless the provider's add-on version is incompatible:",
                "  " + ", ".join(self.meta.managed_components),
            ]
        if self.meta.missing_ok:
            lines += [
                "",
                "### Expected Missing Data",
                "The following kubectl commands are expected to fail on this cluster type.",
                "Treat their absence as EXPECTED, not as an unknown risk:",
                "  " + ", ".join(self.meta.missing_ok),
            ]
        return "\n".join(lines)


def detect_cluster(cluster_data: dict) -> ClusterProfile:
    """
    Detect cluster type from combined kubectl stdout.
    Returns the best matching ClusterProfile.
    """
    all_stdout = " ".join(
        v.get("stdout", "") for v in cluster_data.values()
    ).lower()

    best_flavour  = ClusterFlavour.GENERIC
    best_signals: list[str] = []
    best_score    = 0

    for flavour, signals in _DETECTION_RULES:
        matched = [s for s in signals if s.lower() in all_stdout]
        if matched:
            # Score = number of matching signals (more = more confident)
            score = len(matched)
            if score > best_score:
                best_score   = score
                best_flavour = flavour
                best_signals = matched

    confidence = min(1.0, best_score / 3)   # 3+ signals → 100%
    meta = _FLAVOUR_META.get(best_flavour, _FLAVOUR_META[ClusterFlavour.GENERIC])

    return ClusterProfile(
        flavour=best_flavour,
        meta=meta,
        signals=best_signals,
        confidence=confidence,
    )
