"""QQ 好友画像：这个人是谁、怎么说话、跟你什么关系。

数据落在 ``<data_dir>/profiles.json``，和对话历史分开：

    {
      "10002": {
        "name": "示例好友",
        "stats": {"msgs": 12, "avg_len": 14, "first": 1793..., "last": 1793..., "hours": [14, 15]},
        "impression": "话不多，句子短，偶尔冷不丁开个玩笑。",
        "updated": 1793...
      }
    }

两层：
* **统计**（零成本）——条数、平均长度、活跃时段，从历史里直接算；
* **印象**（要调模型）——低频刷新，写成一句人话，供它在对话里参考。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PROFILE_PROMPT = (
    "下面是一个人和某个 QQ 号的聊天记录（只截取对方说的话）。\n"
    "用一到两句话，客观描述这个人：说话多不多、句子长短、语气、有没有明显的习惯。\n"
    "只根据记录里看得出来的写，不要猜身份、年龄、职业，不要评价好坏，"
    "不要写「这个人」作为开头。30 字以内。"
)


def profile_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / "profiles.json"


def load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: Path, data: dict[str, dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def stats_from(history: list[dict[str, Any]]) -> dict[str, Any]:
    """从对话历史里算统计——不调模型，随时可更新。"""
    rows = [r for r in history if r.get("role") == "user"]
    texts = [str(r.get("content") or "") for r in rows]
    texts = [t for t in texts if t]
    if not texts:
        return {"msgs": 0}

    stamps = [float(r.get("ts") or 0) for r in rows if r.get("ts")]
    hours: dict[int, int] = {}
    for ts in stamps:
        hour = time.localtime(ts).tm_hour
        hours[hour] = hours.get(hour, 0) + 1
    top_hours = [h for h, _ in sorted(hours.items(), key=lambda kv: kv[1], reverse=True)[:3]]

    return {
        "msgs": len(texts),
        "avg_len": round(sum(len(t) for t in texts) / len(texts)),
        "max_len": max(len(t) for t in texts),
        "first": min(stamps) if stamps else 0,
        "last": max(stamps) if stamps else 0,
        "hours": sorted(top_hours),
    }


def needs_refresh(profile: dict[str, Any] | None, stats: dict[str, Any], *, min_msgs: int = 5, hours: float = 6.0) -> bool:
    """够不够格、隔得够不够久——别每次说话都去调模型。"""
    if stats.get("msgs", 0) < min_msgs:
        return False
    if not profile:
        return True
    if not profile.get("impression"):
        return True
    updated = float(profile.get("updated") or 0)
    seen = int((profile.get("stats") or {}).get("msgs") or 0)
    if stats.get("msgs", 0) - seen >= min_msgs and time.time() - updated > hours * 3600:
        return True
    return False


async def build_impression(history: list[dict[str, Any]], llm: Any, *, limit: int = 30) -> str:
    """让模型用一句人话概括这个人说过的话。失败返回空串。"""
    texts = [str(r.get("content") or "")[:80] for r in history if r.get("role") == "user"]
    texts = [t for t in texts if t][-limit:]
    if len(texts) < 3:
        return ""
    body = "\n".join(f"- {t}" for t in texts)
    try:
        message = await llm.chat(
            [
                {"role": "system", "content": PROFILE_PROMPT},
                {"role": "user", "content": body},
            ],
            temperature=0.3,
            max_tokens=120,
        )
    except Exception:
        return ""
    text = " ".join(str((message or {}).get("content") or "").split())
    return text[:120]


def describe(
    profile: dict[str, Any] | None,
    *,
    name: str = "",
    include_facts: bool = False,
) -> str:
    """把画像压成给模型看的一小段。资料字段默认不注入。"""
    if not profile:
        return ""
    bits: list[str] = []
    label = profile.get("name") or name
    if label:
        bits.append(f"对方：{label}")

    if include_facts:
        # QQ 资料（get_stranger_info 来的）：年龄、学校、所在地——都是他自己填的
        facts: list[str] = []
        age = profile.get("age")
        if age:
            facts.append(f"{int(age)} 岁")
        if profile.get("college"):
            facts.append(str(profile["college"]))
        elif profile.get("city"):
            facts.append(str(profile["city"]))
        birth = ""
        year = profile.get("birthday_year")
        month = profile.get("birthday_month")
        day = profile.get("birthday_day")
        if year and month and day:
            birth = f"{int(year)}-{int(month):02d}-{int(day):02d}"
        if birth:
            facts.append(f"生日 {birth}")
        if facts:
            bits.append("资料：" + " · ".join(facts))

    impression = str(profile.get("impression") or "").strip()
    if impression:
        bits.append(f"印象：{impression}")
    stats = profile.get("stats") or {}
    msgs = int(stats.get("msgs") or 0)
    if msgs:
        line = f"聊过 {msgs} 次"
        avg = stats.get("avg_len")
        if avg:
            line += f"，平均每句 {avg} 字"
        hours = stats.get("hours") or []
        if hours:
            line += "，常在 " + "、".join(f"{int(h)}点" for h in hours) + " 出现"
        bits.append(line)
    return "\n".join(bits)


__all__ = [
    "PROFILE_PROMPT",
    "build_impression",
    "describe",
    "load",
    "needs_refresh",
    "profile_path",
    "save",
    "stats_from",
]
