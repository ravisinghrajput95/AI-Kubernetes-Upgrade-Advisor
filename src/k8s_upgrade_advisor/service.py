"""Assessment orchestrator — the one function the CLI, API server, and tests
all call.

Pipeline: snapshot → deterministic analysis → (KB retrieval → LLM narrative)
→ report. Retrieval and LLM stages degrade gracefully: no KB means the
prompt says so and confidence drops; no LLM (dry-run / provider=none) means
the deterministic report ships as-is. Deterministic analysis never degrades
— if the snapshot is unusable, the assessment fails loudly instead.
"""

from __future__ import annotations

import time

from .config import Settings
from .errors import KnowledgeBaseError, LLMError
from .knowledge import KnowledgeStore, select_backend
from .llm import make_provider, run_llm_analysis
from .models import AssessmentReport, ClusterSnapshot, validate_upgrade_pair
from .observability import get_logger, metrics
from .retrieval import ContextBundle, HybridRetriever, build_queries

log = get_logger(__name__)


def assess(
    snapshot: ClusterSnapshot,
    source_version: str,
    target_version: str,
    settings: Settings,
    dry_run: bool = False,
) -> AssessmentReport:
    from .analysis import run_deterministic_analysis  # local import: keeps module import light

    started = time.monotonic()
    source, target = validate_upgrade_pair(source_version, target_version)

    outcome = "failed"
    try:
        report = run_deterministic_analysis(snapshot, source, target)

        bundle = _retrieve(report, settings, source, target)
        report.evidence_metrics.kb_chunks_retrieved = len(bundle.entries)
        report.evidence_metrics.kb_sources = len({e.chunk.doc_key for e in bundle.entries})

        if dry_run or settings.llm.provider == "none":
            report = run_llm_analysis(
                report, bundle.context_text, bundle.citations, provider=_null(), dry_run=True
            )
            outcome = "dry_run"
        else:
            provider = make_provider(settings.llm)
            try:
                report = run_llm_analysis(report, bundle.context_text, bundle.citations, provider)
            except LLMError as exc:
                # Graceful degradation: deterministic report still ships.
                log.error("llm_stage_failed", error=str(exc)[:300])
                report.executive_summary = (
                    f"[degraded] LLM narrative unavailable ({exc}). Deterministic verdict: "
                    f"{report.readiness.verdict.value}, readiness {report.readiness.score}/100, "
                    f"{len(report.blocking_findings)} blocking finding(s)."
                )
            outcome = "completed"
        return report
    finally:
        metrics.assessments_total.labels(outcome=outcome).inc()
        metrics.assessment_duration_seconds.observe(time.monotonic() - started)


def _retrieve(report: AssessmentReport, settings: Settings, source, target) -> ContextBundle:
    try:
        embedder = select_backend(
            settings.knowledge.embedding_backend, settings.knowledge.embedding_model
        )
        store = KnowledgeStore.load(settings.paths.kb_dir, expected_embedder=embedder.name)
    except KnowledgeBaseError as exc:
        log.warning("kb_unavailable", reason=str(exc)[:200])
        report.unknown_risks.append(
            "Knowledge base unavailable — recommendations lack document grounding "
            "(run 'k8s-upgrade-advisor build-kb')."
        )
        return ContextBundle()

    retriever = HybridRetriever(store, embedder, settings.retrieval)
    queries = build_queries(source, target, report.profile)
    allowed = {source.minor_str, *[v.minor_str for v in source.minors_until(target)]}
    installed = {component.key for component in report.profile.components}
    return retriever.retrieve(queries, allowed_versions=allowed, installed_components=installed)


def _null():
    from .llm import NullProvider

    return NullProvider()
