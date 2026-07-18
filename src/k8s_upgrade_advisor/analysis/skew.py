"""Kubernetes version skew policy engine.

Encodes the official skew policy (kubernetes.io/releases/version-skew-policy):
  - kubelet may be up to n-2 minors behind kube-apiserver (n-3 from 1.28)
  - kube-proxy follows the kubelet rule
  - control plane upgrades one minor at a time
  - during an upgrade, apiserver minors in an HA control plane may differ by 1

The engine turns the node inventory into concrete findings: which node pools
must be upgraded before/with which control plane hop.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import (
    ClusterSnapshot,
    Evidence,
    Finding,
    FindingCategory,
    FindingOrigin,
    KubeVersion,
    NodeInfo,
    Severity,
)


def allowed_kubelet_skew(control_plane: KubeVersion) -> int:
    """Maximum minors a kubelet may trail the apiserver."""
    return 3 if control_plane >= KubeVersion(1, 28) else 2


def skew_findings(nodes: list[NodeInfo], source: KubeVersion, target: KubeVersion) -> list[Finding]:
    findings: list[Finding] = []

    # Group nodes by kubelet minor for aggregate reporting.
    by_minor: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if not node.kubelet_version:
            continue
        try:
            kubelet = KubeVersion.parse(node.kubelet_version)
        except Exception:
            continue
        by_minor[kubelet.minor_str].append(node.name)

    hops = source.minors_until(target)

    for minor_str, node_names in sorted(by_minor.items()):
        kubelet = KubeVersion.parse(minor_str)

        # Skew violated already at the source? (inconsistent cluster)
        if source.minor - kubelet.minor > allowed_kubelet_skew(source):
            findings.append(
                _skew_finding(
                    minor_str,
                    node_names,
                    source,
                    "already violates",
                    severity=Severity.HIGH,
                    blocking=True,
                )
            )
            continue

        # Walk each control-plane hop and find where this kubelet minor
        # falls out of the supported window.
        for hop in hops:
            if hop.minor - kubelet.minor > allowed_kubelet_skew(hop):
                findings.append(
                    _skew_finding(
                        minor_str,
                        node_names,
                        hop,
                        "will violate",
                        severity=Severity.HIGH,
                        blocking=True,
                    )
                )
                break

    # Multi-hop path is itself a planning constraint worth stating.
    if len(hops) > 1:
        findings.append(
            Finding(
                id="upgrade-path-multi-hop",
                title=f"{len(hops)}-hop upgrade path: control plane must move one minor at a time",
                category=FindingCategory.UPGRADE_PATH,
                severity=Severity.MEDIUM,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    f"Kubernetes does not support skipping minors for the control plane. "
                    f"{source.minor_str} → {target.minor_str} requires sequential hops: "
                    + " → ".join(v.minor_str for v in hops)
                    + ". Each hop needs its own validation gate; API removals apply per-hop, "
                    "not only at the final version."
                ),
                remediation="Plan and validate each hop independently; do not batch hops in one window.",
                evidence=[
                    Evidence(
                        kind="static-table",
                        detail="Kubernetes version skew policy: kube-apiserver upgrades are supported "
                        "only from the previous minor.",
                    )
                ],
            )
        )
    return findings


def _skew_finding(
    kubelet_minor: str,
    node_names: list[str],
    control_plane: KubeVersion,
    verb: str,
    severity: Severity,
    blocking: bool,
) -> Finding:
    shown = ", ".join(node_names[:5]) + ("…" if len(node_names) > 5 else "")
    max_skew = allowed_kubelet_skew(control_plane)
    return Finding(
        id=f"skew-kubelet-{kubelet_minor.replace('.', '-')}-cp-{control_plane.minor_str.replace('.', '-')}",
        title=f"kubelet {kubelet_minor} {verb} skew policy at control plane {control_plane.minor_str}",
        category=FindingCategory.VERSION_SKEW,
        severity=severity,
        origin=FindingOrigin.DETERMINISTIC,
        description=(
            f"{len(node_names)} node(s) run kubelet {kubelet_minor} ({shown}). The skew policy "
            f"allows kubelets at most {max_skew} minors behind the apiserver, so these nodes "
            f"{verb} the policy when the control plane reaches {control_plane.minor_str}."
        ),
        remediation=(
            f"Upgrade these node pools to within {max_skew} minors before the control plane "
            f"hop to {control_plane.minor_str}."
        ),
        blocking=blocking,
        affected_objects=node_names,
        evidence=[
            Evidence(
                kind="cluster-data",
                detail=f"kubelet versions from node inventory: {len(node_names)} node(s) at {kubelet_minor}.",
            ),
            Evidence(
                kind="static-table",
                detail=f"Skew policy: kubelet may trail apiserver by {max_skew} minors "
                f"at control plane {control_plane.minor_str}.",
            ),
        ],
    )


_KUBE_PROXY_RE = re.compile(r"kube-proxy[^\s,]*:v?(\d+\.\d+)")


def kube_proxy_findings(
    snapshot: ClusterSnapshot, source: KubeVersion, target: KubeVersion
) -> list[Finding]:
    """kube-proxy follows the kubelet skew rule with one extra constraint:
    it must never be NEWER than kube-apiserver. Version is read from the
    kube-proxy DaemonSet image tag when present (managed clusters ship it as
    an addon; self-managed run it per node)."""
    match = _KUBE_PROXY_RE.search(snapshot.stdout("daemonsets"))
    if not match:
        return []
    try:
        proxy = KubeVersion.parse(match.group(1))
    except Exception:
        return []

    findings: list[Finding] = []
    if proxy.minor > source.minor:
        findings.append(
            Finding(
                id="skew-kube-proxy-newer-than-apiserver",
                title=f"kube-proxy {proxy.minor_str} is newer than the control plane "
                f"{source.minor_str}",
                category=FindingCategory.VERSION_SKEW,
                severity=Severity.HIGH,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    "The skew policy forbids kube-proxy running ahead of kube-apiserver. "
                    "This cluster already violates it before the upgrade begins — service "
                    "routing behaviour is undefined."
                ),
                remediation="Align kube-proxy with the control plane minor before planning hops.",
                blocking=True,
                evidence=[
                    Evidence(
                        kind="cluster-data",
                        detail=f"kube-proxy image tag v{proxy.minor_str} vs apiserver "
                        f"{source.minor_str}.",
                    ),
                    Evidence(
                        kind="static-table",
                        detail="Skew policy: kube-proxy must not be newer than kube-apiserver.",
                    ),
                ],
            )
        )
        return findings

    for hop in source.minors_until(target):
        if hop.minor - proxy.minor > allowed_kubelet_skew(hop):
            findings.append(
                Finding(
                    id=f"skew-kube-proxy-{proxy.minor_str.replace('.', '-')}"
                    f"-cp-{hop.minor_str.replace('.', '-')}",
                    title=f"kube-proxy {proxy.minor_str} falls out of skew at control plane "
                    f"{hop.minor_str}",
                    category=FindingCategory.VERSION_SKEW,
                    severity=Severity.HIGH,
                    origin=FindingOrigin.DETERMINISTIC,
                    description=(
                        f"kube-proxy follows the kubelet skew window "
                        f"(n-{allowed_kubelet_skew(hop)} at {hop.minor_str}); it must be "
                        "upgraded with the addon phase before this hop."
                    ),
                    remediation=f"Upgrade the kube-proxy addon before the control plane reaches "
                    f"{hop.minor_str}.",
                    blocking=True,
                    evidence=[
                        Evidence(
                            kind="cluster-data",
                            detail=f"kube-proxy image tag v{proxy.minor_str} from the DaemonSet listing.",
                        )
                    ],
                )
            )
            break
    return findings
