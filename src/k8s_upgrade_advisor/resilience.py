"""Reliability primitives: retry with exponential backoff + jitter, and a
thread-safe circuit breaker.

Kept dependency-free on purpose — these are small enough that owning them
beats pulling in a library, and they are shared by the LLM client, the
document fetcher, and the embedding backends.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import ParamSpec, TypeVar

from .errors import CircuitOpenError

P = ParamSpec("P")
T = TypeVar("T")


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Exponential backoff with full jitter (AWS-style).

    ``attempts`` is the total number of tries, not the number of retries.
    ``on_retry(try_number, exc)`` is called before each sleep — used for
    structured logging and metrics at call sites.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt == attempts:
                        break
                    if on_retry is not None:
                        on_retry(attempt, exc)
                    cap = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    sleep(random.uniform(0, cap))
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _CircuitStats:
    consecutive_failures: int = 0
    opened_at: float = 0.0


class CircuitBreaker:
    """Classic three-state circuit breaker.

    CLOSED → (failure_threshold consecutive failures) → OPEN
    OPEN   → (reset_timeout elapsed)                  → HALF_OPEN
    HALF_OPEN → one probe call; success → CLOSED, failure → OPEN

    Calls while OPEN raise :class:`CircuitOpenError` immediately so a dead
    dependency fails fast instead of stacking timeouts.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._stats = _CircuitStats()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._clock() - self._stats.opened_at >= self.reset_timeout
        ):
            self._state = CircuitState.HALF_OPEN

    def call(self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        with self._lock:
            self._maybe_half_open()
            if self._state is CircuitState.OPEN:
                remaining = self.reset_timeout - (self._clock() - self._stats.opened_at)
                raise CircuitOpenError(
                    f"circuit '{self.name}' open; retry in {max(remaining, 0):.0f}s"
                )
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    def _record_success(self) -> None:
        with self._lock:
            self._stats.consecutive_failures = 0
            self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        with self._lock:
            self._stats.consecutive_failures += 1
            if (
                self._state is CircuitState.HALF_OPEN
                or self._stats.consecutive_failures >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._stats.opened_at = self._clock()
