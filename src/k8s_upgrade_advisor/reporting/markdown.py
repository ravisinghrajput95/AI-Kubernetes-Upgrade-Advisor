"""Markdown renderer — pure function of :class:`AssessmentReport`.

No LLM prose is parsed here; every section reads structured fields. The
markdown is the canonical human artifact (PR-attachable, grep-able); HTML
is derived from the same data, not from this markdown.
"""

from __future__ import annotations

from ..models import AssessmentReport, Finding, Severity, UpgradePhase

_VERDICT_LABEL = {
    "ready": "✅ READY",
    "ready-with-cautions": "🟡 READY WITH CAUTIONS",
    "not-ready": "🔶 NOT READY",
    "blocked": "⛔ BLOCKED",
}

_SEVERITY_ICON = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "⚪",
}


def render_markdown(report: AssessmentReport) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# Kubernetes Upgrade Assessment — {report.source_version} → {report.target_version}")
    add("")
    add(f"**Verdict: {_VERDICT_LABEL[report.readiness.verdict.value]}**  ")
    add(
        f"Readiness **{report.readiness.score}/100**"
        + (
            f" (capped at {report.readiness.cap}: {report.readiness.cap_reason})"
            if report.readiness.cap_reason
            else ""
        )
        + f" · Confidence **{report.readiness.confidence}/100**"
    )
    add("")
    add(
        f"`{report.id}` · {report.created_at:%Y-%m-%d %H:%M UTC} · "
        f"cluster: **{report.profile.flavour.value}** · "
        f"hops: {' → '.join(report.plan.hop_sequence) or 'single'}"
    )
    add("")

    if report.executive_summary:
        add("## Executive Summary")
        add("")
        add(report.executive_summary)
        add("")

    # ── Cluster profile ──────────────────────────────────────────────────
    profile = report.profile
    add("## Cluster Profile")
    add("")
    add(f"- **Distribution:** {profile.flavour.value} ({'; '.join(profile.flavour_evidence)})")
    add(f"- **Current version:** {profile.current_version or 'unknown'}")
    add(
        f"- **Nodes:** {profile.node_count} · **Workloads:** "
        f"{profile.workloads.deployments} deploy / {profile.workloads.statefulsets} sts / "
        f"{profile.workloads.daemonsets} ds / {profile.workloads.cronjobs} cron"
    )
    add(f"- **Upgrade mechanism:** {profile.upgrade_mechanism}")
    if profile.provider_managed:
        add(f"- **Provider-managed:** {', '.join(profile.provider_managed)}")
    if profile.components:
        add("")
        add("| Component | Version | Evidence |")
        add("|---|---|---|")
        for component in profile.components:
            add(
                f"| {component.display_name} | {component.version or '—'} "
                f"| {component.version_source} |"
            )
    add("")

    # ── Findings ─────────────────────────────────────────────────────────
    add(f"## Findings ({len(report.findings)})")
    add("")
    if not report.findings:
        add("No findings — the deterministic analyzers found nothing requiring action.")
        add("")
    for finding in report.findings_by_severity():
        _render_finding(add, finding)

    # ── Compatibility matrix ─────────────────────────────────────────────
    if report.compatibility_matrix:
        add("## Compatibility Matrix")
        add("")
        add(f"Target: Kubernetes {report.target_version}")
        add("")
        add("| Component | Installed | Status | Min required | Notes |")
        add("|---|---|---|---|---|")
        for entry in report.compatibility_matrix:
            add(
                f"| {entry.component} | {entry.current_version or 'unknown'} "
                f"| {entry.status.value} | {entry.minimum_version or '—'} "
                f"| {entry.notes[:160]} |"
            )
        add("")

    # ── Plan ─────────────────────────────────────────────────────────────
    add("## Upgrade Plan")
    add("")
    add(f"**Strategy:** {report.plan.strategy}")
    add("")
    current_phase: UpgradePhase | None = None
    for step in report.plan.steps:
        if step.phase != current_phase:
            current_phase = step.phase
            add(f"### {current_phase.value.replace('-', ' ').title()}")
            add("")
        add(
            f"{step.order}. **{step.title}**"
            + (
                f" _(~{step.estimated_minutes} min, disruption: {step.disruption})_"
                if step.estimated_minutes
                else ""
            )
        )
        if step.description:
            add(f"   {step.description}")
        for command in step.commands:
            add(f"   - `{command}`")
        add("")

    if report.plan.rollback:
        add("## Rollback Plan")
        add("")
        for step in report.plan.rollback:
            add(f"{step.order}. **{step.title}** — {step.description}")
            for command in step.commands:
                add(f"   - `{command}`")
        add("")

    for title, items in (
        ("Pre-Upgrade Checklist", report.plan.pre_upgrade_checklist),
        ("Post-Upgrade Validation", report.plan.post_upgrade_validation),
    ):
        if items:
            add(f"## {title}")
            add("")
            lines.extend(f"- [ ] {item}" for item in items)
            add("")

    # ── Downtime ─────────────────────────────────────────────────────────
    add("## Downtime & Disruption Estimate")
    add("")
    add(f"- **Control plane:** {report.downtime.control_plane_impact}")
    add(f"- **Workloads:** {report.downtime.workload_impact}")
    if report.downtime.estimated_window_minutes:
        add(f"- **Estimated window:** ~{report.downtime.estimated_window_minutes} minutes")
    for assumption in report.downtime.assumptions:
        add(f"- _Assumption: {assumption}_")
    add("")

    if report.risk_narrative:
        add("## Risk Narrative")
        add("")
        add(report.risk_narrative)
        add("")

    if report.unknown_risks:
        add("## Unknown Risks (honest gaps)")
        add("")
        lines.extend(f"- {risk}" for risk in report.unknown_risks)
        add("")

    if report.citations:
        add("## Sources")
        add("")
        for citation in report.citations:
            add(
                f"- **[DOC {citation.ref}]** [{citation.title}]({citation.url})"
                + (f" (Kubernetes {citation.k8s_version})" if citation.k8s_version else "")
            )
        add("")

    # ── Evidence appendix ────────────────────────────────────────────────
    em = report.evidence_metrics
    add("## Evidence Appendix")
    add("")
    add(
        f"- kubectl commands: {em.commands_ok}/{em.commands_total} succeeded "
        f"(critical: {em.critical_ok}/{em.critical_total})"
    )
    add(f"- Component versions resolved: {em.components_with_versions}/{em.components_detected}")
    add(f"- KB chunks retrieved: {em.kb_chunks_retrieved} from {em.kb_sources} documents")
    llm_line = f"- LLM: {report.llm.provider}/{report.llm.model}" + (
        " (dry run)" if report.llm.dry_run else ""
    )
    if report.llm.prompt_tokens:
        llm_line += f" · {report.llm.prompt_tokens}+{report.llm.completion_tokens} tokens"
    if report.llm.estimated_cost_usd:
        llm_line += f" · ~${report.llm.estimated_cost_usd:.4f}"
    add(llm_line)
    if not report.llm.dry_run and report.llm.provider != "none":
        add(
            f"- Narrative grounding: {report.llm.grounding_ratio:.0%} of substantive "
            "sentences carry a [DOC n] citation"
        )
    add("")
    add("---")
    add(
        "_Generated by k8s-upgrade-advisor. Deterministic findings are provable from "
        "cluster data and static lifecycle tables; LLM-origin content is labelled._"
    )
    return "\n".join(lines)


def _render_finding(add, finding: Finding) -> None:
    icon = _SEVERITY_ICON[finding.severity]
    block = " **[BLOCKING]**" if finding.blocking else ""
    add(f"### {icon} {finding.title}{block}")
    add("")
    add(
        f"`{finding.severity.value}` · `{finding.category.value}` · origin: "
        f"`{finding.origin.value}`"
        + (f" · effective in **{finding.effective_in}**" if finding.effective_in else "")
    )
    add("")
    add(finding.description)
    add("")
    if finding.affected_objects:
        shown = ", ".join(finding.affected_objects[:8])
        more = (
            f" (+{len(finding.affected_objects) - 8} more)"
            if len(finding.affected_objects) > 8
            else ""
        )
        add(f"**Affected:** {shown}{more}")
        add("")
    if finding.remediation:
        add(f"**Remediation:** {finding.remediation}")
        add("")
    if finding.evidence:
        add("<details><summary>Evidence</summary>")
        add("")
        for evidence in finding.evidence:
            refs = "".join(f" [DOC {r}]" for r in evidence.citation_refs)
            add(f"- _{evidence.kind}_: {evidence.detail}{refs}")
        add("")
        add("</details>")
        add("")
