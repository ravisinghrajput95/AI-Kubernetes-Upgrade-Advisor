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
        self.assessment_stage_seconds = Histogram(
            "advisor_assessment_stage_seconds",
            "Per-stage assessment latency",
            ["stage"],  # deterministic | retrieval | llm
            buckets=(0.1, 0.5, 1, 5, 15, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.assessments_in_flight = Gauge(
            "advisor_assessments_in_flight",
            "Assessments currently executing",
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
        self.llm_tokens_total = Counter(
            "advisor_llm_tokens_total",
            "LLM tokens consumed, by direction (prompt/completion)",
            ["provider", "direction"],
            registry=self.registry,
        )
        self.llm_cost_usd_total = Counter(
            "advisor_llm_cost_usd_total",
            "Estimated LLM spend in USD (requires configured token prices)",
            ["provider"],
            registry=self.registry,
        )
        self.llm_grounding_ratio = Histogram(
            "advisor_llm_grounding_ratio",
            "Fraction of narrative sentences carrying document citations",
            buckets=(0.1, 0.25, 0.5, 0.75, 0.9, 1.0),
            registry=self.registry,
        )
        self.assessment_cache_total = Counter(
            "advisor_assessment_cache_total",
            "Idempotency cache lookups for assessment submissions",
            ["result"],  # hit | miss
            registry=self.registry,
        )
        self.build_info = Gauge(
            "advisor_build_info",
            "Build/version info (value is always 1)",
            ["version"],
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
