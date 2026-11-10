"""历史对话检索：让 agent 能回答「上次聊到哪了」。

为什么单独做：`history.json` 其实每人存 60 条，但 prompt 只注入最近 8 条——
更早的对话不是没存，是 agent **看不见**。这个模块把剩下的摊开给它搜。

只读。不改数据。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _history_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / "history.json"


def _load(db_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(_history_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _fmt_time(ts: Any) -> str:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(t))


def search(
    db_path: str | Path,
    query: str,
    *,
    person: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """按关键词搜历史对话。

    - `person`：QQ 号或 8 位以上片段，用来只看某个人；留空搜全部
    - 返回按时间倒序的命中片段
    """
    q = (query or "").strip().lower()
    if not q:
        return {"ok": False, "error": "要搜什么？给个关键词。"}

    data = _load(db_path)
    if not data:
        return {"ok": True, "hits": [], "summary": "历史记录是空的。"}

    who = (person or "").strip()
    hits: list[dict[str, Any]] = []
    for channel, rows in data.items():
        if who and who not in str(channel):
            continue
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            content = str(row.get("content") or "")
            if q not in content.lower():
                continue
            # 命中那一条也带上前后各一句，不然只剩半句话看不懂
            before = str(rows[i - 1].get("content") or "")[:120] if i > 0 else ""
            after = str(rows[i + 1].get("content") or "")[:120] if i + 1 < len(rows) else ""
            hits.append(
                {
                    "channel": channel,
                    "role": row.get("role"),
                    "time": _fmt_time(row.get("ts")),
                    "content": content[:400],
                    "before": before,
                    "after": after,
                    "ts": float(row.get("ts") or 0),
                }
            )

    hits.sort(key=lambda h: h["ts"], reverse=True)
    trimmed = hits[: max(1, min(int(limit or 10), 30))]

    if not trimmed:
        return {
            "ok": True,
            "hits": [],
            "summary": f"历史里没搜到「{query}」。" + (f"（只在 {person} 的记录里找）" if who else ""),
        }

    lines = [f"找到 {len(hits)} 处，最近 {len(trimmed)} 条："]
    for h in trimmed:
        who_label = h["channel"].replace("private:", "私聊 ").replace("group:", "群 ")
        role = "他说" if h["role"] == "user" else "我回"
        lines.append(f"- {h['time']} {who_label} {role}：{h['content'][:160]}")
    return {
        "ok": True,
        "summary": "\n".join(lines),
        "hits": trimmed,
        "total": len(hits),
    }


def recent(
    db_path: str | Path, channel: str, *, turns: int = 15
) -> dict[str, Any]:
    """看某个会话最近的完整对话（比 prompt 里的 8 条多）。

    `channel` 可以是 `private:111`、`group:222`，也可以只给裸 uid——
    2026-11-10 加测试时发现：原来只做了 `private:` 的剥离，
    传裸 uid 会查不到（`data.get("111")` 和 `data.get("111")` 都是空）。
    """
    data = _load(db_path)
    rows = (
        data.get(channel)
        or data.get(f"private:{channel}")
        or data.get(channel.replace("private:", ""))
        or []
    )
    if not rows:
        return {"ok": True, "summary": f"{channel} 没有历史记录。", "turns": []}

    keep = max(2, min(int(turns or 15), 60))
    picked = rows[-keep:]
    lines = []
    for row in picked:
        who = "他" if row.get("role") == "user" else "我"
        lines.append(f"{_fmt_time(row.get('ts'))} {who}：{str(row.get('content') or '')[:200]}")
    return {
        "ok": True,
        "summary": "\n".join(lines),
        "turns": picked,
        "count": len(picked),
        "total": len(rows),
    }


__all__ = ["search", "recent"]
