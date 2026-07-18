"""Central configuration.

All tunables live here as pydantic-settings models so they are:
  - overridable via environment (``K8S_ADVISOR_LLM__MODEL=gpt-4o``),
  - validated at startup (fail fast with a clear message),
  - injectable in tests (construct Settings() with overrides, no globals).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigurationError

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class PathsSettings(BaseModel):
    kb_dir: Path = _REPO_ROOT / "kb"
    reports_dir: Path = _REPO_ROOT / "reports"
    # Keep the newest N assessments on disk (md+html+json per assessment);
    # 0 disables pruning. Reports are the durable tier for the API's
    # disk-backed retrieval, so this is the platform's retention policy.
    reports_keep: int = Field(200, ge=0)

    @property
    def raw_docs_dir(self) -> Path:
        return self.kb_dir / "raw"


class KnowledgeSettings(BaseModel):
    """Knowledge-base build parameters. Recorded in the KB manifest;
    a mismatch between manifest and settings forces a rebuild rather than
    silently mixing embedding spaces."""

    embedding_backend: Literal["auto", "sentence-transformers", "hash"] = "auto"
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_chars: int = Field(1400, ge=200, le=8000)
    chunk_overlap: int = Field(200, ge=0)
    fetch_timeout_seconds: float = Field(20.0, gt=0)
    fetch_retries: int = Field(3, ge=0, le=10)
    cache_max_age_days: int = Field(30, ge=1, description="KB staleness warning threshold")

    @model_validator(mode="after")
    def _overlap_lt_chunk(self) -> KnowledgeSettings:
        if self.chunk_overlap >= self.chunk_chars:
            raise ValueError("chunk_overlap must be smaller than chunk_chars")
        return self


class RetrievalSettings(BaseModel):
    top_k: int = Field(24, ge=1, le=200, description="chunks handed to the LLM")
    # Cross-encoder rerank of fused candidates. "none" (default) skips it;
    # "auto" uses it when sentence-transformers is installed; "cross-encoder"
    # requires it. Same graceful-degradation contract as embeddings.
    rerank: Literal["none", "auto", "cross-encoder"] = "none"
    rerank_candidates: int = Field(30, ge=1, le=200)
    dense_candidates: int = Field(50, ge=1)
    lexical_candidates: int = Field(50, ge=1)
    rrf_k: int = Field(60, ge=1, description="reciprocal-rank-fusion constant")
    max_context_chars: int = Field(60_000, ge=4_000)
    mmr_lambda: float = Field(0.7, ge=0.0, le=1.0, description="relevance vs diversity")


class LLMSettings(BaseModel):
    provider: Literal["openai", "none"] = "openai"
    model: str = "gpt-4o"
    api_key: SecretStr | None = None  # falls back to OPENAI_API_KEY env var
    base_url: str = "https://api.openai.com/v1"
    max_output_tokens: int = Field(8000, ge=256)
    timeout_seconds: float = Field(300.0, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    retry_base_delay: float = Field(1.0, gt=0)
    circuit_failure_threshold: int = Field(5, ge=1)
    circuit_reset_seconds: float = Field(60.0, gt=0)
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    # Optional cost accounting (USD per 1k tokens). 0 disables estimation —
    # prices change too often to hardcode.
    prompt_cost_per_1k: float = Field(0.0, ge=0.0)
    completion_cost_per_1k: float = Field(0.0, ge=0.0)

    @model_validator(mode="after")
    def _https_only(self) -> LLMSettings:
        if not self.base_url.startswith("https://"):
            raise ValueError("llm.base_url must be https://")
        return self


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)
    max_snapshot_bytes: int = Field(20 * 1024 * 1024, ge=1024)
    request_log: bool = True
    # Assessments are CPU/LLM-bound and run in the worker threadpool; beyond
    # this many in flight the API sheds load with 503 + Retry-After instead
    # of queueing until the pool starves.
    max_concurrent_assessments: int = Field(4, ge=1, le=64)


class ObservabilitySettings(BaseModel):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    otel_enabled: bool = False
    otel_endpoint: str | None = None


class Settings(BaseSettings):
    """Root settings object. Nested overrides use ``__`` as delimiter, e.g.
    ``K8S_ADVISOR_RETRIEVAL__TOP_K=32``."""

    model_config = SettingsConfigDict(
        env_prefix="K8S_ADVISOR_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    paths: PathsSettings = Field(default_factory=PathsSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValueError as exc:  # pydantic ValidationError subclasses ValueError
        raise ConfigurationError(f"invalid configuration: {exc}") from exc
