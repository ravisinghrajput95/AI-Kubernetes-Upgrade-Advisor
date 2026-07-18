"""Cluster snapshot models — the input side of the platform.

A :class:`ClusterSnapshot` is a point-in-time, serializable capture of
everything the analyzers need. It can come from a live cluster (kubectl/helm
collectors), a saved JSON file (air-gapped assessment), or the API upload
endpoint. Analyzers never shell out themselves; they only read snapshots,
which keeps them pure and unit-testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    """One collector command execution. ``ok`` is based on the process
    return code — stderr warnings do not fail a command."""

    args: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration_ms: int = 0
    error: str | None = None  # spawn-level failure (binary missing, timeout)

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def has_output(self) -> bool:
        return self.ok and bool(self.stdout.strip())


class HelmRelease(BaseModel):
    name: str
    namespace: str
    chart: str = ""  # e.g. "cert-manager-v1.14.4"
    chart_name: str = ""  # e.g. "cert-manager"
    chart_version: str = ""  # e.g. "v1.14.4"
    app_version: str = ""
    status: str = ""
    revision: int | None = None


class ClusterFlavour(str, Enum):
    EKS = "eks"
    GKE = "gke"
    AKS = "aks"
    OPENSHIFT = "openshift"
    RANCHER_RKE2 = "rke2"
    RANCHER_K3S = "k3s"
    KUBEADM = "kubeadm"
    KIND = "kind"
    MINIKUBE = "minikube"
    DOCKER_DESKTOP = "docker-desktop"
    UNKNOWN = "unknown"

    @property
    def is_managed(self) -> bool:
        return self in {self.EKS, self.GKE, self.AKS}

    @property
    def is_local_dev(self) -> bool:
        return self in {self.KIND, self.MINIKUBE, self.DOCKER_DESKTOP}


class NodeInfo(BaseModel):
    name: str
    kubelet_version: str = ""
    os_image: str = ""
    container_runtime: str = ""
    roles: list[str] = Field(default_factory=list)
    ready: bool = True
    node_pool: str | None = None  # cloud node group / pool label when detectable


class WorkloadCounts(BaseModel):
    deployments: int = 0
    statefulsets: int = 0
    daemonsets: int = 0
    jobs: int = 0
    cronjobs: int = 0

    @property
    def total(self) -> int:
        return self.deployments + self.statefulsets + self.daemonsets + self.jobs + self.cronjobs


class DetectedComponent(BaseModel):
    """An addon/operator found in the cluster, with the strongest version
    evidence available (helm chart > image tag > presence-only)."""

    key: str  # canonical id, e.g. "cert-manager"
    display_name: str
    version: str | None = None
    version_source: str = "unknown"  # helm | image | crd | presence
    namespace: str | None = None
    signals: list[str] = Field(default_factory=list)


class ClusterSnapshot(BaseModel):
    schema_version: int = 1
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = "live"  # live | file | api-upload
    context: str | None = None

    kubectl: dict[str, CommandResult] = Field(default_factory=dict)
    helm_releases: list[HelmRelease] = Field(default_factory=list)
    helm_available: bool = False
    # Rendered manifests per release ("namespace/name" → yaml), used to scan
    # for templates still emitting removed API versions.
    helm_manifests: dict[str, str] = Field(default_factory=dict)

    def command(self, key: str) -> CommandResult:
        return self.kubectl.get(key, CommandResult(error="not collected"))

    def stdout(self, key: str) -> str:
        return self.command(key).stdout

    @property
    def commands_ok(self) -> int:
        return sum(1 for r in self.kubectl.values() if r.ok)

    @property
    def commands_total(self) -> int:
        return len(self.kubectl)
