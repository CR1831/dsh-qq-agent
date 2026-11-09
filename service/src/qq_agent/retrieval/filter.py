"""The information-filtering pipeline.

The point of this module is that "筛选" is a *visible, staged* process rather
than a single prompt.  Every discarded document carries a stage tag and a human
readable reason, so the agent can honestly report *what* it removed and *why*
instead of silently hallucinating a clean answer.

    dedupe -> hard rules -> lexical relevance -> LLM judge -> diversity -> quota
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Awaitable, Callable, Iterable, Sequence

from ..models import Doc
from .rank import BM25, mmr_select
from .text import contains_any, hamming, normalize, normalize_for_dedupe, simhash, term_coverage

Judge = Callable[[str, Sequence[Doc]], Awaitable[dict[str, tuple[float, str]]]]


@dataclass
class FilterCriteria:
    query: str = ""
    include_any: list[str] = field(default_factory=list)
    exclude_any: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    channel_id: str | None = None
    author_deny: list[str] = field(default_factory=list)
    since: float | None = None
    until: float | None = None
    min_chars: int = 4
    max_chars: int = 6000
    min_relevance: float = 0.0
    dedupe: bool = True
    near_dup_hamming: int = 3
    top_k: int = 20
    per_author_cap: int = 3
    diversity: float = 0.75
    use_llm_judge: bool = False
    llm_min_score: float = 0.5
    max_llm_batch: int = 40

    @classmethod
    def from_payload(cls, payload: dict | None) -> "FilterCriteria":
        payload = payload or {}
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in payload.items() if k in allowed and v is not None}
        if "since" in clean and isinstance(clean["since"], str):
            clean["since"] = _parse_time(clean["since"])
        if "until" in clean and isinstance(clean["until"], str):
            clean["until"] = _parse_time(clean["until"])
        return cls(**clean)


@dataclass
class Drop:
    doc: Doc
    stage: str
    reason: str


@dataclass
class FilterResult:
    kept: list[Doc]
    scores: dict[str, float]
    dropped: list[Drop]
    stats: dict[str, int]
    elapsed_ms: float

    def summary(self) -> str:
        parts = [f"保留 {len(self.kept)} 条"]
        if self.dropped:
            buckets: dict[str, int] = {}
            for drop in self.dropped:
                buckets[drop.stage] = buckets.get(drop.stage, 0) + 1
            detail = "、".join(f"{k} {v}" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]))
            parts.append(f"过滤 {len(self.dropped)} 条（{detail}）")
        return "，".join(parts)

    def as_payload(self, limit: int | None = None) -> dict:
        kept = self.kept if limit is None else self.kept[:limit]
        return {
            "kept": [d.as_payload() for d in kept],
            "scores": {d.doc_id: round(self.scores.get(d.doc_id, 0.0), 4) for d in kept},
            "stats": self.stats,
            "summary": self.summary(),
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


def _parse_time(value: str) -> float:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m-%d %H:%M"):
        try:
            tm = time.strptime(value, fmt)
        except ValueError:
            continue
        year = tm.tm_year if "%Y" in fmt else time.localtime().tm_year
        return time.mktime((year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0, 0, -1))
    raise ValueError(f"无法解析时间：{value}")


async def filter_docs(
    docs: Iterable[Doc],
    criteria: FilterCriteria | None = None,
    *,
    judge: Judge | None = None,
) -> FilterResult:
    started = time.perf_counter()
    criteria = criteria or FilterCriteria()
    stats: dict[str, int] = {}
    dropped: list[Drop] = []

    def drop(doc: Doc, stage: str, reason: str) -> None:
        stats[stage] = stats.get(stage, 0) + 1
        dropped.append(Drop(doc, stage, reason))

    # ---- stage 1: identity dedupe -------------------------------------
    unique: list[Doc] = []
    seen: set[str] = set()
    for doc in docs:
        if doc.doc_id in seen:
            stats["dedupe"] = stats.get("dedupe", 0) + 1
            continue
        seen.add(doc.doc_id)
        unique.append(doc)
    stats["input"] = len(unique)

    # ---- stage 2: hard rules ------------------------------------------
    survivors: list[Doc] = []
    for doc in unique:
        body = f"{doc.title}\n{doc.text}".strip()
        if criteria.platforms and doc.platform not in criteria.platforms:
            drop(doc, "platform", f"平台不匹配（{doc.platform}）")
            continue
        if criteria.kinds and doc.kind not in criteria.kinds:
            drop(doc, "kind", f"类型不匹配（{doc.kind}）")
            continue
        if criteria.channel_id and doc.channel_id != criteria.channel_id:
            drop(doc, "channel", "频道不匹配")
            continue
        if criteria.since is not None and doc.ts and doc.ts < criteria.since:
            drop(doc, "time", "早于起始时间")
            continue
        if criteria.until is not None and doc.ts and doc.ts > criteria.until:
            drop(doc, "time", "晚于结束时间")
            continue
        if len(normalize(body)) < criteria.min_chars:
            drop(doc, "length", "内容过短")
            continue
        if len(body) > criteria.max_chars:
            doc.text = doc.text[: criteria.max_chars]
        if criteria.author_deny and (doc.author_id in criteria.author_deny or doc.author_name in criteria.author_deny):
            drop(doc, "author", "作者在黑名单")
            continue
        excluded = contains_any(body, criteria.exclude_any)
        if excluded:
            drop(doc, "keyword", f"命中排除词：{excluded[0]}")
            continue
        if criteria.include_any and not contains_any(body, criteria.include_any):
            drop(doc, "keyword", "缺少必含关键词")
            continue
        survivors.append(doc)

    # ---- stage 3: near-duplicate collapse ------------------------------
    if criteria.dedupe and survivors:
        # SimHash is stable for long text; short chat lines need a real string
        # similarity because one changed character flips many 3-grams.
        norms = [normalize_for_dedupe(f"{d.title} {d.text}") for d in survivors]
        hashes = [simhash(f"{d.title} {d.text}") for d in survivors]
        kept_flags = [True] * len(survivors)
        for i in range(len(survivors)):
            if not kept_flags[i]:
                continue
            for j in range(i + 1, len(survivors)):
                if not kept_flags[j]:
                    continue
                a, b = norms[i], norms[j]
                if a == b and a:
                    similar = True
                elif len(a) < 400 and len(b) < 400:
                    similar = SequenceMatcher(None, a, b).ratio() >= 0.94
                else:
                    similar = hamming(hashes[i], hashes[j]) <= criteria.near_dup_hamming
                if similar:
                    kept_flags[j] = False
                    drop(survivors[j], "near_duplicate", "与更早内容高度重复")
        survivors = [doc for doc, keep in zip(survivors, kept_flags) if keep]

    # ---- stage 4: lexical relevance ------------------------------------
    scores: dict[str, float] = {}
    if survivors:
        if criteria.query.strip():
            bm25 = BM25(survivors)
            lexical = bm25.normalized(criteria.query)
            ranked: list[Doc] = []
            for doc, lex in zip(survivors, lexical):
                coverage = term_coverage(f"{doc.title} {doc.text}", criteria.query)
                score = round(0.7 * lex + 0.3 * coverage, 4)
                scores[doc.doc_id] = score
                if score < criteria.min_relevance:
                    drop(doc, "relevance", f"相关度 {score:.2f} 低于阈值")
                    continue
                ranked.append(doc)
            survivors = ranked
        else:
            for doc in survivors:
                scores[doc.doc_id] = 1.0

    # ---- stage 5: optional LLM judge -----------------------------------
    if criteria.use_llm_judge and judge and survivors:
        batch = survivors[: criteria.max_llm_batch]
        batch_ids = {d.doc_id for d in batch}
        try:
            verdicts = await judge(criteria.query, batch)
        except Exception:  # never let the judge break retrieval
            verdicts = {}
            stats["llm_error"] = 1
        judged: list[Doc] = []
        for doc in survivors:
            if doc.doc_id in batch_ids:
                score, why = verdicts.get(doc.doc_id, (0.5, "未评估"))
                combined = round(0.5 * scores.get(doc.doc_id, 0.0) + 0.5 * float(score), 4)
                scores[doc.doc_id] = combined
                if float(score) < criteria.llm_min_score:
                    drop(doc, "llm", why or "模型判定不相关")
                    continue
            judged.append(doc)
        survivors = judged

    # ---- stage 6: ordering, diversity, quota ---------------------------
    order = sorted(survivors, key=lambda d: (scores.get(d.doc_id, 0.0), d.ts), reverse=True)
    ordered_scores = [scores.get(d.doc_id, 0.0) for d in order]
    if len(order) > criteria.top_k:
        picked = mmr_select(
            order,
            ordered_scores,
            criteria.top_k,
            lambda_=criteria.diversity,
            max_per_author=criteria.per_author_cap,
        )
        keep_ids = {order[i].doc_id for i in picked}
        for doc in order:
            if doc.doc_id not in keep_ids:
                drop(doc, "quota", "超出条数上限或作者配额")
        order = [order[i] for i in picked]
        order.sort(key=lambda d: (scores.get(d.doc_id, 0.0), d.ts), reverse=True)

    stats["kept"] = len(order)
    return FilterResult(
        kept=order,
        scores=scores,
        dropped=dropped,
        stats=stats,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


__all__ = ["FilterCriteria", "FilterResult", "Drop", "filter_docs"]
