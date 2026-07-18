"""Versioned knowledge store: chunks + vectors + manifest.

Layout under ``kb/``:
  manifest.json   — embedder name/dim, chunk params, build time, doc stats
  chunks.jsonl    — one chunk (text + metadata) per line
  vectors.npy     — float32 [n_chunks, dim], row-aligned with chunks.jsonl

The manifest is the integrity contract: loading refuses to serve an index
whose embedder or dimensions don't match the runtime configuration, because
querying one embedding space with vectors from another silently returns
garbage similarities. FAISS accelerates search when installed; otherwise a
numpy brute-force search does the same job for KB-scale corpora.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ..errors import KnowledgeBaseError
from ..observability import get_logger, metrics
from .chunker import Chunk
from .embeddings import EmbeddingBackend

log = get_logger(__name__)

MANIFEST_FILE = "manifest.json"
CHUNKS_FILE = "chunks.jsonl"
VECTORS_FILE = "vectors.npy"


@dataclass
class Manifest:
    built_at: str
    embedder: str
    dim: int
    chunk_chars: int
    chunk_overlap: int
    doc_count: int
    chunk_count: int
    source_version: str
    target_version: str
    schema: int = 2

    @property
    def age_days(self) -> float:
        built = datetime.fromisoformat(self.built_at)
        return (datetime.now(UTC) - built).total_seconds() / 86400


class KnowledgeStore:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, manifest: Manifest) -> None:
        if len(chunks) != vectors.shape[0]:
            raise KnowledgeBaseError(
                f"chunk/vector row mismatch: {len(chunks)} chunks vs {vectors.shape[0]} vectors"
            )
        self.chunks = chunks
        self.vectors = vectors
        self.manifest = manifest
        self._faiss_index = self._try_faiss(vectors)
        metrics.kb_chunks.set(len(chunks))
        metrics.kb_build_timestamp.set(datetime.fromisoformat(manifest.built_at).timestamp())

    @staticmethod
    def _try_faiss(vectors: np.ndarray):
        try:
            import faiss  # optional accelerator

            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(vectors)
            return index
        except ImportError:
            return None

    # ── Build / persist ──────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        chunks: list[Chunk],
        embedder: EmbeddingBackend,
        kb_dir: Path,
        source_version: str,
        target_version: str,
        chunk_chars: int,
        chunk_overlap: int,
        doc_count: int,
        batch_size: int = 64,
    ) -> KnowledgeStore:
        if not chunks:
            raise KnowledgeBaseError("no chunks to index — did document collection fail?")
        log.info("kb_build_started", chunks=len(chunks), embedder=embedder.name)

        parts = [
            embedder.encode([c.text for c in chunks[i : i + batch_size]])
            for i in range(0, len(chunks), batch_size)
        ]
        vectors = np.vstack(parts).astype(np.float32)

        manifest = Manifest(
            built_at=datetime.now(UTC).isoformat(),
            embedder=embedder.name,
            dim=vectors.shape[1],
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
            doc_count=doc_count,
            chunk_count=len(chunks),
            source_version=source_version,
            target_version=target_version,
        )

        kb_dir.mkdir(parents=True, exist_ok=True)
        with (kb_dir / CHUNKS_FILE).open("w", encoding="utf-8") as fh:
            for chunk in chunks:
                fh.write(json.dumps(asdict(chunk)) + "\n")
        np.save(kb_dir / VECTORS_FILE, vectors)
        (kb_dir / MANIFEST_FILE).write_text(json.dumps(asdict(manifest), indent=2))
        log.info("kb_build_finished", chunks=len(chunks), dim=manifest.dim)
        return cls(chunks, vectors, manifest)

    @classmethod
    def load(cls, kb_dir: Path, expected_embedder: str | None = None) -> KnowledgeStore:
        manifest_path = kb_dir / MANIFEST_FILE
        if not manifest_path.is_file():
            raise KnowledgeBaseError(
                f"no knowledge base at {kb_dir} — run the 'collect' and 'build-kb' phases first"
            )
        try:
            manifest = Manifest(**json.loads(manifest_path.read_text()))
        except (json.JSONDecodeError, TypeError) as exc:
            raise KnowledgeBaseError(f"corrupt KB manifest: {exc}") from exc

        if expected_embedder and manifest.embedder != expected_embedder:
            raise KnowledgeBaseError(
                f"KB was built with embedder '{manifest.embedder}' but runtime uses "
                f"'{expected_embedder}' — rebuild the KB (build-kb --force) or align config"
            )

        chunks = [
            Chunk(**json.loads(line))
            for line in (kb_dir / CHUNKS_FILE).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        vectors = np.load(kb_dir / VECTORS_FILE).astype(np.float32)
        store = cls(chunks, vectors, manifest)
        if manifest.age_days > 30:
            log.warning(
                "kb_stale",
                age_days=round(manifest.age_days, 1),
                hint="rebuild with 'collect --force' for fresh release notes",
            )
        return store

    # ── Search ───────────────────────────────────────────────────────────

    def dense_search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        """Return [(chunk_index, score)] by inner product (vectors are
        normalised, so this is cosine similarity)."""
        k = min(k, len(self.chunks))
        query = query_vector.reshape(1, -1).astype(np.float32)
        if self._faiss_index is not None:
            scores, indices = self._faiss_index.search(query, k)
            return [
                (int(i), float(s)) for i, s in zip(indices[0], scores[0], strict=True) if i >= 0
            ]
        scores = self.vectors @ query[0]
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top]
