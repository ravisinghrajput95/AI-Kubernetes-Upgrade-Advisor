"""Optional cross-encoder reranking stage.

RRF fusion ranks by *rank position* in each retrieval arm; it never scores
query-document relevance directly. A cross-encoder reads (query, chunk)
pairs jointly and is markedly better at precision@k — at the cost of a
model forward pass per candidate, which is why it runs only on the fused
top-N, not the corpus.

Same degradation contract as embeddings: the null reranker is a no-op, the
cross-encoder is an optional heavy dependency (``[rag]`` extra), and "auto"
quietly falls back when it is absent.
"""

from __future__ import annotations

from typing import Protocol

from ..observability import get_logger

log = get_logger(__name__)


class Reranker(Protocol):
    name: str

    def scores(self, query: str, texts: list[str]) -> list[float] | None:
        """Relevance score per text; None means 'stage unavailable, skip'."""
        ...


class NullReranker:
    name = "none"

    def scores(self, query: str, texts: list[str]) -> list[float] | None:
        return None


class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        from sentence_transformers import CrossEncoder  # heavy import, deferred

        self._model = CrossEncoder(model_name)
        self.name = f"cross-encoder/{model_name.rsplit('/', 1)[-1]}"

    def scores(self, query: str, texts: list[str]) -> list[float] | None:
        if not texts:
            return []
        return [float(s) for s in self._model.predict([(query, t) for t in texts])]


def select_reranker(preference: str = "none") -> Reranker:
    if preference == "none":
        return NullReranker()
    try:
        return CrossEncoderReranker()
    except ImportError:
        if preference == "cross-encoder":
            raise
        log.warning(
            "rerank_fallback",
            reason="sentence-transformers not installed",
            hint="pip install 'k8s-upgrade-advisor[rag]' to enable cross-encoder reranking",
        )
        return NullReranker()
