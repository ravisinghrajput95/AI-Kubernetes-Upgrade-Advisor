"""API request/response schemas (the report itself is the domain model)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import ClusterSnapshot, Verdict


class AssessRequest(BaseModel):
    source_version: str = Field(examples=["1.28"])
    target_version: str = Field(examples=["1.31"])
    snapshot: ClusterSnapshot
    dry_run: bool = False


class AssessmentSummary(BaseModel):
    id: str
    created_at: str
    source_version: str
    target_version: str
    verdict: Verdict
    readiness: int
    confidence: int
    findings: int
    blocking: int


class HealthResponse(BaseModel):
    status: str
    version: str
    kb_loaded: bool = False
    kb_chunks: int = 0
    kb_age_days: float | None = None
