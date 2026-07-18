"""Optional OpenTelemetry spans for pipeline internals.

The FastAPI auto-instrumentation (api/app.py) gives one span per request;
these helpers add child spans for the assessment stages so traces show
where time went (deterministic vs retrieval vs LLM). Everything no-ops at
near-zero cost when the otel extra isn't installed or tracing is disabled —
callers never need to know.
"""

from __future__ import annotations

from contextlib import contextmanager

try:
    from opentelemetry import trace

    _tracer = trace.get_tracer("k8s_upgrade_advisor")
except ImportError:  # otel extra not installed
    _tracer = None


@contextmanager
def span(name: str, **attributes):
    """Child span when otel is available, no-op otherwise."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(key, value)
        yield current
