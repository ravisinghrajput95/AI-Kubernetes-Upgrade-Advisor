"""Recommendation engine: runs the LLM over the deterministic report and
merges the result under strict trust rules.

Trust boundary (enforced here, after the model responds — not by prompt
hope):
  - Scores/verdict: deterministic values always win; the model never sets them.
  - Findings from the model: forced origin=llm, never blocking, severity
    capped at HIGH, and dropped if they duplicate a deterministic finding id.
  - Citations: refs that don't exist in the retrieved context are discarded.
  - Plan: adopted only if structurally valid; otherwise the deterministic
    skeleton stands. Checklists are merged (union) so the model can only add
    checks, not remove them.
"""

from __future__ import annotations

import time

from pydantic import ValidationError

from ..errors import LLMResponseInvalid
from ..models import (
    AssessmentReport,
    Citation,
    FindingOrigin,
    LLMAnalysis,
    LLMMetadata,
    Severity,
)
from ..observability import get_logger
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .provider import LLMProvider, parse_json_response

log = get_logger(__name__)


def run_llm_analysis(
    report: AssessmentReport,
    context_text: str,
    citations: list[Citation],
    provider: LLMProvider,
    dry_run: bool = False,
) -> AssessmentReport:
    """Augment ``report`` in place with LLM narrative + refined plan."""
    user_prompt = build_user_prompt(report, context_text)
    report.llm = LLMMetadata(
        provider=provider.name,
        model=provider.model,
        prompt_chars=len(user_prompt),
        dry_run=dry_run,
    )
    report.citations = citations

    if dry_run:
        report.executive_summary = (
            "[dry-run] Deterministic assessment only — no LLM narrative was generated. "
            f"Verdict {report.readiness.verdict.value} with readiness "
            f"{report.readiness.score}/100 from {len(report.findings)} findings."
        )
        return report

    started = time.monotonic()
    raw = provider.complete_json(SYSTEM_PROMPT, user_prompt)
    analysis = _validate_with_repair(raw, provider, user_prompt)
    report.llm.completion_chars = len(raw)
    report.llm.duration_ms = int((time.monotonic() - started) * 1000)

    _merge(report, analysis, citations)
    return report


def _validate_with_repair(raw: str, provider: LLMProvider, user_prompt: str) -> LLMAnalysis:
    """One repair round-trip: if validation fails, show the model its own
    errors. A second failure is terminal — deterministic output still ships."""
    try:
        return LLMAnalysis.model_validate(parse_json_response(raw))
    except (ValidationError, ValueError) as first_error:
        log.warning("llm_response_invalid", error=str(first_error)[:300], repairing=True)
        repair_prompt = (
            f"{user_prompt}\n\n## REPAIR\nYour previous response failed schema validation:\n"
            f"{str(first_error)[:1500]}\n\nReturn a corrected JSON object. JSON only."
        )
        raw2 = provider.complete_json(SYSTEM_PROMPT, repair_prompt)
        try:
            return LLMAnalysis.model_validate(parse_json_response(raw2))
        except (ValidationError, ValueError) as second_error:
            raise LLMResponseInvalid(
                f"LLM output failed schema validation twice: {second_error}"
            ) from second_error


def _merge(report: AssessmentReport, analysis: LLMAnalysis, citations: list[Citation]) -> None:
    report.executive_summary = analysis.executive_summary
    report.risk_narrative = analysis.risk_narrative

    # ── Findings: append-only, demoted trust ─────────────────────────────
    existing_ids = {f.id for f in report.findings}
    added = 0
    for finding in analysis.additional_findings:
        if finding.id in existing_ids:
            continue
        finding.origin = FindingOrigin.LLM
        finding.blocking = False
        if finding.severity is Severity.CRITICAL:
            finding.severity = Severity.HIGH
        report.findings.append(finding)
        added += 1

    # ── Compatibility notes: fill gaps only, never overwrite verdicts ────
    known = {e.component.lower() for e in report.compatibility_matrix}
    for entry in analysis.compatibility_notes:
        if entry.component.lower() not in known:
            report.compatibility_matrix.append(entry)

    # ── Plan: structural validation before adoption ──────────────────────
    if analysis.plan.steps:
        analysis.plan.hop_sequence = report.plan.hop_sequence  # hop math is ours
        analysis.plan.pre_upgrade_checklist = _union(
            report.plan.pre_upgrade_checklist, analysis.plan.pre_upgrade_checklist
        )
        analysis.plan.post_upgrade_validation = _union(
            report.plan.post_upgrade_validation, analysis.plan.post_upgrade_validation
        )
        if not analysis.plan.rollback:
            analysis.plan.rollback = report.plan.rollback
        if not analysis.plan.strategy:
            analysis.plan.strategy = report.plan.strategy
        report.plan = analysis.plan
    if analysis.upgrade_strategy:
        report.plan.strategy = analysis.upgrade_strategy

    if analysis.downtime.control_plane_impact or analysis.downtime.workload_impact:
        report.downtime = analysis.downtime

    # ── Citations: keep only refs that exist ─────────────────────────────
    valid_refs = {c.ref for c in citations}
    used = [ref for ref in analysis.citations_used if ref in valid_refs]
    dropped = set(analysis.citations_used) - set(used)
    if dropped:
        log.warning("llm_invalid_citations_dropped", refs=sorted(dropped))
    report.citations = [c for c in citations if c.ref in used] or citations

    log.info(
        "llm_merge_complete",
        added_findings=added,
        plan_steps=len(report.plan.steps),
        citations_used=len(used),
    )


def _union(base: list[str], extra: list[str]) -> list[str]:
    seen = {item.strip().lower() for item in base}
    merged = list(base)
    for item in extra:
        if item.strip().lower() not in seen:
            merged.append(item)
    return merged
