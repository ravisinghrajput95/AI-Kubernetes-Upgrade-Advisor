"""Pluggable embedding backends.

Two implementations behind one interface:

  - :class:`SentenceTransformerEmbedder` — real semantic embeddings
    (all-MiniLM-L6-v2 by default). Optional heavy dependency.
  - :class:`HashingEmbedder` — deterministic feature-hashed token/bigram
    vectors. No dependencies beyond numpy, identical results on every
    machine. It is the degradation path *and* the test backend: retrieval
    still works (lexical-ish), just with weaker semantics — and the hybrid
    retriever's BM25 arm covers much of the gap.

The KB manifest records which backend built the index; loading with a
different backend is refused rather than silently mixing vector spaces.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import numpy as np

from ..observability import get_logger

log = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9./\-]{1,40}")


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Feature hashing over tokens + bigrams, L2-normalised, float32."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _tokens(self, text: str) -> list[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        return tokens + bigrams

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            counts: dict[int, float] = {}
            for token in self._tokens(text):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                counts[index] = counts.get(index, 0.0) + sign
            for index, value in counts.items():
                vectors[row, index] = value * (1.0 + math.log1p(abs(value)))
            norm = np.linalg.norm(vectors[row])
            if norm > 0:
                vectors[row] /= norm
        return vectors


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, deferred

        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers/{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(vectors, dtype=np.float32)


def select_backend(
    preference: str = "auto", model_name: str = "all-MiniLM-L6-v2"
) -> EmbeddingBackend:
    if preference == "hash":
        return HashingEmbedder()
    if preference in ("auto", "sentence-transformers"):
        try:
            return SentenceTransformerEmbedder(model_name)
        except ImportError:
            if preference == "sentence-transformers":
                raise
            log.warning(
                "embedding_fallback",
                reason="sentence-transformers not installed",
                backend="hashing",
                hint="pip install 'k8s-upgrade-advisor[rag]' for semantic embeddings",
            )
            return HashingEmbedder()
    raise ValueError(f"unknown embedding backend '{preference}'")
