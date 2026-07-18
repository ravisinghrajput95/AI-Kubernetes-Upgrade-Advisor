"""Compact BM25 (Okapi) implementation.

~70 lines beats a dependency: the corpus is KB-scale (thousands of chunks),
scoring is a hot loop only at assessment time, and owning the tokenizer
means it matches the one used for feature-hashed embeddings — API group
tokens like ``flowcontrol.apiserver.k8s.io/v1beta2`` survive intact, which
is precisely where dense embeddings are weakest.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9./\-]{1,60}")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_tokens = [Counter(tokenize(doc)) for doc in documents]
        self._doc_lengths = [sum(c.values()) for c in self._doc_tokens]
        self._avg_length = (sum(self._doc_lengths) / len(self._doc_lengths)) if documents else 0.0

        df: Counter[str] = Counter()
        for counts in self._doc_tokens:
            df.update(counts.keys())
        n = len(documents)
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Return [(doc_index, score)] for the top-k matching documents."""
        terms = tokenize(query)
        scores = [0.0] * len(self._doc_tokens)
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, counts in enumerate(self._doc_tokens):
                tf = counts.get(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * self._doc_lengths[i] / (self._avg_length or 1.0)
                )
                scores[i] += idf * tf * (self.k1 + 1) / denom
        ranked = sorted(
            ((i, s) for i, s in enumerate(scores) if s > 0),
            key=lambda pair: -pair[1],
        )
        return ranked[:k]
