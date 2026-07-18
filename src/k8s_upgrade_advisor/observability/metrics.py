"""Prometheus metrics for the platform.

A single module-level :class:`Metrics` instance is shared by the CLI and the
API server. The API exposes it at ``/metrics``; CLI runs simply don't scrape
it. Metric names follow the Prometheus naming guide
(unit-suffixed, ``_total`` for counters).
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.assessments_total = Counter(
            "advisor_assessments_total",
            "Assessments run, by outcome",
            ["outcome"],  # completed | failed | dry_run
            registry=self.registry,
        )
        self.assessment_duration_seconds = Histogram(
            "advisor_assessment_duration_seconds",
            "End-to-end assessment latency",
            buckets=(1, 5, 15, 30, 60, 120, 300, 600),
            registry=self.registry,
        )
        self.llm_requests_total = Counter(
            "advisor_llm_requests_total",
            "LLM API calls, by provider and status",
            ["provider", "status"],  # ok | error | circuit_open
            registry=self.registry,
        )
        self.llm_request_seconds = Histogram(
            "advisor_llm_request_seconds",
            "LLM API call latency",
            buckets=(1, 5, 15, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.retrieval_seconds = Histogram(
            "advisor_retrieval_seconds",
            "Hybrid retrieval latency per assessment",
            registry=self.registry,
        )
        self.kb_chunks = Gauge(
            "advisor_kb_chunks",
            "Chunks in the loaded knowledge base",
            registry=self.registry,
        )
        self.kb_build_timestamp = Gauge(
            "advisor_kb_build_timestamp_seconds",
            "Unix time the knowledge base was built (staleness alerting)",
            registry=self.registry,
        )
        self.doc_fetches_total = Counter(
            "advisor_doc_fetches_total",
            "Document fetches during KB collection",
            ["status"],  # ok | cached | error
            registry=self.registry,
        )
        self.http_requests_total = Counter(
            "advisor_http_requests_total",
            "API server requests",
            ["method", "path", "status"],
            registry=self.registry,
        )
        self.http_request_seconds = Histogram(
            "advisor_http_request_seconds",
            "API server request latency",
            ["method", "path"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


metrics = Metrics()
