# ADR-0001: Deterministic analysis first, LLM reasoning second

**Status:** accepted · **Date:** 2026-07-18

## Context

v1 of this tool sent kubectl output + retrieved docs to an LLM and asked for the
whole assessment — findings, scores, verdict — then regex-parsed the prose. Two
structural failures: (a) compatibility "facts" could be hallucinated with no way
to tell, and (b) a phrasing change in model output silently broke report parsing.
The set of removed Kubernetes APIs per version is finite and public; asking a
language model to rediscover it per assessment is strictly worse than a table.

## Decision

Split the system along trust lines. Deterministic engines (static lifecycle
tables + cluster evidence) produce every finding, compatibility verdict, and
score. The LLM receives those as fixed inputs and produces only narrative,
sequencing, and plan refinement in a schema-validated `LLMAnalysis`. The merge
step enforces the boundary post-hoc: model findings are non-blocking and
severity-capped, scores are untouchable, citations are validated against the
retrieved set.

## Consequences

- The platform works with **no LLM at all** (dry-run / provider=none) — the
  deterministic report is complete and CI-gateable.
- Hallucination and prompt-injection blast radius shrinks to narrative quality.
- Cost: static tables must be maintained per Kubernetes release (documented
  release checklist in development.md). This is deliberate — that maintenance
  *is* the product's accuracy.
