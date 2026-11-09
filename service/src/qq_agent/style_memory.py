"""风格记忆：让平时聊天的语感，慢慢影响这个账号怎么说话。

只学「怎么说」和「最近聊什么」——短语、话题、语气观察，不搬运内容本身。
按天分桶，只留最近 5 天，避免风格漂移太远。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from .llm import LLM, parse_json_object

KEEP_DAYS = 5
LEARN_PROMPT = """你在观察最近聊天里的语言习惯。材料是这段时间的对话片段。

只输出严格 JSON：
{
  "phrases": [{"text": "口语短语", "note": "什么时候用，10 字内"}],
  "topics": ["最近聊得比较多的话题"],
  "tone": "一句话描述这些消息的语气（20 字内）"
}

规则：
- phrases 要短（≤6 字）、口语、能直接说出口，比如「整活」「上头」「绷不住」；
- 不要生造，材料里确实常见的才行；最多 8 个；
- topics 最多 6 个，用短词；
- tone 只描述语气，不要评价内容好坏。
"""


def memory_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / "style_memory.json"


def load(path: str | Path) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return {"days": {}, "updated": 0}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return {"days": {}, "updated": 0}
    if not isinstance(data, dict):
        return {"days": {}, "updated": 0}
    data.setdefault("days", {})
    return data


def save(path: str | Path, data: dict[str, Any], *, keep_days: int = KEEP_DAYS) -> Path:
    days = data.get("days") or {}
    ordered = sorted(days.items(), key=lambda kv: kv[0])[-keep_days:]
    payload = {"days": dict(ordered), "updated": time.time()}
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file


def merge(data: dict[str, Any], day: str, learned: dict[str, Any]) -> dict[str, Any]:
    days = dict(data.get("days") or {})
    row = dict(days.get(day) or {"phrases": [], "topics": [], "tone": ""})
    seen = {str(p.get("text")) for p in row.get("phrases") or []}
    phrases = list(row.get("phrases") or [])
    for item in learned.get("phrases") or []:
        text = str(item.get("text") or "").strip()
        if text and text not in seen:
            phrases.append({"text": text, "note": str(item.get("note") or "")[:16]})
            seen.add(text)
    topics = list(dict.fromkeys(list(row.get("topics") or []) + [str(t) for t in learned.get("topics") or []]))
    tone = str(learned.get("tone") or row.get("tone") or "")
    days[day] = {"phrases": phrases[:12], "topics": topics[:8], "tone": tone}
    return {"days": days, "updated": time.time()}


async def learn(llm: LLM, docs: Iterable[Any], path: str | Path, *, day: str, max_docs: int = 30) -> int:
    items = list(docs)[:max_docs]
    if not items or not getattr(llm, "available", False):
        return 0
    material = "\n".join(
        f"- {getattr(d, 'title', '')}｜{(getattr(d, 'text', '') or '')[:80]}" for d in items
    )
    try:
        message = await llm.chat(
            [
                {"role": "system", "content": "你只输出严格 JSON。"},
                {"role": "user", "content": LEARN_PROMPT + "\n\n材料：\n" + material},
            ],
            temperature=0.4,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        learned = parse_json_object(message.get("content") or "")
    except Exception:
        return 0
    if not learned:
        return 0
    data = merge(load(path), day, learned)
    save(path, data)
    return len(learned.get("phrases") or [])


def hint(path: str | Path, *, phrases: int = 3, topics: int = 2) -> str:
    """给对话注入的一小段「最近语感」，没有就返回空。"""
    data = load(path)
    days = data.get("days") or {}
    if not days:
        return ""
    recent = [days[k] for k in sorted(days)[-3:]]
    seen: list[str] = []
    topic_list: list[str] = []
    tone = ""
    for row in reversed(recent):
        for item in row.get("phrases") or []:
            text = str(item.get("text") or "").strip()
            if text and text not in seen:
                seen.append(text)
        for topic in row.get("topics") or []:
            if topic and topic not in topic_list:
                topic_list.append(topic)
        tone = tone or str(row.get("tone") or "")
    if not seen and not topic_list:
        return ""
    bits = []
    if seen[:phrases]:
        bits.append("最近常见的说法：" + "、".join(seen[:phrases]))
    if topic_list[:topics]:
        bits.append("最近大家在聊：" + "、".join(topic_list[:topics]))
    if tone:
        bits.append("语气观察：" + tone)
    return (
        "【近期语感】" + "；".join(bits)
        + "。可以偶尔借用一两个说法，让话更像最近的人说的，但别硬塞、也别解释这些词。"
    )


def summary(path: str | Path) -> dict[str, Any]:
    data = load(path)
    days = data.get("days") or {}
    latest = days[max(days)] if days else {}
    return {
        "days": len(days),
        "phrases": [p.get("text") for p in latest.get("phrases") or []],
        "topics": latest.get("topics") or [],
        "tone": latest.get("tone") or "",
    }


__all__ = ["memory_path", "load", "save", "merge", "learn", "hint", "summary", "KEEP_DAYS"]
