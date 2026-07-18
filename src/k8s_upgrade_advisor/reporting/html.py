"""Self-contained HTML report renderer.

Renders directly from :class:`AssessmentReport` (same data as markdown — the
two never drift). Inline CSS, no external assets, light/dark via
prefers-color-scheme, printable.
"""

from __future__ import annotations

from html import escape

from ..models import AssessmentReport, CompatibilityStatus, Severity

_VERDICT_CLASS = {
    "ready": ("READY", "ok"),
    "ready-with-cautions": ("READY WITH CAUTIONS", "warn"),
    "not-ready": ("NOT READY", "risk"),
    "blocked": ("BLOCKED", "block"),
}

_SEV_CLASS = {
    Severity.CRITICAL: "sev-critical",
    Severity.HIGH: "sev-high",
    Severity.MEDIUM: "sev-medium",
    Severity.LOW: "sev-low",
    Severity.INFO: "sev-info",
}

_STATUS_CLASS = {
    CompatibilityStatus.COMPATIBLE: "ok",
    CompatibilityStatus.UPGRADE_REQUIRED: "risk",
    CompatibilityStatus.INCOMPATIBLE: "block",
    CompatibilityStatus.UNKNOWN: "warn",
}

_CSS = """
:root { --bg:#fff; --fg:#1a1f27; --muted:#5b6472; --card:#f6f8fa; --line:#d8dee6;
  --ok:#1a7f37; --warn:#9a6700; --risk:#bc4c00; --block:#cf222e; --accent:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --card:#161b22; --line:#30363d;
    --ok:#3fb950; --warn:#d29922; --risk:#f0883e; --block:#f85149; --accent:#58a6ff; } }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
  font:15px/1.55 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
main { max-width:960px; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
h2 { font-size:1.15rem; margin:2.2rem 0 .8rem; padding-bottom:.3rem;
  border-bottom:1px solid var(--line); }
code, pre { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.86em;
  background:var(--card); border-radius:4px; padding:.1em .35em; }
.meta { color:var(--muted); font-size:.85rem; }
.scoreband { display:flex; gap:1rem; flex-wrap:wrap; margin:1.2rem 0; }
.scorecard { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:.9rem 1.2rem; min-width:150px; }
.scorecard .value { font-size:1.7rem; font-weight:700; }
.scorecard .label { color:var(--muted); font-size:.78rem; text-transform:uppercase;
  letter-spacing:.04em; }
.badge { display:inline-block; padding:.25rem .7rem; border-radius:999px; font-weight:700;
  font-size:.9rem; color:#fff; }
.badge.ok{background:var(--ok);} .badge.warn{background:var(--warn);}
.badge.risk{background:var(--risk);} .badge.block{background:var(--block);}
.finding { background:var(--card); border:1px solid var(--line); border-left-width:4px;
  border-radius:6px; padding: .9rem 1.1rem; margin:.7rem 0; }
.finding h3 { margin:0 0 .35rem; font-size:1rem; }
.sev-critical{border-left-color:var(--block);} .sev-high{border-left-color:var(--risk);}
.sev-medium{border-left-color:var(--warn);} .sev-low{border-left-color:var(--accent);}
.sev-info{border-left-color:var(--line);}
.tag { display:inline-block; font-size:.72rem; padding:.06rem .5rem; margin-right:.3rem;
  border:1px solid var(--line); border-radius:999px; color:var(--muted); }
.tag.blocking { color:#fff; background:var(--block); border-color:var(--block); }
table { border-collapse:collapse; width:100%; margin:.6rem 0; font-size:.9rem; }
th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.03em; }
.status { font-weight:600; }
.status.ok{color:var(--ok);} .status.warn{color:var(--warn);}
.status.risk{color:var(--risk);} .status.block{color:var(--block);}
.phase { color:var(--accent); font-size:.8rem; font-weight:700; text-transform:uppercase;
  letter-spacing:.05em; margin-top:1.1rem; }
ol.steps { padding-left:1.3rem; } ol.steps li { margin:.55rem 0; }
ul.check { list-style:none; padding-left:0; }
ul.check li::before { content:'☐ '; color:var(--muted); }
details { margin:.4rem 0; } summary { cursor:pointer; color:var(--muted); font-size:.85rem; }
.overflow { overflow-x:auto; }
footer { margin-top:3rem; color:var(--muted); font-size:.8rem;
  border-top:1px solid var(--line); padding-top:1rem; }
@media print { body{padding:0;} .finding{break-inside:avoid;} }
"""


def render_html(report: AssessmentReport) -> str:
    verdict_text, verdict_class = _VERDICT_CLASS[report.readiness.verdict.value]
    e = escape
    parts: list[str] = []
    add = parts.append

    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    add(
        f"<title>Upgrade Assessment {e(report.source_version)} → {e(report.target_version)}</title>"
    )
    add(f"<style>{_CSS}</style></head><body><main>")

    add(
        f"<h1>Kubernetes Upgrade Assessment — {e(report.source_version)} → "
        f"{e(report.target_version)}</h1>"
    )
    add(
        f"<p class='meta'>{e(report.id)} · {report.created_at:%Y-%m-%d %H:%M UTC} · "
        f"{e(report.profile.flavour.value)} · hops: "
        f"{e(' → '.join(report.plan.hop_sequence) or 'single')}</p>"
    )

    add("<div class='scoreband'>")
    add(
        f"<div class='scorecard'><div class='value'><span class='badge {verdict_class}'>"
        f"{verdict_text}</span></div><div class='label'>Verdict</div></div>"
    )
    cap_note = f" / cap {report.readiness.cap}" if report.readiness.cap < 100 else ""
    add(
        f"<div class='scorecard'><div class='value'>{report.readiness.score}{e(cap_note)}</div>"
        "<div class='label'>Readiness</div></div>"
    )
    add(
        f"<div class='scorecard'><div class='value'>{report.readiness.confidence}</div>"
        "<div class='label'>Confidence</div></div>"
    )
    add(
        f"<div class='scorecard'><div class='value'>{len(report.blocking_findings)}</div>"
        "<div class='label'>Blocking findings</div></div>"
    )
    add("</div>")
    if report.readiness.cap_reason:
        add(f"<p class='meta'>Score capped: {e(report.readiness.cap_reason)}</p>")

    if report.executive_summary:
        add("<h2>Executive Summary</h2>")
        add(f"<p>{e(report.executive_summary)}</p>")

    # ── Profile ──────────────────────────────────────────────────────────
    profile = report.profile
    add("<h2>Cluster Profile</h2><div class='overflow'><table>")
    add("<tr><th>Distribution</th><th>Version</th><th>Nodes</th><th>Workloads</th></tr>")
    workloads = profile.workloads
    add(
        f"<tr><td>{e(profile.flavour.value)}<br><span class='meta'>"
        f"{e('; '.join(profile.flavour_evidence))}</span></td>"
        f"<td>{e(profile.current_version or 'unknown')}</td>"
        f"<td>{profile.node_count}</td>"
        f"<td>{workloads.deployments} deploy · {workloads.statefulsets} sts · "
        f"{workloads.daemonsets} ds · {workloads.cronjobs} cron</td></tr>"
    )
    add("</table></div>")
    if profile.components:
        add(
            "<div class='overflow'><table><tr><th>Component</th><th>Version</th>"
            "<th>Evidence</th></tr>"
        )
        for component in profile.components:
            add(
                f"<tr><td>{e(component.display_name)}</td>"
                f"<td>{e(component.version or '—')}</td>"
                f"<td>{e(component.version_source)}</td></tr>"
            )
        add("</table></div>")

    # ── Findings ─────────────────────────────────────────────────────────
    add(f"<h2>Findings ({len(report.findings)})</h2>")
    if not report.findings:
        add("<p>No findings — deterministic analyzers found nothing requiring action.</p>")
    for finding in report.findings_by_severity():
        add(f"<div class='finding {_SEV_CLASS[finding.severity]}'>")
        add(f"<h3>{e(finding.title)}</h3>")
        add("<p>")
        if finding.blocking:
            add("<span class='tag blocking'>BLOCKING</span>")
        add(
            f"<span class='tag'>{e(finding.severity.value)}</span>"
            f"<span class='tag'>{e(finding.category.value)}</span>"
            f"<span class='tag'>origin: {e(finding.origin.value)}</span>"
        )
        if finding.effective_in:
            add(f"<span class='tag'>effective {e(finding.effective_in)}</span>")
        add("</p>")
        add(f"<p>{e(finding.description)}</p>")
        if finding.affected_objects:
            shown = ", ".join(finding.affected_objects[:8])
            add(f"<p class='meta'>Affected: {e(shown)}</p>")
        if finding.remediation:
            add(f"<p><strong>Remediation:</strong> {e(finding.remediation)}</p>")
        if finding.evidence:
            add("<details><summary>Evidence</summary><ul>")
            for evidence in finding.evidence:
                refs = "".join(f" [DOC {r}]" for r in evidence.citation_refs)
                add(f"<li><em>{e(evidence.kind)}</em>: {e(evidence.detail)}{e(refs)}</li>")
            add("</ul></details>")
        add("</div>")

    # ── Matrix ───────────────────────────────────────────────────────────
    if report.compatibility_matrix:
        add(f"<h2>Compatibility Matrix — target {e(report.target_version)}</h2>")
        add(
            "<div class='overflow'><table><tr><th>Component</th><th>Installed</th>"
            "<th>Status</th><th>Min required</th><th>Notes</th></tr>"
        )
        for entry in report.compatibility_matrix:
            status_class = _STATUS_CLASS[entry.status]
            add(
                f"<tr><td>{e(entry.component)}</td>"
                f"<td>{e(entry.current_version or 'unknown')}</td>"
                f"<td class='status {status_class}'>{e(entry.status.value)}</td>"
                f"<td>{e(entry.minimum_version or '—')}</td>"
                f"<td>{e(entry.notes[:200])}</td></tr>"
            )
        add("</table></div>")

    # ── Plan ─────────────────────────────────────────────────────────────
    add("<h2>Upgrade Plan</h2>")
    add(f"<p><strong>Strategy:</strong> {e(report.plan.strategy)}</p>")
    current_phase = None
    open_list = False
    for step in report.plan.steps:
        if step.phase != current_phase:
            if open_list:
                add("</ol>")
            current_phase = step.phase
            add(f"<div class='phase'>{e(current_phase.value.replace('-', ' '))}</div>")
            add("<ol class='steps'>")
            open_list = True
        timing = (
            (
                f" <span class='meta'>~{step.estimated_minutes} min · "
                f"disruption: {e(step.disruption)}</span>"
            )
            if step.estimated_minutes
            else ""
        )
        add(f"<li><strong>{e(step.title)}</strong>{timing}")
        if step.description:
            add(f"<br>{e(step.description)}")
        for command in step.commands:
            add(f"<br><code>{e(command)}</code>")
        add("</li>")
    if open_list:
        add("</ol>")

    if report.plan.rollback:
        add("<h2>Rollback Plan</h2><ol class='steps'>")
        for step in report.plan.rollback:
            add(f"<li><strong>{e(step.title)}</strong><br>{e(step.description)}")
            for command in step.commands:
                add(f"<br><code>{e(command)}</code>")
            add("</li>")
        add("</ol>")

    for title, items in (
        ("Pre-Upgrade Checklist", report.plan.pre_upgrade_checklist),
        ("Post-Upgrade Validation", report.plan.post_upgrade_validation),
    ):
        if items:
            add(f"<h2>{title}</h2><ul class='check'>")
            parts.extend(f"<li>{e(item)}</li>" for item in items)
            add("</ul>")

    # ── Downtime / risks / citations ─────────────────────────────────────
    add("<h2>Downtime &amp; Disruption</h2><ul>")
    add(f"<li><strong>Control plane:</strong> {e(report.downtime.control_plane_impact)}</li>")
    add(f"<li><strong>Workloads:</strong> {e(report.downtime.workload_impact)}</li>")
    if report.downtime.estimated_window_minutes:
        add(
            f"<li><strong>Estimated window:</strong> "
            f"~{report.downtime.estimated_window_minutes} minutes</li>"
        )
    add("</ul>")
    if report.downtime.assumptions:
        add(
            "<p class='meta'>Assumptions: "
            + "; ".join(e(a) for a in report.downtime.assumptions)
            + "</p>"
        )

    if report.risk_narrative:
        add("<h2>Risk Narrative</h2>")
        add(f"<p>{e(report.risk_narrative)}</p>")

    if report.unknown_risks:
        add("<h2>Unknown Risks</h2><ul>")
        parts.extend(f"<li>{e(risk)}</li>" for risk in report.unknown_risks)
        add("</ul>")

    if report.citations:
        add("<h2>Sources</h2><ul>")
        for citation in report.citations:
            version = f" (Kubernetes {e(citation.k8s_version)})" if citation.k8s_version else ""
            add(
                f"<li>[DOC {citation.ref}] <a href='{e(citation.url)}'>"
                f"{e(citation.title)}</a>{version}</li>"
            )
        add("</ul>")

    em = report.evidence_metrics
    add(
        "<footer>Evidence: "
        f"kubectl {em.commands_ok}/{em.commands_total} ok (critical "
        f"{em.critical_ok}/{em.critical_total}) · versions resolved "
        f"{em.components_with_versions}/{em.components_detected} · "
        f"KB chunks {em.kb_chunks_retrieved} from {em.kb_sources} docs · "
        f"LLM {e(report.llm.provider)}/{e(report.llm.model)}"
        + (" (dry run)" if report.llm.dry_run else "")
        + "<br>Generated by k8s-upgrade-advisor — deterministic findings are provable "
        "from cluster data; LLM-origin content is labelled.</footer>"
    )
    add("</main></body></html>")
    return "".join(parts)
