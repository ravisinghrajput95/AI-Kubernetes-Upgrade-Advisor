"""Deterministic analysis pipeline.

snapshot → profile → components → findings (API lifecycle, skew, compat)
→ plan skeleton → downtime → evidence metrics → readiness.

The output is a complete, standalone :class:`AssessmentReport` — the LLM
layer *augments* it (narrative, refined plan) but the platform degrades
gracefully to this deterministic report when no LLM is configured.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ..models import (
    AssessmentReport,
    ClusterSnapshot,
    KubeVersion,
)
from ..observability import get_logger
from . import api_lifecycle, compatibility, components, planner, profile, risk, skew

log = get_logger(__name__)


def run_deterministic_analysis(
    snapshot: ClusterSnapshot, source: KubeVersion, target: KubeVersion
) -> AssessmentReport:
    prof = profile.build_profile(snapshot)
    detected = components.detect_components(snapshot)
    prof.components = detected

    findings = []
    findings += api_lifecycle.detect_api_removal_findings(snapshot, source, target)
    findings += api_lifecycle.detect_behavior_findings(
        snapshot,
        source,
        target,
        node_runtimes=[n.container_runtime for n in prof.nodes],
    )
    findings += skew.skew_findings(prof.nodes, source, target)
    matrix, compat_findings = compatibility.compatibility_findings(detected, target)
    findings += compat_findings

    plan = planner.build_plan(prof, snapshot, source, target)
    hops = source.minors_until(target)
    downtime = planner.estimate_downtime(prof, snapshot, len(hops))

    metrics = risk.build_evidence_metrics(snapshot, prof, detected)
    readiness = risk.compute_readiness(findings, metrics)

    report = AssessmentReport(
        id=f"assess-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
        source_version=source.minor_str,
        target_version=target.minor_str,
        version_path=[v.minor_str for v in hops],
        profile=prof,
        readiness=readiness,
        findings=sorted(findings, key=lambda f: (f.severity.rank, f.category.value)),
        compatibility_matrix=matrix,
        plan=plan,
        downtime=downtime,
        unknown_risks=metrics.unknown_risks,
        evidence_metrics=metrics,
    )
    log.info(
        "deterministic_analysis_complete",
        findings=len(findings),
        blocking=len(report.blocking_findings),
        readiness=readiness.score,
        verdict=readiness.verdict.value,
    )
    return report
