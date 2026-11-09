"""Ranking helpers: BM25 for lexical recall, MMR for diversity."""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from ..models import Doc
from .text import cosine, tokenize


class BM25:
    """Plain Okapi BM25 over an in-memory corpus.

    FTS5 already ranks matches, but results fetched from a platform API (a
    Bilibili search page, a chat history dump) never went through FTS5, so they
    need their own lexical score before the final merge.
    """

    def __init__(self, corpus: Sequence[Doc], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = list(corpus)
        self.tokens = [tokenize(f"{d.title} {d.text}") for d in self.docs]
        self.lengths = [len(t) for t in self.tokens]
        self.avgdl = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.tf = [Counter(t) for t in self.tokens]
        df: Counter = Counter()
        for counter in self.tf:
            df.update(counter.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def scores(self, query: str) -> list[float]:
        q_terms = tokenize(query)
        if not q_terms or not self.docs:
            return [0.0] * len(self.docs)
        out: list[float] = []
        for idx, counter in enumerate(self.tf):
            dl = self.lengths[idx] or 1
            score = 0.0
            for term in q_terms:
                freq = counter.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                score += idf * (freq * (self.k1 + 1)) / denom
            out.append(score)
        return out

    def normalized(self, query: str) -> list[float]:
        raw = self.scores(query)
        top = max(raw) if raw else 0.0
        if top <= 0:
            return [0.0] * len(raw)
        return [min(1.0, value / top) for value in raw]


def mmr_select(
    docs: Sequence[Doc],
    scores: Sequence[float],
    k: int,
    lambda_: float = 0.75,
    max_per_author: int = 3,
) -> list[int]:
    """Maximal marginal relevance selection over char-trigram vectors.

    Returns indices into ``docs``.  `max_per_author` keeps one loud account from
    eating the whole digest.
    """
    if k <= 0 or not docs:
        return []
    vectors = [Counter(tokenize(f"{d.title} {d.text}")) for d in docs]
    author_counts: Counter = Counter()
    selected: list[int] = []
    candidates = list(range(len(docs)))
    while candidates and len(selected) < k:
        best_idx = -1
        best_value = -1e9
        for idx in candidates:
            if author_counts[docs[idx].author_id or docs[idx].author_name] >= max_per_author:
                continue
            redundancy = 0.0
            if selected:
                redundancy = max(cosine(vectors[idx], vectors[j]) for j in selected)
            value = lambda_ * scores[idx] - (1 - lambda_) * redundancy
            if value > best_value:
                best_value = value
                best_idx = idx
        if best_idx < 0:  # every remaining candidate hit the author cap
            break
        selected.append(best_idx)
        author_counts[docs[best_idx].author_id or docs[best_idx].author_name] += 1
        candidates.remove(best_idx)
    return selected


__all__ = ["BM25", "mmr_select"]
