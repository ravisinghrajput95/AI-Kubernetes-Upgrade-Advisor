"""Semantic (structure-aware) chunking.

Documents split along their own structure — markdown headings first, then
paragraph groups — rather than at fixed character offsets. Each chunk
carries its heading path ("CHANGELOG 1.29 > Deprecation") plus the source
metadata (component, k8s_version), which is what makes retrieval-time
metadata filtering possible.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .fetcher import RawDocument

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")


@dataclass
class Chunk:
    chunk_id: str
    doc_key: str
    title: str
    url: str
    kind: str
    component: str | None
    k8s_version: str | None
    section: str
    text: str
    metadata: dict = field(default_factory=dict)

    @property
    def display_source(self) -> str:
        return f"{self.title}" + (f" § {self.section}" if self.section else "")


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown into (heading-path, body) sections."""
    sections: list[tuple[str, str]] = []
    path: dict[int, str] = {}
    current: list[str] = []
    current_path = ""

    def flush() -> None:
        body = "\n".join(current).strip()
        if body:
            sections.append((current_path, body))

    for line in content.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            current = []
            level = len(m.group(1))
            path[level] = m.group(2).strip()
            for deeper in list(path):
                if deeper > level:
                    del path[deeper]
            current_path = " > ".join(path[k] for k in sorted(path))
        else:
            current.append(line)
    flush()
    return sections or [("", content.strip())]


def _split_to_size(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = re.split(r"\n{2,}", text)
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > max_chars and buf:
            pieces.append(buf)
            buf = (buf[-overlap:] + "\n\n" if overlap else "") + para
        else:
            buf = candidate
        # A single paragraph larger than max_chars gets hard-wrapped.
        while len(buf) > max_chars:
            pieces.append(buf[:max_chars])
            buf = buf[max_chars - overlap :] if overlap else buf[max_chars:]
    if buf.strip():
        pieces.append(buf)
    return pieces


def chunk_document(doc: RawDocument, max_chars: int = 1400, overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section_path, body in _split_sections(doc.content):
        for piece in _split_to_size(body, max_chars, overlap):
            text = piece.strip()
            if len(text) < 80:  # boilerplate fragments carry no signal
                continue
            digest = hashlib.sha256(f"{doc.key}|{section_path}|{text[:200]}".encode()).hexdigest()[
                :12
            ]
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.key}-{digest}",
                    doc_key=doc.key,
                    title=doc.title,
                    url=doc.url,
                    kind=doc.kind,
                    component=doc.component,
                    k8s_version=doc.k8s_version,
                    section=section_path,
                    text=text,
                )
            )
    return chunks


def chunk_documents(
    docs: list[RawDocument], max_chars: int = 1400, overlap: int = 200
) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in docs:
        out.extend(chunk_document(doc, max_chars=max_chars, overlap=overlap))
    return out
