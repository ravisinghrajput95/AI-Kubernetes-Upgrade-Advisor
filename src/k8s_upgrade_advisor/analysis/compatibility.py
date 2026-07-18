"""Component ↔ Kubernetes compatibility engine.

Static support matrices for the components the platform tracks. Each entry
maps a component release line to the Kubernetes minors it supports, sourced
from upstream support-matrix docs. The matrices are intentionally
conservative: when the detected version or the mapping is missing, the
result is UNKNOWN — never a guess. The RAG layer supplies document evidence
for the LLM to reference; this table supplies the verdicts.

Maintenance note: matrices are data, reviewed on each Kubernetes release.
`min_component_for_k8s` is the primary lookup: "to run k8s X you need at
least component Y".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    CompatibilityEntry,
    CompatibilityStatus,
    DetectedComponent,
    Evidence,
    Finding,
    FindingCategory,
    FindingOrigin,
    KubeVersion,
    Severity,
)


@dataclass(frozen=True)
class SupportMatrix:
    component: str
    kind: str
    # k8s minor → minimum component version that supports it
    min_component_for_k8s: dict[str, str]
    recommended: dict[str, str] | None = None
    notes: str = ""
    # "published": row mirrors an upstream support matrix (source_url).
    # "inferred": upstream publishes no per-minor minimums; row is a
    # conservative approximation — findings are downgraded and never gate.
    confidence: str = "published"
    source_url: str = ""


MATRICES: dict[str, SupportMatrix] = {
    "cert-manager": SupportMatrix(
        "cert-manager",
        "operator",
        {
            "1.25": "1.10",
            "1.26": "1.11",
            "1.27": "1.12",
            "1.28": "1.13",
            "1.29": "1.14",
            "1.30": "1.15",
            "1.31": "1.16",
            "1.32": "1.16",
            "1.33": "1.17",
        },
        notes="Webhook-based; must be compatible *before* the control plane hop.",
        source_url="https://cert-manager.io/docs/releases/",
    ),
    "ingress-nginx": SupportMatrix(
        "ingress-nginx",
        "ingress",
        {
            "1.25": "1.5.1",
            "1.26": "1.6.4",
            "1.27": "1.8.0",
            "1.28": "1.9.0",
            "1.29": "1.10.0",
            "1.30": "1.10.1",
            "1.31": "1.11.2",
            "1.32": "1.12.0",
            "1.33": "1.12.1",
        },
        source_url="https://github.com/kubernetes/ingress-nginx#supported-versions-table",
    ),
    "istio": SupportMatrix(
        "istio",
        "mesh",
        {
            "1.25": "1.16",
            "1.26": "1.17",
            "1.27": "1.18",
            "1.28": "1.19",
            "1.29": "1.20",
            "1.30": "1.21",
            "1.31": "1.23",
            "1.32": "1.24",
            "1.33": "1.25",
        },
        notes="Istio supports ~4 k8s minors per release; control plane first, then data-plane rollout.",
        source_url="https://istio.io/latest/docs/releases/supported-releases/",
    ),
    "cilium": SupportMatrix(
        "cilium",
        "cni",
        {
            "1.25": "1.12",
            "1.26": "1.13",
            "1.27": "1.13",
            "1.28": "1.14",
            "1.29": "1.15",
            "1.30": "1.15",
            "1.31": "1.16",
            "1.32": "1.17",
            "1.33": "1.17",
        },
        notes="CNI incompatibility surfaces as node-level networking failure — treat as gating.",
        source_url="https://docs.cilium.io/en/stable/network/kubernetes/compatibility/",
    ),
    "calico": SupportMatrix(
        "calico",
        "cni",
        {
            "1.25": "3.24",
            "1.26": "3.25",
            "1.27": "3.26",
            "1.28": "3.26",
            "1.29": "3.27",
            "1.30": "3.28",
            "1.31": "3.29",
            "1.32": "3.29",
            "1.33": "3.30",
        },
        source_url="https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements",
    ),
    "karpenter": SupportMatrix(
        "karpenter",
        "autoscaler",
        {
            "1.25": "0.28",
            "1.26": "0.28",
            "1.27": "0.29",
            "1.28": "0.31",
            "1.29": "0.34",
            "1.30": "0.37",
            "1.31": "1.0",
            "1.32": "1.2",
            "1.33": "1.4",
        },
        notes="Karpenter v1 API (NodePool/NodeClaim) required from 0.32+; v1alpha5 Provisioners must be migrated.",
        source_url="https://karpenter.sh/docs/upgrading/compatibility/",
    ),
    "cluster-autoscaler": SupportMatrix(
        "cluster-autoscaler",
        "autoscaler",
        {
            # CA versions in lockstep with k8s minors
            "1.25": "1.25",
            "1.26": "1.26",
            "1.27": "1.27",
            "1.28": "1.28",
            "1.29": "1.29",
            "1.30": "1.30",
            "1.31": "1.31",
            "1.32": "1.32",
            "1.33": "1.33",
        },
        notes="Cluster Autoscaler minor must match the cluster minor exactly (upstream guidance).",
        source_url="https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler#releases",
    ),
    "argocd": SupportMatrix(
        "argocd",
        "gitops",
        {
            "1.25": "2.6",
            "1.26": "2.7",
            "1.27": "2.8",
            "1.28": "2.9",
            "1.29": "2.10",
            "1.30": "2.11",
            "1.31": "2.12",
            "1.32": "2.13",
            "1.33": "3.0",
        },
        confidence="inferred",
    ),
    "flux": SupportMatrix(
        "flux",
        "gitops",
        {
            "1.25": "0.38",
            "1.26": "2.0",
            "1.27": "2.0",
            "1.28": "2.1",
            "1.29": "2.2",
            "1.30": "2.3",
            "1.31": "2.4",
            "1.32": "2.4",
            "1.33": "2.5",
        },
        confidence="inferred",
    ),
    "keda": SupportMatrix(
        "keda",
        "autoscaler",
        {
            "1.25": "2.9",
            "1.26": "2.10",
            "1.27": "2.11",
            "1.28": "2.12",
            "1.29": "2.13",
            "1.30": "2.14",
            "1.31": "2.15",
            "1.32": "2.16",
            "1.33": "2.17",
        },
        confidence="inferred",
    ),
    "ebs-csi": SupportMatrix(
        "ebs-csi",
        "csi",
        {
            "1.25": "1.13",
            "1.26": "1.15",
            "1.27": "1.18",
            "1.28": "1.22",
            "1.29": "1.26",
            "1.30": "1.30",
            "1.31": "1.34",
            "1.32": "1.38",
            "1.33": "1.42",
        },
        confidence="inferred",
        notes="EBS CSI supports broad k8s ranges; per-minor minimums here are conservative approximations.",
    ),
    "metrics-server": SupportMatrix(
        "metrics-server",
        "operator",
        {
            "1.25": "0.6",
            "1.26": "0.6",
            "1.27": "0.6",
            "1.28": "0.7",
            "1.29": "0.7",
            "1.30": "0.7",
            "1.31": "0.7",
            "1.32": "0.8",
            "1.33": "0.8",
        },
        confidence="inferred",
    ),
}


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in v.lstrip("v").split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _at_least(current: str, minimum: str) -> bool:
    return _version_tuple(current) >= _version_tuple(minimum)


def evaluate_component(component: DetectedComponent, target: KubeVersion) -> CompatibilityEntry:
    matrix = MATRICES.get(component.key)
    entry = CompatibilityEntry(
        component=component.display_name,
        current_version=component.version,
    )
    if matrix is None:
        entry.kind = "operator"
        entry.notes = "No static support matrix tracked; verify against upstream docs."
        return entry

    entry.kind = matrix.kind
    minimum = matrix.min_component_for_k8s.get(target.minor_str)
    if minimum is None:
        entry.notes = f"No support-matrix data for Kubernetes {target.minor_str}; verify upstream."
        return entry

    entry.minimum_version = minimum
    inferred = matrix.confidence == "inferred"
    if inferred:
        entry.notes = (
            "(inferred matrix — upstream publishes no per-minor minimums; verify) " + matrix.notes
        ).strip()
    if component.version is None:
        entry.status = CompatibilityStatus.UNKNOWN
        entry.notes = (
            f"Detected via {', '.join(component.signals[:2])} but version could not be "
            f"resolved; requires >= {minimum} for Kubernetes {target.minor_str}."
        )
        return entry

    prefix = "(inferred matrix — verify upstream) " if inferred else ""
    if _at_least(component.version, minimum):
        entry.status = CompatibilityStatus.COMPATIBLE
        entry.notes = (prefix + matrix.notes).strip()
    else:
        entry.status = CompatibilityStatus.UPGRADE_REQUIRED
        entry.notes = (
            f"{prefix}Installed {component.version} < required {minimum} for "
            f"Kubernetes {target.minor_str}. {matrix.notes}".strip()
        )
    return entry


_GATING_KINDS = {"cni", "csi"}


def compatibility_findings(
    components: list[DetectedComponent], target: KubeVersion
) -> tuple[list[CompatibilityEntry], list[Finding]]:
    """Evaluate every detected component; emit findings for the ones that
    need action. CNI/CSI incompatibilities are blocking — nodes lose
    networking/storage, which is not a 'caution'."""
    entries: list[CompatibilityEntry] = []
    findings: list[Finding] = []
    category_by_kind = {
        "cni": FindingCategory.CNI_COMPAT,
        "csi": FindingCategory.CSI_COMPAT,
        "mesh": FindingCategory.SERVICE_MESH,
        "gitops": FindingCategory.GITOPS,
        "autoscaler": FindingCategory.AUTOSCALER,
    }

    for component in components:
        entry = evaluate_component(component, target)
        entries.append(entry)

        if entry.status is CompatibilityStatus.UPGRADE_REQUIRED:
            matrix = MATRICES.get(component.key)
            inferred = matrix is not None and matrix.confidence == "inferred"
            gating = entry.kind in _GATING_KINDS and not inferred
            findings.append(
                Finding(
                    id=f"compat-{component.key}",
                    title=f"{component.display_name} {component.version} does not support "
                    f"Kubernetes {target.minor_str}",
                    category=category_by_kind.get(entry.kind, FindingCategory.OPERATOR_COMPAT),
                    severity=Severity.CRITICAL
                    if gating
                    else (Severity.MEDIUM if inferred else Severity.HIGH),
                    origin=FindingOrigin.DETERMINISTIC,
                    description=entry.notes,
                    remediation=(
                        f"Upgrade {component.display_name} to >= {entry.minimum_version} "
                        f"*before* the control plane reaches {target.minor_str}."
                    ),
                    blocking=gating,
                    affected_objects=[component.namespace or component.key],
                    evidence=[
                        Evidence(
                            kind="cluster-data",
                            detail=f"Version {component.version} resolved from "
                            f"{component.version_source} ({'; '.join(component.signals[:2])}).",
                        ),
                        Evidence(
                            kind="static-table",
                            detail=f"Support matrix: Kubernetes {target.minor_str} requires "
                            f">= {entry.minimum_version}.",
                        ),
                    ],
                )
            )
        elif entry.status is CompatibilityStatus.UNKNOWN and entry.minimum_version:
            findings.append(
                Finding(
                    id=f"compat-unknown-{component.key}",
                    title=f"{component.display_name} version unresolved — compatibility unverified",
                    category=category_by_kind.get(entry.kind, FindingCategory.OPERATOR_COMPAT),
                    severity=Severity.MEDIUM,
                    origin=FindingOrigin.DETERMINISTIC,
                    description=entry.notes,
                    remediation=(
                        f"Determine the installed version (helm list, image tags) and confirm "
                        f">= {entry.minimum_version} before upgrading."
                    ),
                    evidence=[
                        Evidence(
                            kind="cluster-data",
                            detail=f"Detected via {'; '.join(component.signals[:2])}, no version signal.",
                        ),
                    ],
                )
            )
    return entries, findings
