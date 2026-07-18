# ADR-0002: Schema-validated LLM output; renderers never parse prose

**Status:** accepted · **Date:** 2026-07-18

## Context

Report artifacts (Markdown/HTML/JSON) previously extracted verdicts and scores
from free-form LLM markdown with regexes — fragile against any model or prompt
change, and unfixable silently (a missed regex = wrong verdict badge).

## Decision

The LLM must return one JSON object matching `LLMAnalysis` (pydantic schema
embedded in the prompt, `response_format: json_object` at the API level).
Validation failures get exactly one repair round-trip carrying the validation
errors; a second failure degrades to the deterministic report. All renderers are
pure functions of the typed `AssessmentReport`.

## Consequences

- Markdown, HTML, and JSON artifacts cannot drift from each other.
- Model/provider swaps are renderer-invisible.
- The JSON report is a stable machine interface for CI gating and diffing.
- Cost: schema evolution requires versioning (`schema_version` field reserved).
