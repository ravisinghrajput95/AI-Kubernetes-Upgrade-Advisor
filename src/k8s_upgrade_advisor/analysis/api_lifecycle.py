"""Kubernetes API lifecycle engine.

The list of APIs removed at each minor version is *finite and known* — it
must never come from an LLM or a similarity search. This module owns that
table and turns cluster evidence into deterministic findings. The LLM's job
is to explain them, not discover them.

Sources: kubernetes.io deprecation guide + release notes. Table covers the
removals that affect real workloads from 1.16 through 1.33.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import (
    ClusterSnapshot,
    Evidence,
    Finding,
    FindingCategory,
    FindingOrigin,
    KubeVersion,
    Severity,
)

# The newest Kubernetes minor the static tables below have been reviewed
# against. Assessments targeting anything newer get an explicit
# "beyond knowledge horizon" finding and a readiness cap — an empty findings
# list must never masquerade as safety. Bump BOTH constants when reviewing
# tables for a new release (see docs/development.md release checklist).
KNOWLEDGE_HORIZON = "1.33"
TABLES_LAST_REVIEWED = "2026-07-18"

HORIZON_FINDING_ID = "knowledge-horizon-exceeded"


@dataclass(frozen=True)
class APIRemoval:
    group_version: str
    kinds: tuple[str, ...]
    removed_in: str  # first minor where the GV is gone
    deprecated_in: str
    replacement: str
    notes: str = ""


API_REMOVALS: tuple[APIRemoval, ...] = (
    # ── 1.16 ────────────────────────────────────────────────────────────
    APIRemoval(
        "extensions/v1beta1", ("Deployment", "DaemonSet", "ReplicaSet"), "1.16", "1.9", "apps/v1"
    ),
    APIRemoval("apps/v1beta1", ("Deployment", "StatefulSet"), "1.16", "1.9", "apps/v1"),
    APIRemoval(
        "apps/v1beta2",
        ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"),
        "1.16",
        "1.9",
        "apps/v1",
    ),
    # ── 1.22 ────────────────────────────────────────────────────────────
    APIRemoval(
        "admissionregistration.k8s.io/v1beta1",
        ("ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"),
        "1.22",
        "1.16",
        "admissionregistration.k8s.io/v1",
    ),
    APIRemoval(
        "apiextensions.k8s.io/v1beta1",
        ("CustomResourceDefinition",),
        "1.22",
        "1.16",
        "apiextensions.k8s.io/v1",
        "v1 requires structural schemas; legacy CRDs may need schema work",
    ),
    APIRemoval(
        "certificates.k8s.io/v1beta1",
        ("CertificateSigningRequest",),
        "1.22",
        "1.19",
        "certificates.k8s.io/v1",
    ),
    APIRemoval(
        "extensions/v1beta1",
        ("Ingress",),
        "1.22",
        "1.14",
        "networking.k8s.io/v1",
        "v1 Ingress uses pathType and a restructured backend field",
    ),
    APIRemoval(
        "networking.k8s.io/v1beta1",
        ("Ingress", "IngressClass"),
        "1.22",
        "1.19",
        "networking.k8s.io/v1",
    ),
    APIRemoval(
        "rbac.authorization.k8s.io/v1beta1",
        ("ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"),
        "1.22",
        "1.17",
        "rbac.authorization.k8s.io/v1",
    ),
    APIRemoval(
        "scheduling.k8s.io/v1beta1", ("PriorityClass",), "1.22", "1.14", "scheduling.k8s.io/v1"
    ),
    APIRemoval(
        "storage.k8s.io/v1beta1",
        ("CSIDriver", "CSINode", "StorageClass", "VolumeAttachment"),
        "1.22",
        "1.19",
        "storage.k8s.io/v1",
    ),
    APIRemoval("coordination.k8s.io/v1beta1", ("Lease",), "1.22", "1.19", "coordination.k8s.io/v1"),
    # ── 1.25 ────────────────────────────────────────────────────────────
    APIRemoval(
        "policy/v1beta1",
        ("PodSecurityPolicy",),
        "1.25",
        "1.21",
        "Pod Security Admission (pod-security.kubernetes.io labels)",
        "The PSP feature is removed entirely, not just the API version",
    ),
    APIRemoval("policy/v1beta1", ("PodDisruptionBudget",), "1.25", "1.21", "policy/v1"),
    APIRemoval("batch/v1beta1", ("CronJob",), "1.25", "1.21", "batch/v1"),
    APIRemoval(
        "autoscaling/v2beta1", ("HorizontalPodAutoscaler",), "1.25", "1.22", "autoscaling/v2"
    ),
    APIRemoval(
        "discovery.k8s.io/v1beta1", ("EndpointSlice",), "1.25", "1.21", "discovery.k8s.io/v1"
    ),
    APIRemoval("events.k8s.io/v1beta1", ("Event",), "1.25", "1.22", "events.k8s.io/v1"),
    APIRemoval("node.k8s.io/v1beta1", ("RuntimeClass",), "1.25", "1.22", "node.k8s.io/v1"),
    # ── 1.26 ────────────────────────────────────────────────────────────
    APIRemoval(
        "flowcontrol.apiserver.k8s.io/v1beta1",
        ("FlowSchema", "PriorityLevelConfiguration"),
        "1.26",
        "1.23",
        "flowcontrol.apiserver.k8s.io/v1beta3 (v1 from 1.29)",
    ),
    APIRemoval(
        "autoscaling/v2beta2", ("HorizontalPodAutoscaler",), "1.26", "1.23", "autoscaling/v2"
    ),
    # ── 1.27 ────────────────────────────────────────────────────────────
    APIRemoval(
        "storage.k8s.io/v1beta1", ("CSIStorageCapacity",), "1.27", "1.24", "storage.k8s.io/v1"
    ),
    # ── 1.29 ────────────────────────────────────────────────────────────
    APIRemoval(
        "flowcontrol.apiserver.k8s.io/v1beta2",
        ("FlowSchema", "PriorityLevelConfiguration"),
        "1.29",
        "1.26",
        "flowcontrol.apiserver.k8s.io/v1",
    ),
    # ── 1.32 ────────────────────────────────────────────────────────────
    APIRemoval(
        "flowcontrol.apiserver.k8s.io/v1beta3",
        ("FlowSchema", "PriorityLevelConfiguration"),
        "1.32",
        "1.29",
        "flowcontrol.apiserver.k8s.io/v1",
    ),
)


@dataclass(frozen=True)
class BehaviorChange:
    """Platform-level behaviour changes (KEP graduations/removals) that are
    not an API group/version disappearing but still break clusters."""

    id: str
    title: str
    effective_in: str
    severity: Severity
    description: str
    remediation: str
    detect: str = "always"  # always | docker_runtime | legacy_registry | in_tree_cloud
    kep: str = ""
    signals: tuple[str, ...] = field(default=())


BEHAVIOR_CHANGES: tuple[BehaviorChange, ...] = (
    BehaviorChange(
        id="dockershim-removal",
        title="dockershim removed — Docker Engine no longer a supported runtime",
        effective_in="1.24",
        severity=Severity.CRITICAL,
        description=(
            "kubelet 1.24+ cannot talk to Docker Engine via the built-in "
            "dockershim. Nodes reporting a docker:// runtime must migrate to "
            "containerd or CRI-O before their kubelets are upgraded."
        ),
        remediation="Migrate node runtime to containerd/CRI-O (or cri-dockerd if Docker is required).",
        detect="docker_runtime",
        kep="KEP-2221",
    ),
    BehaviorChange(
        id="psp-removal",
        title="PodSecurityPolicy feature removed",
        effective_in="1.25",
        severity=Severity.CRITICAL,
        description=(
            "PSP objects and the admission plugin are gone in 1.25. Workload "
            "admission controls silently disappear unless migrated to Pod "
            "Security Admission or a policy engine (Kyverno/Gatekeeper)."
        ),
        remediation="Migrate PSPs to Pod Security Admission namespace labels before upgrading past 1.24.",
        detect="psp_in_use",
        kep="KEP-2579",
    ),
    BehaviorChange(
        id="legacy-registry-freeze",
        title="k8s.gcr.io frozen — images must come from registry.k8s.io",
        effective_in="1.27",
        severity=Severity.MEDIUM,
        description=(
            "The legacy k8s.gcr.io registry is frozen; images referencing it "
            "will not receive new tags and may be garbage-collected."
        ),
        remediation="Repoint image references from k8s.gcr.io to registry.k8s.io.",
        detect="legacy_registry",
    ),
    BehaviorChange(
        id="kubelet-skew-n3",
        title="kubelet version skew widened to n-3",
        effective_in="1.28",
        severity=Severity.INFO,
        description=(
            "From 1.28 the control plane supports kubelets up to three minors "
            "older, enabling fewer node pool upgrade waves on long paths."
        ),
        remediation="",
        kep="KEP-3935",
    ),
    BehaviorChange(
        id="in-tree-cloud-provider-removal",
        title="in-tree cloud providers removed — external cloud-controller-manager required",
        effective_in="1.31",
        severity=Severity.HIGH,
        description=(
            "1.31 completes the removal of in-tree AWS/Azure/GCP/vSphere cloud "
            "providers. Self-managed clusters still using --cloud-provider=<name> "
            "flags must run the external cloud-controller-manager."
        ),
        remediation="Deploy the external cloud-controller-manager and CSI drivers before crossing 1.31.",
        detect="in_tree_cloud",
        kep="KEP-2395",
    ),
    BehaviorChange(
        id="cgroup-v1-maintenance",
        title="cgroup v1 support in maintenance mode",
        effective_in="1.31",
        severity=Severity.LOW,
        description=(
            "kubelet cgroup v1 support is feature-frozen from 1.31; distros "
            "and node images should be on cgroup v2."
        ),
        remediation="Verify node OS images use cgroup v2 (systemd unified hierarchy).",
    ),
)


# ── Detection ────────────────────────────────────────────────────────────────


def removals_in_range(source: KubeVersion, target: KubeVersion) -> list[APIRemoval]:
    """Removals that take effect strictly after source, at or before target."""
    hits: list[APIRemoval] = []
    for removal in API_REMOVALS:
        removed = KubeVersion.parse(removal.removed_in)
        if source < removed <= target:
            hits.append(removal)
    return hits


def _served_group_versions(snapshot: ClusterSnapshot) -> set[str]:
    return {line.strip() for line in snapshot.stdout("api_versions").splitlines() if line.strip()}


def detect_api_removal_findings(
    snapshot: ClusterSnapshot, source: KubeVersion, target: KubeVersion
) -> list[Finding]:
    served = _served_group_versions(snapshot)
    findings: list[Finding] = []

    for removal in removals_in_range(source, target):
        is_served = removal.group_version in served
        if not is_served:
            # Not served on the source cluster — nothing can be using it.
            continue

        psp_case = "PodSecurityPolicy" in removal.kinds
        psp_objects = snapshot.command("psp")
        psp_in_use = (
            psp_case and psp_objects.has_output and "No resources" not in psp_objects.stdout
        )

        kinds = ", ".join(removal.kinds)
        audit_cmds = [
            f"kubectl get {kind.lower()}.{removal.group_version.split('/')[0]} -A"
            for kind in removal.kinds[:3]
        ]
        evidence = [
            Evidence(
                kind="cluster-data",
                detail=f"API group version '{removal.group_version}' is served by this cluster "
                f"(kubectl api-versions) and is removed in Kubernetes {removal.removed_in}.",
            ),
            Evidence(
                kind="static-table",
                detail=f"Deprecated {removal.deprecated_in}, removed {removal.removed_in}. "
                f"Replacement: {removal.replacement}.",
            ),
        ]
        if psp_in_use:
            evidence.append(
                Evidence(
                    kind="cluster-data",
                    detail="PodSecurityPolicy objects exist in this cluster (kubectl get psp).",
                )
            )

        findings.append(
            Finding(
                id=f"removed-api-{removal.group_version.replace('/', '-').replace('.', '-')}"
                f"-{removal.kinds[0].lower()}",
                title=f"{kinds} ({removal.group_version}) removed in {removal.removed_in}",
                category=FindingCategory.REMOVED_API,
                severity=Severity.CRITICAL if psp_in_use else Severity.HIGH,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    f"The cluster still serves {removal.group_version}, which is removed in "
                    f"Kubernetes {removal.removed_in} (inside this upgrade path). Any manifest, "
                    f"Helm chart, controller, or stored object using this version fails after the "
                    f"hop to {removal.removed_in}. {removal.notes}".strip()
                ),
                remediation=(
                    f"Migrate to {removal.replacement}. Audit usage before upgrading: "
                    + "; ".join(audit_cmds)
                    + ". Tools like 'pluto detect-all-in-cluster' or 'kubent' find manifests "
                    "pinned to removed versions."
                ),
                blocking=psp_in_use,
                evidence=evidence,
                introduced_in=removal.deprecated_in,
                effective_in=removal.removed_in,
            )
        )
    return findings


def detect_behavior_findings(
    snapshot: ClusterSnapshot,
    source: KubeVersion,
    target: KubeVersion,
    node_runtimes: list[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    runtimes = node_runtimes or []
    deploy_images = snapshot.stdout("deployments") + snapshot.stdout("daemonsets")

    for change in BEHAVIOR_CHANGES:
        effective = KubeVersion.parse(change.effective_in)
        if not (source < effective <= target):
            continue

        triggered = False
        evidence: list[Evidence] = [
            Evidence(
                kind="static-table",
                detail=f"{change.title} — effective in Kubernetes {change.effective_in}"
                + (f" ({change.kep})" if change.kep else ""),
            )
        ]
        if change.detect == "always":
            triggered = True
        elif change.detect == "docker_runtime":
            docker_nodes = [r for r in runtimes if r.startswith("docker://")]
            if docker_nodes:
                triggered = True
                evidence.append(
                    Evidence(
                        kind="cluster-data",
                        detail=f"{len(docker_nodes)} node(s) report a docker:// container runtime.",
                    )
                )
        elif change.detect == "psp_in_use":
            psp = snapshot.command("psp")
            if psp.has_output and "No resources" not in psp.stdout:
                triggered = True
                evidence.append(
                    Evidence(
                        kind="cluster-data",
                        detail="PodSecurityPolicy objects exist (kubectl get psp).",
                    )
                )
        elif change.detect == "legacy_registry":
            if "k8s.gcr.io" in deploy_images:
                triggered = True
                evidence.append(
                    Evidence(
                        kind="cluster-data",
                        detail="Workload images reference the frozen k8s.gcr.io registry.",
                    )
                )
        elif (
            change.detect == "in_tree_cloud"
            # Managed clusters handle this server-side; only relevant when we
            # cannot prove an external CCM is running on a self-managed cluster.
            and "cloud-controller-manager" not in deploy_images
        ):
            triggered = True
            evidence.append(
                Evidence(
                    kind="cluster-data",
                    detail="No external cloud-controller-manager detected in workloads; "
                    "verify cloud provider integration mode before crossing 1.31.",
                )
            )

        if triggered:
            findings.append(
                Finding(
                    id=f"behavior-{change.id}",
                    title=change.title,
                    category=FindingCategory.KEP_IMPACT
                    if change.kep
                    else FindingCategory.BREAKING_CHANGE,
                    severity=change.severity,
                    origin=FindingOrigin.DETERMINISTIC,
                    description=change.description,
                    remediation=change.remediation,
                    blocking=change.severity is Severity.CRITICAL,
                    evidence=evidence,
                    effective_in=change.effective_in,
                )
            )
    return findings


def horizon_findings(source: KubeVersion, target: KubeVersion) -> list[Finding]:
    """Honesty guard: when the target minor is newer than the reviewed
    tables, say so loudly. The risk engine caps readiness on this finding —
    'no removals found' beyond the horizon means 'not looked', not 'safe'."""
    horizon = KubeVersion.parse(KNOWLEDGE_HORIZON)
    if target <= horizon:
        return []
    return [
        Finding(
            id=HORIZON_FINDING_ID,
            title=f"Target {target.minor_str} is beyond the reviewed knowledge horizon "
            f"({KNOWLEDGE_HORIZON})",
            category=FindingCategory.UPGRADE_PATH,
            severity=Severity.HIGH,
            origin=FindingOrigin.DETERMINISTIC,
            description=(
                f"The static API-lifecycle and compatibility tables in this build were last "
                f"reviewed against Kubernetes {KNOWLEDGE_HORIZON} (on {TABLES_LAST_REVIEWED}). "
                f"API removals and component support for "
                f"{', '.join(v.minor_str for v in KubeVersion.parse(KNOWLEDGE_HORIZON).minors_until(target))} "
                "are NOT covered — an empty findings list for those minors means unexamined, "
                "not safe. Readiness is capped accordingly."
            ),
            remediation=(
                "Upgrade k8s-upgrade-advisor to a build whose tables cover the target minor, "
                "and cross-check the official deprecation guide and CHANGELOG for versions "
                "beyond the horizon."
            ),
            evidence=[
                Evidence(
                    kind="static-table",
                    detail=f"Table coverage ends at {KNOWLEDGE_HORIZON}; "
                    f"target is {target.minor_str}.",
                )
            ],
        )
    ]
