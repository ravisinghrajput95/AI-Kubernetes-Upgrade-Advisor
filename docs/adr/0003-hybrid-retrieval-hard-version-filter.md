# ADR-0003: Hybrid retrieval with a hard version metadata filter

**Status:** accepted · **Date:** 2026-07-18

## Context

Two observed retrieval failures: (1) dense embeddings cannot reliably
distinguish `…/v1beta2` from `…/v1beta3` or "1.28" from "1.34" — release-note
chunks for irrelevant versions surfaced in assessments; (2) exact-token queries
(API groups, version strings) are precisely where semantic similarity is
weakest. v1 mitigated (1) by fully-qualifying query text, which is
probabilistic.

## Decision

- Stamp `k8s_version` on version-specific chunks at ingestion; apply a **hard
  filter** at retrieval — out-of-window chunks are dropped regardless of score.
- Run two arms per query — BM25 (owned, ~70 lines, tokenizer preserves API-group
  tokens) and dense cosine — fused with Reciprocal Rank Fusion, then MMR
  diversification.

## Consequences

- The "wrong-version chunk" class of grounding error is structurally eliminated,
  not just made less likely.
- BM25 keeps retrieval useful even on the fallback hash embedder.
- RRF avoids score-calibration between arms; one constant (k=60) to tune.
