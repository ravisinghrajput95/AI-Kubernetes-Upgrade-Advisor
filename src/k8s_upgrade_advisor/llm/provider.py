"""LLM provider abstraction.

One small interface (:class:`LLMProvider`) so the advisor is provider-
agnostic; the OpenAI implementation wraps the call in the platform's retry
and circuit-breaker primitives and records metrics. ``NullProvider`` backs
``--dry-run`` and llm.provider=none deployments (deterministic-only mode).
"""

from __future__ import annotations

import json
import os
import time
from typing import Protocol

import requests

from ..config import LLMSettings
from ..errors import LLMError
from ..observability import get_logger, metrics
from ..resilience import CircuitBreaker, retry

log = get_logger(__name__)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str) -> str:
        """Return the raw completion text of a JSON-mode chat call."""
        ...


class NullProvider:
    name = "none"
    model = "none"

    def complete_json(self, system: str, user: str) -> str:
        raise LLMError("no LLM provider configured (llm.provider=none)")


class _RetryableHTTP(Exception):
    """HTTP 429/5xx — worth retrying with backoff."""


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.model = settings.model
        key = (
            settings.api_key.get_secret_value()
            if settings.api_key
            else os.environ.get("OPENAI_API_KEY", "")
        )
        if not key:
            raise LLMError(
                "OPENAI_API_KEY is not set (or llm.api_key in config) — "
                "use --dry-run for a deterministic-only assessment"
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
        )
        self._breaker = CircuitBreaker(
            "openai",
            failure_threshold=settings.circuit_failure_threshold,
            reset_timeout=settings.circuit_reset_seconds,
        )
        self.last_usage: dict[str, int] | None = None

    def complete_json(self, system: str, user: str) -> str:
        started = time.monotonic()
        try:
            text = self._breaker.call(self._call_with_retry, system, user)
        except LLMError:
            metrics.llm_requests_total.labels(provider=self.name, status="circuit_open").inc()
            raise
        except Exception as exc:
            metrics.llm_requests_total.labels(provider=self.name, status="error").inc()
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        duration = time.monotonic() - started
        metrics.llm_requests_total.labels(provider=self.name, status="ok").inc()
        metrics.llm_request_seconds.observe(duration)
        log.info("llm_call_ok", model=self.model, seconds=round(duration, 1), chars=len(text))
        return text

    def _call_with_retry(self, system: str, user: str) -> str:
        @retry(
            attempts=self.settings.max_retries + 1,
            base_delay=self.settings.retry_base_delay,
            max_delay=30.0,
            retry_on=(_RetryableHTTP, requests.ConnectionError, requests.Timeout),
            on_retry=lambda n, exc: log.warning("llm_retry", attempt=n, error=str(exc)[:200]),
        )
        def _once() -> str:
            resp = self._session.post(
                f"{self.settings.base_url}/chat/completions",
                json={
                    "model": self.settings.model,
                    "temperature": self.settings.temperature,
                    "max_tokens": self.settings.max_output_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self.settings.timeout_seconds,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 30))
                raise _RetryableHTTP(f"HTTP {resp.status_code}: {resp.text[:300]}")
            if resp.status_code != 200:
                raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                log.warning("llm_truncated", hint="raise llm.max_output_tokens")
            usage = data.get("usage") or {}
            if usage:
                prompt_tokens = int(usage.get("prompt_tokens", 0))
                completion_tokens = int(usage.get("completion_tokens", 0))
                cost = (
                    prompt_tokens / 1000 * self.settings.prompt_cost_per_1k
                    + completion_tokens / 1000 * self.settings.completion_cost_per_1k
                )
                self.last_usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": cost,
                }
                for direction in ("prompt", "completion"):
                    metrics.llm_tokens_total.labels(provider=self.name, direction=direction).inc(
                        self.last_usage[f"{direction}_tokens"]
                    )
                if cost > 0:
                    metrics.llm_cost_usd_total.labels(provider=self.name).inc(cost)
            return choice["message"]["content"]

        return _once()


def make_provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "none":
        return NullProvider()
    return OpenAIProvider(settings)


def parse_json_response(text: str) -> dict:
    """JSON-mode responses are documents, but be tolerant of stray fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned)
