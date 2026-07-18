"""Cluster profiling: which distribution is this, what does it look like,
and how does it upgrade?

Flavour detection uses layered signals, strongest first:
  1. apiserver gitVersion suffixes (-eks-, -gke., +rke2, +k3s)
  2. node providerID prefixes (aws:// gce:// azure://)
  3. distribution-specific API groups (route.openshift.io, management.cattle.io)
  4. node names / labels (kind-, minikube, docker-desktop)

The resulting profile drives which upgrade mechanism the planner emits and
which missing evidence is expected rather than a risk.
"""

from __future__ import annotations

import json
import re

from ..models import (
    ClusterFlavour,
    ClusterProfileSummary,
    ClusterSnapshot,
    NodeInfo,
    WorkloadCounts,
)
from ..observability import get_logger

log = get_logger(__name__)

_POOL_LABELS = (
    "eks.amazonaws.com/nodegroup",
    "cloud.google.com/gke-nodepool",
    "kubernetes.azure.com/agentpool",
    "karpenter.sh/nodepool",
)

UPGRADE_MECHANISM: dict[ClusterFlavour, str] = {
    ClusterFlavour.EKS: "Managed control plane via EKS API (eksctl/console/IaC); "
    "managed node groups or Karpenter node rotation",
    ClusterFlavour.GKE: "Managed control plane via GKE API (gcloud container clusters upgrade); "
    "node pools upgrade with surge settings; release channels gate versions",
    ClusterFlavour.AKS: "Managed control plane via AKS API (az aks upgrade); "
    "node pools upgraded per-pool with max-surge",
    ClusterFlavour.OPENSHIFT: "Cluster Version Operator (oc adm upgrade); OpenShift versions "
    "map to specific Kubernetes minors — upgrade OCP, not k8s directly",
    ClusterFlavour.RANCHER_RKE2: "rke2 config channel/version pinning; sequential server then agent restarts",
    ClusterFlavour.RANCHER_K3S: "k3s binary/channel upgrade or Rancher system-upgrade-controller",
    ClusterFlavour.KUBEADM: "kubeadm upgrade plan/apply on control plane nodes, then kubelet+drain per node",
    ClusterFlavour.KIND: "Recreate cluster with a newer node image (kind clusters are disposable)",
    ClusterFlavour.MINIKUBE: "minikube start --kubernetes-version=<target> (recreate)",
    ClusterFlavour.DOCKER_DESKTOP: "Bundled with Docker Desktop releases; upgrade Docker Desktop itself",
    ClusterFlavour.UNKNOWN: "Undetermined — verify distribution before planning",
}

PROVIDER_MANAGED: dict[ClusterFlavour, list[str]] = {
    ClusterFlavour.EKS: [
        "etcd",
        "kube-apiserver",
        "kube-controller-manager",
        "kube-scheduler",
        "coredns (addon)",
        "kube-proxy (addon)",
        "vpc-cni (addon)",
    ],
    ClusterFlavour.GKE: ["etcd", "control plane", "coredns/kube-dns", "konnectivity", "gce-pd CSI"],
    ClusterFlavour.AKS: ["etcd", "control plane", "coredns", "azure CNI", "azure-disk CSI"],
    ClusterFlavour.OPENSHIFT: ["etcd", "control plane operators", "ingress operator", "SDN/OVN"],
}


def detect_flavour(snapshot: ClusterSnapshot) -> tuple[ClusterFlavour, list[str]]:
    evidence: list[str] = []
    version_out = snapshot.stdout("version")
    git_version = ""
    try:
        git_version = json.loads(version_out).get("serverVersion", {}).get("gitVersion", "")
    except (json.JSONDecodeError, AttributeError):
        m = re.search(r'"gitVersion":\s*"([^"]+)"', version_out)
        git_version = m.group(1) if m else ""

    if "-eks-" in git_version:
        return ClusterFlavour.EKS, [f"apiserver gitVersion '{git_version}' has EKS suffix"]
    if "-gke." in git_version:
        return ClusterFlavour.GKE, [f"apiserver gitVersion '{git_version}' has GKE suffix"]
    if "+rke2" in git_version:
        return ClusterFlavour.RANCHER_RKE2, [
            f"apiserver gitVersion '{git_version}' has rke2 suffix"
        ]
    if "+k3s" in git_version:
        return ClusterFlavour.RANCHER_K3S, [f"apiserver gitVersion '{git_version}' has k3s suffix"]

    api_resources = snapshot.stdout("api_resources") + snapshot.stdout("crds")
    if (
        "route.openshift.io" in api_resources
        or "clusterversions.config.openshift.io" in api_resources
    ):
        return ClusterFlavour.OPENSHIFT, ["OpenShift API groups present (route.openshift.io)"]
    if "management.cattle.io" in api_resources:
        evidence.append("Rancher management API groups present")

    nodes_json = snapshot.stdout("nodes_json")
    provider_ids = re.findall(r'"providerID":\s*"([^"]+)"', nodes_json)
    if provider_ids:
        prefix = provider_ids[0].split(":", 1)[0]
        if prefix == "azure":
            return ClusterFlavour.AKS, [f"node providerID prefix '{prefix}://'"]
        if prefix == "gce":
            return ClusterFlavour.GKE, [f"node providerID prefix '{prefix}://'"]
        if prefix == "aws":
            evidence.append("node providerID aws:// (EKS or self-managed on AWS)")
            return ClusterFlavour.EKS, evidence

    nodes_out = snapshot.stdout("nodes")
    if "docker-desktop" in nodes_out:
        return ClusterFlavour.DOCKER_DESKTOP, ["node named docker-desktop"]
    if re.search(r"\bkind-", nodes_out) or "kindest" in nodes_json:
        return ClusterFlavour.KIND, ["kind node naming/image detected"]
    if "minikube" in nodes_out:
        return ClusterFlavour.MINIKUBE, ["node named minikube"]

    if "node-role.kubernetes.io/control-plane" in nodes_json or "kubeadm" in nodes_json:
        evidence.append("self-managed control-plane node labels present")
        return ClusterFlavour.KUBEADM, evidence

    return ClusterFlavour.UNKNOWN, evidence or ["no distribution signal matched"]


def parse_nodes(snapshot: ClusterSnapshot) -> list[NodeInfo]:
    raw = snapshot.stdout("nodes_json")
    if not raw:
        return []
    try:
        items = json.loads(raw).get("items", [])
    except json.JSONDecodeError:
        return []

    nodes: list[NodeInfo] = []
    for item in items:
        meta = item.get("metadata", {})
        labels = meta.get("labels", {})
        status = item.get("status", {})
        info = status.get("nodeInfo", {})
        roles = [
            label.split("/", 1)[1]
            for label in labels
            if label.startswith("node-role.kubernetes.io/")
        ]
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in status.get("conditions", [])
        )
        pool = next((labels[label] for label in _POOL_LABELS if label in labels), None)
        nodes.append(
            NodeInfo(
                name=meta.get("name", ""),
                kubelet_version=info.get("kubeletVersion", ""),
                os_image=info.get("osImage", ""),
                container_runtime=info.get("containerRuntimeVersion", ""),
                roles=roles or ["worker"],
                ready=ready,
                node_pool=pool,
            )
        )
    return nodes


def _count_rows(output: str) -> int:
    lines = [line for line in output.splitlines() if line.strip()]
    return max(len(lines) - 1, 0)  # minus header


def parse_workloads(snapshot: ClusterSnapshot) -> WorkloadCounts:
    return WorkloadCounts(
        deployments=_count_rows(snapshot.stdout("deployments")),
        statefulsets=_count_rows(snapshot.stdout("statefulsets")),
        daemonsets=_count_rows(snapshot.stdout("daemonsets")),
        jobs=_count_rows(snapshot.stdout("jobs")),
        cronjobs=_count_rows(snapshot.stdout("cronjobs")),
    )


def current_server_version(snapshot: ClusterSnapshot) -> str:
    out = snapshot.stdout("version")
    try:
        return json.loads(out).get("serverVersion", {}).get("gitVersion", "")
    except json.JSONDecodeError:
        m = re.search(r'"gitVersion":\s*"([^"]+)"', out)
        return m.group(1) if m else ""


def build_profile(snapshot: ClusterSnapshot) -> ClusterProfileSummary:
    flavour, evidence = detect_flavour(snapshot)
    nodes = parse_nodes(snapshot)
    profile = ClusterProfileSummary(
        flavour=flavour,
        flavour_evidence=evidence,
        current_version=current_server_version(snapshot),
        node_count=len(nodes),
        nodes=nodes,
        workloads=parse_workloads(snapshot),
        upgrade_mechanism=UPGRADE_MECHANISM[flavour],
        provider_managed=PROVIDER_MANAGED.get(flavour, []),
    )
    log.info(
        "cluster_profiled",
        flavour=flavour.value,
        nodes=profile.node_count,
        workloads=profile.workloads.total,
    )
    return profile
