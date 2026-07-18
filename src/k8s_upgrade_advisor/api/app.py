"""Backend API — FastAPI application.

Endpoints:
  GET  /healthz | /livez      liveness (process up)
  GET  /readyz                readiness (deps checked: KB presence reported)
  GET  /metrics               Prometheus exposition
  POST /api/v1/assessments    run an assessment on an uploaded snapshot
  GET  /api/v1/assessments    recent assessment summaries
  GET  /api/v1/assessments/{id}        full report JSON
  GET  /api/v1/assessments/{id}/html   rendered HTML report
  GET  /api/v1/assessments/{id}/markdown  markdown report
  GET  /                      web UI

Assessments run synchronously in FastAPI's worker threadpool; an in-memory
ring buffer keeps the last N reports (the JSON/MD/HTML artifacts on disk are
the durable copies).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .. import __version__
from ..config import Settings, get_settings
from ..errors import AdvisorError, ConfigurationError
from ..knowledge.store import KnowledgeStore
from ..models import AssessmentReport
from ..observability import configure_logging, get_logger, metrics
from ..reporting import render_html, render_markdown, save_reports
from ..service import assess
from .schemas import AssessmentSummary, AssessRequest, HealthResponse

log = get_logger(__name__)

_FRONTEND = (
    __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
    / "frontend"
    / "index.html"
)
_MAX_STORED = 50


class _ReportStore:
    """Bounded, thread-safe, newest-last mapping of recent reports."""

    def __init__(self, limit: int = _MAX_STORED) -> None:
        self._reports: OrderedDict[str, AssessmentReport] = OrderedDict()
        self._lock = threading.Lock()
        self._limit = limit

    def add(self, report: AssessmentReport) -> None:
        with self._lock:
            self._reports[report.id] = report
            while len(self._reports) > self._limit:
                self._reports.popitem(last=False)

    def get(self, report_id: str) -> AssessmentReport | None:
        with self._lock:
            return self._reports.get(report_id)

    def list(self) -> list[AssessmentReport]:
        with self._lock:
            return list(reversed(self._reports.values()))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.observability.log_level, settings.observability.log_json)

    app = FastAPI(
        title="k8s-upgrade-advisor",
        version=__version__,
        description="AI Kubernetes upgrade intelligence platform",
    )
    store = _ReportStore()

    if settings.observability.otel_enabled:
        _try_enable_otel(app, settings)

    # ── Observability middleware ─────────────────────────────────────────
    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        elapsed = time.monotonic() - started
        metrics.http_requests_total.labels(
            method=request.method, path=path, status=str(response.status_code)
        ).inc()
        metrics.http_request_seconds.labels(method=request.method, path=path).observe(elapsed)
        if settings.server.request_log and not path.startswith(("/healthz", "/livez", "/metrics")):
            log.info(
                "http_request",
                method=request.method,
                path=path,
                status=response.status_code,
                ms=int(elapsed * 1000),
            )
        return response

    # ── Health ───────────────────────────────────────────────────────────
    @app.get("/healthz", response_model=HealthResponse)
    @app.get("/livez", response_model=HealthResponse, include_in_schema=False)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/readyz", response_model=HealthResponse)
    def readyz() -> HealthResponse:
        kb_loaded, kb_chunks, kb_age = False, 0, None
        try:
            kb = KnowledgeStore.load(settings.paths.kb_dir)
            kb_loaded, kb_chunks = True, kb.manifest.chunk_count
            kb_age = round(kb.manifest.age_days, 1)
        except AdvisorError:
            pass  # KB is optional; readiness reports its absence, doesn't fail on it
        return HealthResponse(
            status="ok",
            version=__version__,
            kb_loaded=kb_loaded,
            kb_chunks=kb_chunks,
            kb_age_days=kb_age,
        )

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    # ── Assessments ──────────────────────────────────────────────────────
    @app.post("/api/v1/assessments", response_model=AssessmentReport)
    def create_assessment(request: AssessRequest) -> AssessmentReport:
        try:
            report = assess(
                request.snapshot,
                request.source_version,
                request.target_version,
                settings,
                dry_run=request.dry_run,
            )
        except ConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AdvisorError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        store.add(report)
        save_reports(report, settings.paths.reports_dir)
        return report

    @app.get("/api/v1/assessments", response_model=list[AssessmentSummary])
    def list_assessments() -> list[AssessmentSummary]:
        return [
            AssessmentSummary(
                id=r.id,
                created_at=r.created_at.isoformat(),
                source_version=r.source_version,
                target_version=r.target_version,
                verdict=r.readiness.verdict,
                readiness=r.readiness.score,
                confidence=r.readiness.confidence,
                findings=len(r.findings),
                blocking=len(r.blocking_findings),
            )
            for r in store.list()
        ]

    @app.get("/api/v1/assessments/{report_id}", response_model=AssessmentReport)
    def get_assessment(report_id: str) -> AssessmentReport:
        return _must_get(store, report_id)

    @app.get("/api/v1/assessments/{report_id}/html", response_class=HTMLResponse)
    def get_assessment_html(report_id: str) -> HTMLResponse:
        return HTMLResponse(render_html(_must_get(store, report_id)))

    @app.get("/api/v1/assessments/{report_id}/markdown", response_class=PlainTextResponse)
    def get_assessment_markdown(report_id: str) -> PlainTextResponse:
        return PlainTextResponse(render_markdown(_must_get(store, report_id)))

    # ── Frontend ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        if not _FRONTEND.is_file():
            raise HTTPException(status_code=404, detail="frontend not bundled")
        return FileResponse(_FRONTEND, media_type="text/html")

    return app


def _must_get(store: _ReportStore, report_id: str) -> AssessmentReport:
    report = store.get(report_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"assessment {report_id} not found "
            "(in-memory store holds the most recent runs; "
            "older reports live in the reports/ directory)",
        )
    return report


def _try_enable_otel(app: FastAPI, settings: Settings) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "k8s-upgrade-advisor"}))
        exporter = OTLPSpanExporter(endpoint=settings.observability.otel_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        log.info("otel_enabled", endpoint=settings.observability.otel_endpoint)
    except ImportError:
        log.warning(
            "otel_unavailable", hint="pip install 'k8s-upgrade-advisor[otel]' to enable tracing"
        )


app = create_app  # uvicorn factory: uvicorn "k8s_upgrade_advisor.api.app:app" --factory
