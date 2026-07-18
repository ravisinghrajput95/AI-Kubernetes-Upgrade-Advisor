"""Exception hierarchy for the platform.

Every subsystem raises a subclass of :class:`AdvisorError` so callers (CLI,
API) can distinguish "our" failures from programming errors, map them to exit
codes / HTTP statuses, and log them with consistent structure.
"""

from __future__ import annotations


class AdvisorError(Exception):
    """Base class for all expected platform failures."""

    exit_code = 1


class ConfigurationError(AdvisorError):
    """Invalid or missing configuration."""

    exit_code = 78  # EX_CONFIG


class CollectionError(AdvisorError):
    """Cluster or document collection failed."""

    exit_code = 69  # EX_UNAVAILABLE


class KnowledgeBaseError(AdvisorError):
    """Knowledge base build/load failure (missing index, manifest mismatch)."""


class RetrievalError(AdvisorError):
    """Retrieval pipeline failure."""


class LLMError(AdvisorError):
    """LLM provider failure after retries were exhausted."""

    exit_code = 69


class LLMResponseInvalid(LLMError):
    """The model returned output that failed schema validation."""


class CircuitOpenError(LLMError):
    """Circuit breaker is open; the dependency is considered unhealthy."""
