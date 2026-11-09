"""Small, dependency-free text utilities used by filtering and ranking."""

from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Sequence

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RUN = re.compile(f"[{_CJK}]+")
_WORD = re.compile(r"[a-z0-9_]+")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_CTRL = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_PUNCT = re.compile(r"[\s!-/:-@\[-`{-~，。！？、；：“”‘’（）《》【】—…·]+")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = _TAG.sub(" ", text)
    return html.unescape(text)


def normalize(text: str) -> str:
    """Canonical form used for hashing, dedupe and matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _CTRL.sub("", text)
    text = text.replace("\u00a0", " ")
    text = _SPACE.sub(" ", text).strip()
    return text


def normalize_for_dedupe(text: str) -> str:
    text = normalize(text).lower()
    text = _URL.sub(" ", text)
    text = _PUNCT.sub("", text)
    return text


def tokenize(text: str) -> list[str]:
    """Latin words + CJK bigrams (unigram for single-character runs)."""
    text = normalize(text).lower()
    tokens: list[str] = []
    tokens.extend(_WORD.findall(text))
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def char_ngrams(text: str, n: int = 3) -> set[str]:
    cleaned = normalize_for_dedupe(text)
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _hash64(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    weights = Counter(char_ngrams(text, 3))
    if not weights:
        return 0
    vector = [0] * 64
    for feature, weight in weights.items():
        h = _hash64(feature)
        for bit in range(64):
            vector[bit] += weight if (h >> bit) & 1 else -weight
    out = 0
    for bit in range(64):
        if vector[bit] > 0:
            out |= 1 << bit
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def contains_any(text: str, needles: Iterable[str]) -> list[str]:
    haystack = normalize(text).lower()
    return [needle for needle in needles if needle and needle.lower() in haystack]


def excerpt(text: str, width: int = 160) -> str:
    body = _SPACE.sub(" ", strip_html(text)).strip()
    return body if len(body) <= width else body[: width - 1] + "…"


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def term_coverage(text: str, query: str) -> float:
    """Fraction of query terms present in ``text`` (0..1)."""
    q = [t for t in normalize(query).split() if t]
    if not q:
        return 0.0
    body = normalize(text).lower()
    hits = [t for t in q if t.lower() in body]
    return len(hits) / len(q)


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


__all__ = [
    "strip_html",
    "normalize",
    "normalize_for_dedupe",
    "tokenize",
    "char_ngrams",
    "simhash",
    "hamming",
    "cosine",
    "contains_any",
    "excerpt",
    "truncate",
    "term_coverage",
    "jaccard",
]
