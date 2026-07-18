"""Risk engine: evidence metrics, readiness scoring, confidence.

Two separate numbers, deliberately:
  - readiness — how safe is this upgrade, given what we *found*
  - confidence — how much of the cluster we could actually *see*

Missing data lowers confidence and caps readiness; it never raises either.
The LLM receives these numbers as constraints and cannot exceed them — the
renderer re-applies the caps after the LLM responds, so a creative model
cannot inflate a verdict.
"""

from __future__ import annotations

from ..collectors.cluster import CRITICAL_COMMANDS
from ..models import (
    ClusterProfileSummary,
    ClusterSnapshot,
    DetectedComponent,
    EvidenceMetrics,
    Finding,
    ReadinessScore,
    Severity,
)

# Commands that are expected to fail on some flavours — not evidence gaps.
_EXPECTED_MISSING: dict[str, set[str]] = {
    "kind": {"top_nodes", "psp", "flowschemas"},
    "docker-desktop": {"top_nodes", "psp"},
    "minikube": {"top_nodes", "psp"},
    "eks": {"psp"},
    "gke": {"psp"},
    "aks": {"psp"},
    "openshift": {"psp"},
    "rke2": {"psp"},
    "k3s": {"psp"},
    "kubeadm": {"psp"},
    "unknown": {"psp"},
}

_SEVERITY_PENALTY = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 12,
    Severity.MEDIUM: 5,
    Severity.LOW: 2,
    Severity.INFO: 0,
}


def build_evidence_metrics(
    snapshot: ClusterSnapshot,
    profile: ClusterProfileSummary,
    components: list[DetectedComponent],
) -> EvidenceMetrics:
    expected_missing = _EXPECTED_MISSING.get(profile.flavour.value, {"psp"})

    considered = {
        key: result for key, result in snapshot.kubectl.items() if key not in expected_missing
    }
    critical = {key for key in CRITICAL_COMMANDS if key not in expected_missing}

    metrics = EvidenceMetrics(
        commands_ok=sum(1 for r in considered.values() if r.ok),
        commands_total=len(considered),
        critical_ok=sum(1 for key in critical if snapshot.command(key).ok),
        critical_total=len(critical),
        components_detected=len(components),
        components_with_versions=sum(1 for c in components if c.version),
    )

    # Enumerate honest unknowns — these feed the report's Unknown Risks section.
    if not snapshot.command("top_nodes").ok and "top_nodes" not in expected_missing:
        metrics.unknown_risks.append(
            "Node resource headroom unknown (metrics-server unavailable) — surge capacity "
            "during node rotation is unverified."
        )
    if not snapshot.helm_available:
        metrics.unknown_risks.append(
            "Helm not available during collection — component versions rely on image tags only."
        )
    unversioned = [c.display_name for c in components if not c.version]
    if unversioned:
        metrics.unknown_risks.append(
            "Version could not be resolved for: "
            + ", ".join(sorted(unversioned))
            + " — their target compatibility is unverified."
        )
    if not snapshot.command("pdbs").has_output:
        metrics.unknown_risks.append(
            "No PodDisruptionBudget data — workload disruption during node drains is unmodelled."
        )
    metrics.unknown_risks.append(
        "No load/canary testing performed — runtime behaviour under production traffic "
        "during the upgrade is unverified."
    )
    return metrics


def compute_confidence(metrics: EvidenceMetrics) -> int:
    score = (
        metrics.critical_coverage * 45
        + metrics.command_success_rate * 25
        + metrics.version_resolution_rate * 20
        + (1.0 if metrics.kb_chunks_retrieved > 0 else 0.4) * 10
    )
    if metrics.critical_coverage < 1.0:
        score = min(score, 75.0)
    return round(score)


def compute_readiness(findings: list[Finding], metrics: EvidenceMetrics) -> ReadinessScore:
    penalty = 0
    for finding in findings:
        penalty += _SEVERITY_PENALTY[finding.severity]
    raw = max(0, 100 - penalty)

    # Evidence-based caps: what score can the data support at all?
    cap, cap_reason = 100, ""
    if metrics.critical_coverage < 1.0:
        cap, cap_reason = (
            60,
            ("critical inventory commands failed — the cluster was not fully observable"),
        )
    elif metrics.unknown_risks and metrics.version_resolution_rate < 0.5:
        cap, cap_reason = (
            80,
            ("most component versions unresolved — compatibility verdicts are incomplete"),
        )
    elif metrics.unknown_risks:
        cap, cap_reason = 95, "unverified risks remain (see Unknown Risks)"

    score = min(raw, cap)
    has_blockers = any(f.blocking for f in findings)
    return ReadinessScore(
        score=score,
        cap=cap,
        cap_reason=cap_reason,
        confidence=compute_confidence(metrics),
        verdict=ReadinessScore.verdict_for(score, has_blockers),
    )
