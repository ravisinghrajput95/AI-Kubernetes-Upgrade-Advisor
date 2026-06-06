"""
k8s_assess/knowledge_base.py

Phase 2 — Knowledge Base
Chunks documents, generates embeddings via sentence-transformers,
and stores/loads a FAISS index.
"""

from __future__ import annotations

import json
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from .collector import Document, RAW_DIR

KB_DIR   = Path(__file__).parent.parent / "kb"
INDEX_PATH  = KB_DIR / "faiss.index"
META_PATH   = KB_DIR / "chunks_meta.pkl"

EMBED_MODEL = "all-MiniLM-L6-v2"    # 22 MB, fast, good quality
CHUNK_SIZE  = 600                    # chars
CHUNK_OVERLAP = 100


# ── Chunking ──────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id:  str
    doc_id:    str
    source:    str
    title:     str
    url:       str
    text:      str
    metadata:  dict


def _split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping windows on sentence boundaries where possible."""
    # First try to split on double newlines (paragraph boundaries)
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= size:
            current = (current + "\n\n" + para).lstrip()
        else:
            if current:
                chunks.append(current)
                # overlap: keep last `overlap` chars of current
                current = current[-overlap:].lstrip() + "\n\n" + para
            else:
                # paragraph itself is too long; hard-split
                for i in range(0, len(para), size - overlap):
                    chunks.append(para[i : i + size])
                current = ""

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if len(c.strip()) > 50]


def chunk_documents(docs: list[Document]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        parts = _split_text(doc.content)
        for i, text in enumerate(parts):
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}__{i:04d}",
                doc_id=doc.doc_id,
                source=doc.source,
                title=doc.title,
                url=doc.url,
                text=text,
                metadata={**doc.metadata, "chunk_index": i, "total_chunks": len(parts)},
            ))
    return chunks


# ── Embeddings + FAISS ────────────────────────────────────────────────────────

class KnowledgeBase:
    def __init__(self, model_name: str = EMBED_MODEL):
        self.model_name = model_name
        self._model: Optional[SentenceTransformer] = None
        self._index: Optional[faiss.IndexFlatIP] = None
        self._chunks: list[Chunk] = []

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            print(f"  ▸ Loading embedding model ({self.model_name}) …")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def build(self, chunks: list[Chunk], save: bool = True) -> None:
        """Embed all chunks and build FAISS index."""
        print(f"  ▸ Embedding {len(chunks)} chunks …")
        texts = [c.text for c in chunks]
        embeddings = self.model.encode(
            texts,
            batch_size=64,
            show_progress_bar=True,
            normalize_embeddings=True,   # inner-product == cosine after normalise
        )
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings.astype(np.float32))

        self._index  = index
        self._chunks = chunks

        if save:
            KB_DIR.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(INDEX_PATH))
            with open(META_PATH, "wb") as f:
                pickle.dump(chunks, f)
            print(f"  ✔  Index saved ({len(chunks)} chunks, dim={dim})")

    def load(self) -> bool:
        """Load a pre-built index from disk. Returns True if successful."""
        if not INDEX_PATH.exists() or not META_PATH.exists():
            return False
        self._index = faiss.read_index(str(INDEX_PATH))
        with open(META_PATH, "rb") as f:
            self._chunks = pickle.load(f)
        print(f"  ✔  Loaded KB: {len(self._chunks)} chunks")
        return True

    def is_ready(self) -> bool:
        return self._index is not None and len(self._chunks) > 0

    def search(self, query: str, top_k: int = 8) -> list[Chunk]:
        """Return top-k most relevant chunks for a query."""
        if not self.is_ready():
            raise RuntimeError("Knowledge base not loaded. Run 'build' or 'load' first.")
        q_emb = self.model.encode(
            [query], normalize_embeddings=True
        ).astype(np.float32)
        scores, indices = self._index.search(q_emb, top_k)
        results = []
        for idx, score in zip(indices[0], scores[0]):
            if idx < 0:
                continue
            chunk = self._chunks[idx]
            results.append(chunk)
        return results

    def search_multi(self, queries: list[str], top_k_each: int = 5,
                     relevant_versions: list[str] | None = None) -> list[Chunk]:
        """
        Run multiple queries, deduplicate by chunk_id, return combined results.

        If relevant_versions is provided (e.g. ["1.34", "1.35"]), release-note
        chunks whose title contains a version number NOT in the list are
        deprioritised — moved to the end of results.  This prevents retrieval
        drift where a query for "1.35 release notes" surfaces "1.28 Release Notes"
        due to semantic similarity in the embedding space.
        """
        seen: set[str] = set()
        priority: list[Chunk] = []
        fallback: list[Chunk] = []

        for q in queries:
            for chunk in self.search(q, top_k=top_k_each):
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)

                if relevant_versions and chunk.source == "kubernetes_release_notes":
                    # Check whether this chunk's version is in our upgrade path
                    ver_match = re.search(r"(\d+\.\d+)", chunk.title)
                    if ver_match:
                        chunk_ver = ver_match.group(1)
                        if chunk_ver not in relevant_versions:
                            fallback.append(chunk)
                            continue
                priority.append(chunk)

        return priority + fallback


# ── Convenience: load all raw docs from disk ─────────────────────────────────

def load_raw_docs(directory: Path = RAW_DIR) -> list[Document]:
    docs = []
    for p in sorted(directory.glob("*.json")):
        try:
            docs.append(Document.load(p))
        except Exception as e:
            print(f"  ⚠  Could not load {p.name}: {e}")
    return docs


def build_kb_from_raw(force: bool = False) -> KnowledgeBase:
    """Load raw docs → chunk → embed → return ready KnowledgeBase."""
    kb = KnowledgeBase()
    if not force and kb.load():
        return kb
    print("  ▸ Building knowledge base from raw docs …")
    docs = load_raw_docs()
    if not docs:
        print("  ⚠  No raw documents found. Run collect first.")
        return kb
    chunks = chunk_documents(docs)
    print(f"  ✔  {len(docs)} docs → {len(chunks)} chunks")
    kb.build(chunks, save=True)
    return kb
