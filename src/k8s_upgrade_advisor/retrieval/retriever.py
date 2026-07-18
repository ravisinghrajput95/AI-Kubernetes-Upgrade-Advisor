"""Hybrid retrieval pipeline.

Per assessment:
  1. Query generation — version-qualified questions for every hop plus
     component-specific questions for what is actually installed.
  2. Two retrieval arms per query: dense (embedding cosine) and lexical
     (BM25). BM25 catches exact tokens (API groups, version numbers) that
     embeddings blur; dense catches paraphrases BM25 misses.
  3. Reciprocal Rank Fusion across all (query, arm) rankings.
  4. Hard metadata filter — a chunk stamped with a k8s version outside the
     upgrade window is dropped no matter how similar it looks. This is the
     structural fix for "1.28 release notes surfacing in a 1.34 assessment";
     query phrasing alone is probabilistic, the filter is not.
  5. MMR diversification so twenty near-identical CHANGELOG chunks don't
     crowd out operator docs.
  6. Context assembly under a character budget with stable [DOC n] refs the
     LLM must cite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config import RetrievalSettings
from ..knowledge.chunker import Chunk
from ..knowledge.embeddings import EmbeddingBackend
from ..knowledge.store import KnowledgeStore
from ..models import Citation, ClusterProfileSummary, KubeVersion
from ..observability import get_logger, metrics
from .bm25 import BM25

log = get_logger(__name__)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float
    ref: int = 0  # [DOC n] number, assigned at assembly


@dataclass
class ContextBundle:
    entries: list[RetrievedChunk] = field(default_factory=list)
    context_text: str = ""

    @property
    def citations(self) -> list[Citation]:
        return [
            Citation(
                ref=e.ref,
                title=e.chunk.display_source,
                url=e.chunk.url,
                source=e.chunk.kind,
                k8s_version=e.chunk.k8s_version,
            )
            for e in self.entries
        ]


def build_queries(
    source: KubeVersion, target: KubeVersion, profile: ClusterProfileSummary
) -> list[str]:
    queries: list[str] = []
    for hop in source.minors_until(target):
        v = hop.minor_str
        queries.append(f"Kubernetes {v} release notes breaking changes deprecations removed APIs")
        queries.append(f"Kubernetes {v} urgent upgrade notes changes before upgrading")
    queries.append(f"upgrade Kubernetes {source.minor_str} to {target.minor_str} steps sequence")
    queries.append("Kubernetes version skew policy kubelet kube-proxy supported versions")

    flavour = profile.flavour.value
    if profile.flavour.is_managed or flavour in ("openshift", "rke2", "k3s"):
        queries.append(
            f"{flavour} upgrade cluster kubernetes {target.minor_str} procedure node pools"
        )

    for component in profile.components:
        version = f" {component.version}" if component.version else ""
        queries.append(
            f"{component.display_name}{version} kubernetes {target.minor_str} "
            "compatibility supported versions upgrade"
        )
    return queries


class HybridRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: EmbeddingBackend,
        settings: RetrievalSettings,
        reranker=None,
    ) -> None:
        if embedder.name != store.manifest.embedder:
            # Store.load() also guards this; re-check because retriever can be
            # constructed with an arbitrary backend in tests.
            log.warning("embedder_mismatch", store=store.manifest.embedder, runtime=embedder.name)
        self.store = store
        self.embedder = embedder
        self.settings = settings
        self.reranker = reranker
        self._bm25 = BM25([chunk.text for chunk in store.chunks])

    # ── Core pipeline ────────────────────────────────────────────────────

    def retrieve(
        self,
        queries: list[str],
        allowed_versions: set[str],
        installed_components: set[str],
        rerank_query: str | None = None,
    ) -> ContextBundle:
        started = time.monotonic()
        fused = self._fuse_rankings(queries)
        filtered = self._metadata_filter(fused, allowed_versions, installed_components)
        filtered = self._rerank(filtered, rerank_query or " ".join(queries[:3]))
        selected = self._mmr(filtered, self.settings.top_k)
        bundle = self._assemble(selected)
        metrics.retrieval_seconds.observe(time.monotonic() - started)
        log.info(
            "retrieval_complete",
            queries=len(queries),
            candidates=len(fused),
            after_filter=len(filtered),
            selected=len(bundle.entries),
            reranked=getattr(self.reranker, "name", "none") != "none",
        )
        return bundle

    def _rerank(self, ranked: list[tuple[int, float]], query: str) -> list[tuple[int, float]]:
        """Cross-encoder rescoring of the fused top-N. RRF ranks by rank
        position; the cross-encoder scores actual query-document relevance.
        Reranked candidates keep positions above the untouched tail."""
        if self.reranker is None or not ranked:
            return ranked
        head = ranked[: self.settings.rerank_candidates]
        scores = self.reranker.scores(query, [self.store.chunks[i].text for i, _ in head])
        if scores is None:  # stage unavailable (null reranker) — no-op
            return ranked
        floor = min(score for _, score in head)
        # Normalise cross-encoder scores into a band above the tail so the
        # MMR relevance term stays meaningful across the seam.
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        reranked = sorted(
            (
                (index, floor + 0.001 + (score - lo) / span)
                for (index, _old), score in zip(head, scores, strict=True)
            ),
            key=lambda pair: -pair[1],
        )
        return reranked + ranked[len(head) :]

    def _fuse_rankings(self, queries: list[str]) -> dict[int, float]:
        """Reciprocal Rank Fusion over every (query, arm) ranking:
        score(chunk) = Σ 1 / (rrf_k + rank)."""
        rrf_k = self.settings.rrf_k
        scores: dict[int, float] = {}
        query_vectors = self.embedder.encode(queries)
        for row, query in enumerate(queries):
            dense = self.store.dense_search(query_vectors[row], self.settings.dense_candidates)
            lexical = self._bm25.search(query, self.settings.lexical_candidates)
            for ranking in (dense, lexical):
                for rank, (index, _score) in enumerate(ranking):
                    scores[index] = scores.get(index, 0.0) + 1.0 / (rrf_k + rank + 1)
        return scores

    def _metadata_filter(
        self,
        scores: dict[int, float],
        allowed_versions: set[str],
        installed_components: set[str],
    ) -> list[tuple[int, float]]:
        kept: list[tuple[int, float]] = []
        for index, score in scores.items():
            chunk = self.store.chunks[index]
            if chunk.k8s_version is not None and chunk.k8s_version not in allowed_versions:
                continue  # hard version filter — similarity cannot override
            if (
                chunk.component is not None
                and installed_components
                and chunk.component not in installed_components
                and chunk.kind == "component-docs"
            ):
                # Docs for a component that is not installed: keep with a
                # penalty rather than drop — the LLM may still need e.g.
                # provider docs matched under a component key.
                score *= 0.3
            kept.append((index, score))
        kept.sort(key=lambda pair: -pair[1])
        return kept

    def _mmr(self, ranked: list[tuple[int, float]], k: int) -> list[tuple[int, float]]:
        """Maximal Marginal Relevance over the fused candidates using the
        stored dense vectors for pairwise similarity."""
        if not ranked:
            return []
        lam = self.settings.mmr_lambda
        candidates = ranked[: max(k * 4, 40)]
        selected: list[tuple[int, float]] = []
        selected_vecs: list[np.ndarray] = []
        pool = list(candidates)
        max_score = max(score for _, score in pool) or 1.0

        while pool and len(selected) < k:
            best, best_value = None, -np.inf
            for index, score in pool:
                relevance = score / max_score
                redundancy = 0.0
                if selected_vecs:
                    vec = self.store.vectors[index]
                    redundancy = max(float(vec @ other) for other in selected_vecs)
                value = lam * relevance - (1 - lam) * redundancy
                if value > best_value:
                    best, best_value = (index, score), value
            assert best is not None
            selected.append(best)
            selected_vecs.append(self.store.vectors[best[0]])
            pool.remove(best)
        return selected

    def _assemble(self, selected: list[tuple[int, float]]) -> ContextBundle:
        """Number chunks as [DOC n] and pack them under the char budget.
        Compression strategy: whole chunks first; once the budget tightens,
        truncate the tail chunk rather than dropping it entirely.

        Ordering counters the lost-in-the-middle effect: models attend most
        to the start and end of long contexts, so the strongest chunks are
        placed at both edges (1st, 3rd, 5th … then … 6th, 4th, 2nd) and the
        weakest land in the middle. [DOC n] refs follow presentation order —
        the numbers are labels, not ranks."""
        arranged = selected[0::2] + list(reversed(selected[1::2]))
        budget = self.settings.max_context_chars
        entries: list[RetrievedChunk] = []
        parts: list[str] = []
        used = 0

        for ref, (index, score) in enumerate(arranged, start=1):
            chunk = self.store.chunks[index]
            header = (
                f"[DOC {ref}] {chunk.display_source}"
                + (f" (Kubernetes {chunk.k8s_version})" if chunk.k8s_version else "")
                + f" — {chunk.url}\n"
            )
            remaining = budget - used - len(header)
            if remaining < 400:
                break
            body = (
                chunk.text
                if len(chunk.text) <= remaining
                else chunk.text[:remaining] + " …[truncated]"
            )
            parts.append(header + body)
            used += len(header) + len(body) + 2
            entries.append(RetrievedChunk(chunk=chunk, score=score, ref=ref))

        return ContextBundle(entries=entries, context_text="\n\n".join(parts))
