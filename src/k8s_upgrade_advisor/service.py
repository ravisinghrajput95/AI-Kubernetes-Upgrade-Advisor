"""Assessment orchestrator — the one function the CLI, API server, and tests
all call.

Pipeline: snapshot → deterministic analysis → (KB retrieval → LLM narrative)
→ report. Retrieval and LLM stages degrade gracefully: no KB means the
prompt says so and confidence drops; no LLM (dry-run / provider=none) means
the deterministic report ships as-is. Deterministic analysis never degrades
— if the snapshot is unusable, the assessment fails loudly instead.

Process-level caches (both thread-safe):
  - KnowledgeStore + HybridRetriever, invalidated when the KB manifest
    changes on disk — loading the corpus and building the BM25 index are
    O(corpus) and must not happen per request.
  - LLM provider, keyed by its settings — the circuit breaker only
    protects anything if it accumulates state across assessments, and the
    pooled HTTP session avoids per-request TLS setup.
"""

from __future__ import annotations

import threading
import time

from .config import LLMSettings, Settings
from .errors import KnowledgeBaseError, LLMError
from .knowledge import KnowledgeStore, select_backend
from .knowledge.store import read_manifest
from .llm import LLMProvider, NullProvider, make_provider, run_llm_analysis
from .models import AssessmentReport, ClusterSnapshot, validate_upgrade_pair
from .observability import get_logger, metrics
from .observability.tracing import span
from .retrieval import ContextBundle, HybridRetriever, build_queries

log = get_logger(__name__)


class _stage:
    """Times a pipeline stage into the per-stage histogram and an OTel span."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        self._span = span(f"assess.{self.name}")
        self._span.__enter__()
        self._started = time.monotonic()
        return self

    def __exit__(self, *exc_info):
        metrics.assessment_stage_seconds.labels(stage=self.name).observe(
            time.monotonic() - self._started
        )
        return self._span.__exit__(*exc_info)


_cache_lock = threading.Lock()
_retriever_cache: dict = {"built_at": None, "kb_dir": None, "retriever": None}
_provider_cache: dict = {"key": None, "provider": None}


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
    metrics.assessments_in_flight.inc()
    try:
        with _stage("deterministic"):
            report = run_deterministic_analysis(snapshot, source, target)

        with _stage("retrieval"):
            bundle = _retrieve(report, settings, source, target)
        report.evidence_metrics.kb_chunks_retrieved = len(bundle.entries)
        report.evidence_metrics.kb_sources = len({e.chunk.doc_key for e in bundle.entries})

        if dry_run or settings.llm.provider == "none":
            report = run_llm_analysis(
                report, bundle.context_text, bundle.citations, provider=NullProvider(), dry_run=True
            )
            outcome = "dry_run"
        else:
            provider = _get_provider(settings.llm)
            try:
                with _stage("llm"):
                    report = run_llm_analysis(
                        report, bundle.context_text, bundle.citations, provider
                    )
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
        metrics.assessments_in_flight.dec()
        metrics.assessments_total.labels(outcome=outcome).inc()
        metrics.assessment_duration_seconds.observe(time.monotonic() - started)


def _get_retriever(settings: Settings) -> HybridRetriever | None:
    """Cached retriever, invalidated when the KB manifest's build timestamp
    or location changes. Returns None when no valid KB exists."""
    manifest = read_manifest(settings.paths.kb_dir)
    if manifest is None:
        return None
    with _cache_lock:
        if (
            _retriever_cache["retriever"] is not None
            and _retriever_cache["built_at"] == manifest.built_at
            and _retriever_cache["kb_dir"] == str(settings.paths.kb_dir)
        ):
            return _retriever_cache["retriever"]

        embedder = select_backend(
            settings.knowledge.embedding_backend, settings.knowledge.embedding_model
        )
        store = KnowledgeStore.load(settings.paths.kb_dir, expected_embedder=embedder.name)
        retriever = HybridRetriever(store, embedder, settings.retrieval)
        _retriever_cache.update(
            built_at=manifest.built_at, kb_dir=str(settings.paths.kb_dir), retriever=retriever
        )
        log.info("retriever_cached", chunks=store.manifest.chunk_count, built_at=manifest.built_at)
        return retriever


def _get_provider(llm_settings: LLMSettings) -> LLMProvider:
    """Process-level provider so the circuit breaker and HTTP session
    persist across assessments."""
    key = (llm_settings.provider, llm_settings.model, llm_settings.base_url)
    with _cache_lock:
        if _provider_cache["provider"] is None or _provider_cache["key"] != key:
            _provider_cache["provider"] = make_provider(llm_settings)
            _provider_cache["key"] = key
        return _provider_cache["provider"]


def _retrieve(report: AssessmentReport, settings: Settings, source, target) -> ContextBundle:
    try:
        retriever = _get_retriever(settings)
    except KnowledgeBaseError as exc:
        log.warning("kb_unavailable", reason=str(exc)[:200])
        retriever = None
    if retriever is None:
        report.unknown_risks.append(
            "Knowledge base unavailable — recommendations lack document grounding "
            "(run 'k8s-upgrade-advisor build-kb')."
        )
        return ContextBundle()

    queries = build_queries(source, target, report.profile)
    allowed = {source.minor_str, *[v.minor_str for v in source.minors_until(target)]}
    installed = {component.key for component in report.profile.components}
    return retriever.retrieve(queries, allowed_versions=allowed, installed_components=installed)
