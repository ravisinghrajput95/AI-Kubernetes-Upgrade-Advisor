# ADR-0004: Pluggable embedding backends with graceful degradation

**Status:** accepted · **Date:** 2026-07-18

## Context

`sentence-transformers` drags in torch (~GB-scale, platform-sensitive wheels).
Hard-requiring it makes the CLI un-installable in constrained environments (CI
runners, air-gapped boxes, new Python versions before wheels land) and makes the
test suite download models.

## Decision

One `EmbeddingBackend` protocol, two implementations:
`SentenceTransformerEmbedder` (optional extra `[rag]`) and a deterministic
feature-hashed token/bigram embedder (numpy-only) used as automatic fallback and
as the test backend. The KB manifest records which backend built the index;
loading with a mismatched backend is refused (mixed vector spaces fail loudly).
FAISS is likewise an optional accelerator over a numpy brute-force search.

## Consequences

- Core platform + full test suite run anywhere numpy runs, in <1s, offline.
- Retrieval quality degrades gracefully (hash+BM25 ≈ good lexical retrieval)
  instead of the feature disappearing.
- The manifest check turns a silent-garbage failure mode into a clear
  "rebuild the KB" error.
