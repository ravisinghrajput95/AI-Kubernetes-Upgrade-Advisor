"""Prompt construction for the recommendation engine.

Grounding contract given to the model:
  - Compatibility facts come ONLY from the deterministic findings and the
    numbered [DOC n] context. The model must cite [DOC n] refs for every
    document-derived claim and must say "unknown" rather than infer.
  - The model does not produce scores or verdicts — those are computed
    deterministically and given to it as fixed constraints to explain.
  - Output is a single JSON object matching the provided schema; anything
    else is rejected by validation.
"""

from __future__ import annotations

import json

from ..models import AssessmentReport, LLMAnalysis

SYSTEM_PROMPT = """\
You are a principal Kubernetes platform engineer writing the narrative and \
execution plan for a cluster upgrade assessment.

Non-negotiable rules:
1. GROUNDING — Every compatibility or behaviour claim must come from either \
(a) the DETERMINISTIC FINDINGS section, or (b) a retrieved document, cited \
inline as [DOC n]. If neither supports a claim, write that it is unknown and \
what evidence would resolve it. Never state a version compatibility from \
memory.
2. SCORES ARE FIXED — Readiness, confidence, and verdict are computed by the \
platform and provided to you. Explain them; do not restate different numbers.
3. PLAN REALISM — Steps must be executable by an on-call engineer: concrete \
commands for this distribution, one control-plane minor per hop, webhook \
operators upgraded before the control plane, validation gates between hops.
4. OUTPUT — Exactly one JSON object matching the provided schema. No prose \
outside JSON. Findings you add must have honest severity and never duplicate \
deterministic findings.
"""


def build_user_prompt(report: AssessmentReport, context_text: str) -> str:
    profile = report.profile
    # Token budget: findings are already severity-ordered; a pathological
    # cluster with hundreds of findings must not blow the context window.
    shown_findings = report.findings_by_severity()[:40]
    omitted = len(report.findings) - len(shown_findings)
    findings_block = (
        "\n".join(
            f"- [{f.severity.value.upper()}]{' [BLOCKING]' if f.blocking else ''} "
            f"({f.category.value}) {f.title} — {f.description[:300]}"
            for f in shown_findings
        )
        + (
            f"\n- (+{omitted} lower-severity findings omitted from prompt; "
            "they appear in the deterministic report)"
            if omitted > 0
            else ""
        )
        or "- none"
    )

    matrix_block = (
        "\n".join(
            f"- {e.component}: installed={e.current_version or 'unknown'} "
            f"status={e.status.value} min_required={e.minimum_version or 'n/a'}"
            for e in report.compatibility_matrix
        )
        or "- none detected"
    )

    nodes_block = (
        "\n".join(
            f"- {n.name}: kubelet {n.kubelet_version}, {n.container_runtime}, "
            f"pool={n.node_pool or 'n/a'}, roles={'/'.join(n.roles)}"
            for n in profile.nodes[:20]
        )
        or "- no node inventory"
    )

    schema = json.dumps(LLMAnalysis.model_json_schema(), indent=None)

    return f"""\
## UPGRADE REQUEST
Source: Kubernetes {report.source_version}  →  Target: Kubernetes {report.target_version}
Hop path: {" → ".join(report.version_path)}

## CLUSTER PROFILE (measured)
Distribution: {profile.flavour.value} ({"; ".join(profile.flavour_evidence)})
Upgrade mechanism: {profile.upgrade_mechanism}
Provider-managed components: {", ".join(profile.provider_managed) or "none"}
Nodes ({profile.node_count}):
{nodes_block}
Workloads: {profile.workloads.deployments} deployments, {profile.workloads.statefulsets} \
statefulsets, {profile.workloads.daemonsets} daemonsets, {profile.workloads.cronjobs} cronjobs

## DETERMINISTIC FINDINGS (source of truth — explain, never contradict)
{findings_block}

## COMPATIBILITY MATRIX (computed)
{matrix_block}

## FIXED SCORES (computed by the platform — explain these exact numbers)
Readiness: {report.readiness.score}/100 (cap {report.readiness.cap}\
{f" — {report.readiness.cap_reason}" if report.readiness.cap_reason else ""})
Confidence: {report.readiness.confidence}/100
Verdict: {report.readiness.verdict.value}

## UNKNOWN RISKS (must appear honestly in your narrative)
{chr(10).join("- " + r for r in report.unknown_risks) or "- none"}

## RETRIEVED DOCUMENTS (cite as [DOC n])
{context_text or "(no knowledge base available — rely only on deterministic findings)"}

## YOUR TASK
Produce the JSON object now. Schema:
{schema}

Field guidance:
- executive_summary: 5-8 sentences for an engineering leader: verdict, why, \
the two or three decisive facts, and what happens next.
- risk_narrative: the story of what could go wrong on THIS cluster, ordered \
by severity, citing [DOC n] where documents inform it.
- upgrade_strategy + plan: refine the phase skeleton for this distribution; \
keep one minor per hop; include concrete commands and validation gates.
- downtime: reason from the measured workload/PDB/node facts above.
- additional_findings: only genuinely new, document-grounded items.
- citations_used: every [DOC n] number you cited anywhere.
"""
