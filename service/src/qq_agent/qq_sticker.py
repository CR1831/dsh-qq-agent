"""收藏表情：拉下来、读成备注、按备注挑。

QQ 的收藏表情有备注，但只在客户端本地，NapCat 的 `fetch_custom_face` 只给 URL。
所以这里用视觉模型给每张写一句备注，之后按那句备注找表情。

数据：`data/qq/stickers.json`

    [{"url": "https://p.qpic.cn/…", "note": "闭眼流汗，尴尬无语", "added": 1793…}]
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

NOTE_PROMPT = (
    "这张是聊天用的表情包。用一句很短的话写它的「备注」："
    "画面是什么 + 什么情绪，20 字以内。"
    "不要客套，不要写「这张图」，不要用引号。例如：闭眼流汗，尴尬无语。"
)


def store_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / "stickers.json"


def load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "url": url,
                "note": str(item.get("note") or "").strip(),
                "added": float(item.get("added") or 0),
            }
        )
    return out


def save(path: Path, items: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


async def sync(
    adapter: Any,
    vision: Any,
    path: Path,
    *,
    limit: int = 40,
) -> dict[str, Any]:
    """拉一遍收藏表情，给没备注的补上备注。已描述过的跳过。"""
    try:
        raw = await adapter.api("fetch_custom_face")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "added": 0}

    urls = [str(u).strip() for u in (raw or []) if str(u).strip()][:limit]
    if not urls:
        return {"ok": True, "added": 0, "total": 0, "skipped": 0}

    existing = {item["url"]: item for item in load(path)}
    added = 0
    failed = 0
    for url in urls:
        if url in existing and existing[url].get("note"):
            continue
        note = ""
        try:
            note = await vision.describe(url, prompt=NOTE_PROMPT, errors=None)
        except Exception:
            note = ""
        if not note:
            failed += 1
            continue
        existing[url] = {"url": url, "note": note.strip(), "added": time.time()}
        added += 1

    items = list(existing.values())
    save(path, items)
    return {
        "ok": True,
        "added": added,
        "failed": failed,
        "skipped": len(urls) - added - failed,
        "total": len(items),
    }


def find(items: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    """按备注找表情。先精确包含，再退回按单字命中数排序。"""
    key = str(keyword or "").strip()
    if not key:
        return []
    hits = [item for item in items if key in item.get("note", "")]
    if hits:
        return hits
    # 退回：关键词里的字有多少出现在备注里
    scored = []
    for item in items:
        note = item.get("note", "")
        score = sum(1 for ch in key if ch in note)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored]


def match(items: list[dict[str, Any]], keyword: str) -> dict[str, Any] | None:
    hits = find(items, keyword)
    return hits[0] if hits else None


def summary(items: list[dict[str, Any]], limit: int = 20) -> str:
    if not items:
        return "（还没有收藏表情，先跑 `qq-agent qq --stickers` 同步）"
    lines = []
    for index, item in enumerate(items[:limit], 1):
        lines.append(f"{index}. {item.get('note') or '(无备注)'}")
    if len(items) > limit:
        lines.append(f"……还有 {len(items) - limit} 个")
    return "\n".join(lines)


__all__ = [
    "NOTE_PROMPT",
    "find",
    "load",
    "match",
    "save",
    "store_path",
    "summary",
    "sync",
]
