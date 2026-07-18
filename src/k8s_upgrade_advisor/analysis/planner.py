"""Deterministic upgrade planner.

Produces the *skeleton* of the upgrade plan — hop sequence, phase ordering,
distribution-specific commands, node pool strategy, and a downtime estimate
grounded in cluster facts (replica counts, PDBs, control plane type). The
LLM may refine wording and add context-specific steps, but phase ordering
and the command vocabulary come from here.

Canonical phase order per hop:
  preparation → control-plane → addons → node-pools → workloads → validation

Addons upgrade *after* the control plane hop but webhook-backed operators
must already be at a compatible version *before* it — the planner encodes
that asymmetry in the preparation phase.
"""

from __future__ import annotations

from ..models import (
    ClusterFlavour,
    ClusterProfileSummary,
    ClusterSnapshot,
    DowntimeEstimate,
    KubeVersion,
    UpgradePhase,
    UpgradePlan,
    UpgradeStep,
)

_CP_COMMANDS: dict[ClusterFlavour, list[str]] = {
    ClusterFlavour.EKS: [
        "eksctl upgrade cluster --name <cluster> --version {v} --approve",
        "aws eks update-cluster-version --name <cluster> --kubernetes-version {v}",
    ],
    ClusterFlavour.GKE: [
        "gcloud container clusters upgrade <cluster> --master --cluster-version {v}",
    ],
    ClusterFlavour.AKS: [
        "az aks upgrade --resource-group <rg> --name <cluster> --control-plane-only --kubernetes-version {v}",
    ],
    ClusterFlavour.KUBEADM: [
        "kubeadm upgrade plan v{v}",
        "kubeadm upgrade apply v{v}   # first control-plane node",
        "kubeadm upgrade node         # remaining control-plane nodes",
    ],
    ClusterFlavour.RANCHER_RKE2: [
        "Update RKE2 version/channel in config, restart rke2-server sequentially per server node",
    ],
    ClusterFlavour.RANCHER_K3S: [
        "Update k3s channel/version (or system-upgrade-controller Plan), servers before agents",
    ],
    ClusterFlavour.OPENSHIFT: [
        "oc adm upgrade --to <ocp-version-mapping-to-k8s-{v}>",
    ],
}

_NODE_COMMANDS: dict[ClusterFlavour, list[str]] = {
    ClusterFlavour.EKS: [
        "eksctl upgrade nodegroup --cluster <cluster> --name <ng> --kubernetes-version {v}",
        "# Karpenter nodes: drift replacement picks up the new AMI automatically",
    ],
    ClusterFlavour.GKE: [
        "gcloud container clusters upgrade <cluster> --node-pool <pool> --cluster-version {v}",
    ],
    ClusterFlavour.AKS: [
        "az aks nodepool upgrade --resource-group <rg> --cluster-name <cluster> --name <pool> --kubernetes-version {v}",
    ],
    ClusterFlavour.KUBEADM: [
        "kubectl drain <node> --ignore-daemonsets --delete-emptydir-data",
        "apt-get install -y kubelet={v}.x-* kubeadm={v}.x-*  # or distro equivalent",
        "systemctl restart kubelet && kubectl uncordon <node>",
    ],
}


def build_plan(
    profile: ClusterProfileSummary,
    snapshot: ClusterSnapshot,
    source: KubeVersion,
    target: KubeVersion,
) -> UpgradePlan:
    hops = source.minors_until(target)
    flavour = profile.flavour
    order = 0
    steps: list[UpgradeStep] = []

    def step(
        phase: UpgradePhase,
        title: str,
        description: str = "",
        commands: list[str] | None = None,
        minutes: int | None = None,
        disruption: str = "none",
    ) -> None:
        nonlocal order
        order += 1
        steps.append(
            UpgradeStep(
                order=order,
                phase=phase,
                title=title,
                description=description,
                commands=commands or [],
                estimated_minutes=minutes,
                disruption=disruption,
            )
        )

    # ── Preparation (once, before the first hop) ─────────────────────────
    step(
        UpgradePhase.PREPARATION,
        "Snapshot cluster state and back up",
        "etcd snapshot (self-managed) or velero backup; export current manifests for diffing.",
        [
            "velero backup create pre-upgrade-$(date +%Y%m%d)",
            "kubectl get all -A -o yaml > pre-upgrade-state.yaml",
        ],
        minutes=15,
    )
    step(
        UpgradePhase.PREPARATION,
        "Remediate removed-API usage flagged in findings",
        "Every finding in the removed-api category must be resolved before the first hop.",
        ["pluto detect-all-in-cluster", "kubent"],
        minutes=60,
    )
    step(
        UpgradePhase.PREPARATION,
        "Upgrade webhook-backed operators to target-compatible versions",
        "cert-manager, policy engines, and other admission webhooks must support the "
        "target version *before* the control plane moves — a failing webhook can block "
        "all admissions cluster-wide.",
        minutes=30,
    )
    if profile.workloads.total > 0:
        step(
            UpgradePhase.PREPARATION,
            "Verify PodDisruptionBudgets allow node drains",
            "PDBs with maxUnavailable: 0 or single-replica workloads without PDBs will "
            "stall or take downtime during node rotation.",
            ["kubectl get pdb -A"],
            minutes=15,
        )

    # ── Per-hop phases ───────────────────────────────────────────────────
    cp_minutes = 10 if flavour.is_managed else 30
    for hop in hops:
        v = hop.minor_str
        step(
            UpgradePhase.CONTROL_PLANE,
            f"Upgrade control plane to {v}",
            f"Distribution mechanism: {profile.upgrade_mechanism}",
            [c.format(v=v) for c in _CP_COMMANDS.get(flavour, ["<distribution-specific>"])],
            minutes=cp_minutes,
            disruption="none" if flavour.is_managed else "control-plane-api",
        )
        step(
            UpgradePhase.ADDONS,
            f"Upgrade cluster addons for {v}",
            "CoreDNS, kube-proxy, CNI, CSI drivers, metrics-server to versions matching "
            f"{v}; managed addons via provider APIs." + _addon_requirements(profile, hop),
            minutes=20,
        )
        step(
            UpgradePhase.NODE_POOLS,
            f"Roll node pools to {v}",
            _node_pool_description(profile),
            [c.format(v=v) for c in _NODE_COMMANDS.get(flavour, ["<distribution-specific>"])],
            minutes=_node_roll_minutes(profile),
            disruption="rolling",
        )
        step(
            UpgradePhase.VALIDATION,
            f"Validate hop to {v} before proceeding",
            "Gate the next hop on: all nodes Ready at the new version, no CrashLoopBackOffs, "
            "webhook and DNS health, workload smoke tests.",
            ["kubectl get nodes", "kubectl get pods -A | grep -v Running | grep -v Completed"],
            minutes=15,
        )

    # ── Final workload validation ────────────────────────────────────────
    step(
        UpgradePhase.WORKLOADS,
        "Run application-level verification",
        "Synthetic checks / golden transactions against critical services; compare "
        "error rates and latency to the pre-upgrade baseline.",
        minutes=30,
    )

    rollback = _rollback_steps(flavour)

    return UpgradePlan(
        strategy=_strategy(profile, hops),
        hop_sequence=[
            f"{a.minor_str}→{b.minor_str}" for a, b in zip([source, *hops[:-1]], hops, strict=True)
        ],
        steps=steps,
        rollback=rollback,
        pre_upgrade_checklist=_pre_checklist(profile),
        post_upgrade_validation=_post_checklist(profile),
    )


def _addon_requirements(profile: ClusterProfileSummary, hop: KubeVersion) -> str:
    """Concrete per-hop minimums for detected components, from the same
    support matrices the compatibility engine uses. Turns 'upgrade addons'
    into 'Cluster Autoscaler → >=1.29, Cilium → >=1.15'."""
    from .compatibility import MATRICES  # local import avoids a module cycle

    requirements = []
    for component in profile.components:
        matrix = MATRICES.get(component.key)
        if matrix is None:
            continue
        minimum = matrix.min_component_for_k8s.get(hop.minor_str)
        if minimum:
            requirements.append(f"{component.display_name} → >={minimum}")
    if not requirements:
        return ""
    return " Detected components required at this hop: " + ", ".join(sorted(requirements)) + "."


def _strategy(profile: ClusterProfileSummary, hops: list[KubeVersion]) -> str:
    base = (
        "sequential in-place upgrade, one minor per maintenance window"
        if len(hops) > 1
        else "single-minor in-place upgrade"
    )
    if profile.flavour.is_local_dev:
        return "recreate cluster at target version (local/dev cluster — no in-place value)"
    if profile.flavour is ClusterFlavour.OPENSHIFT:
        return f"{base}, expressed as OpenShift release upgrades (CVO-managed)"
    return base


def _node_pool_description(profile: ClusterProfileSummary) -> str:
    pools = {n.node_pool for n in profile.nodes if n.node_pool}
    if pools:
        return (
            f"{len(pools)} node pool(s) detected ({', '.join(sorted(pools))}). "
            "Upgrade one pool at a time; start with the least critical. Use surge "
            "settings to keep capacity during rotation."
        )
    return (
        "Roll nodes with drain/replace, respecting PDBs; keep spare capacity for "
        "evicted pods during the roll."
    )


def _node_roll_minutes(profile: ClusterProfileSummary) -> int:
    # Heuristic: ~8 min per node with surge parallelism, floor 20.
    return max(20, min(profile.node_count, 50) * 8)


def _rollback_steps(flavour: ClusterFlavour) -> list[UpgradeStep]:
    steps = [
        UpgradeStep(
            order=1,
            phase=UpgradePhase.CONTROL_PLANE,
            title="Control plane rollback reality",
            description=(
                "Managed control planes (EKS/GKE/AKS) cannot be downgraded once upgraded. "
                "Self-managed: kubeadm downgrade is unsupported territory once etcd schema "
                "migrates — restore from the etcd snapshot instead. Rollback planning must "
                "therefore be *forward-fix for the control plane, backward-roll for nodes*."
                if flavour.is_managed
                else "Restore the pre-upgrade etcd snapshot to roll back the control plane; this "
                "loses all API writes made after the snapshot. Prefer forward-fixes."
            ),
        ),
        UpgradeStep(
            order=2,
            phase=UpgradePhase.NODE_POOLS,
            title="Roll nodes back to previous version",
            description="Recreate node pools pinned to the previous version/AMI and drain new-version nodes.",
        ),
        UpgradeStep(
            order=3,
            phase=UpgradePhase.WORKLOADS,
            title="Revert workload/addon changes",
            description="Helm rollback for addons upgraded during the window; redeploy prior manifests via GitOps history.",
            commands=["helm rollback <release> <revision>"],
        ),
    ]
    return steps


def _pre_checklist(profile: ClusterProfileSummary) -> list[str]:
    items = [
        "All removed-API findings remediated and verified with pluto/kubent",
        "Webhook operators (cert-manager, policy engines) at target-compatible versions",
        "etcd snapshot / velero backup completed and restore-tested",
        "PDBs reviewed; no maxUnavailable: 0 deadlocks",
        "Maintenance window and rollback decision criteria agreed",
        "Monitoring dashboards and alerts green before starting",
    ]
    if profile.flavour.is_managed:
        items.append("Cloud provider release notes for the target version reviewed")
    else:
        items.append("OS packages for target kubeadm/kubelet staged on all nodes")
    return items


def _post_checklist(profile: ClusterProfileSummary) -> list[str]:
    return [
        "All nodes Ready at target kubelet version (kubectl get nodes)",
        "No pods in CrashLoopBackOff/ImagePullBackOff introduced by the upgrade",
        "Admission webhooks answering (create a dry-run object through each)",
        "DNS resolution healthy (CoreDNS metrics/log check)",
        "Ingress traffic serving; certificate issuance verified if cert-manager present",
        "Autoscaling verified (scale a canary deployment; node provisioning if Karpenter/CA)",
        "Application golden-path checks pass; error budgets not consumed",
        "Deprecation warnings in audit logs reviewed for the *next* upgrade",
    ]


def estimate_downtime(
    profile: ClusterProfileSummary, snapshot: ClusterSnapshot, hops: int
) -> DowntimeEstimate:
    pdb_rows = max(len(snapshot.stdout("pdbs").splitlines()) - 1, 0)
    assumptions = [
        f"{profile.node_count} nodes, {profile.workloads.total} workloads, {hops} hop(s)",
        "Surge capacity available for node rotation",
    ]
    if profile.flavour.is_managed:
        cp = "None expected — managed control plane upgrades are non-disruptive to the API."
    else:
        cp = (
            "Brief API server unavailability per control-plane node restart "
            "(seconds to ~2 min per hop on HA; longer on single control-plane clusters)."
        )
    if pdb_rows > 0:
        wl = (
            "Zero expected for PDB-protected, multi-replica workloads; single-replica "
            "pods restart once per node rotation."
        )
        assumptions.append(f"{pdb_rows} PodDisruptionBudget(s) present")
    else:
        wl = (
            "No PDBs detected: every workload restarts un-coordinated during node "
            "rotation; single-replica services will take brief outages."
        )
    window = hops * (15 + max(20, min(profile.node_count, 50) * 8) + 35)
    return DowntimeEstimate(
        control_plane_impact=cp,
        workload_impact=wl,
        estimated_window_minutes=window,
        assumptions=assumptions,
    )
