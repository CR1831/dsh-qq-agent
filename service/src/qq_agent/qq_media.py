"""QQ 消息里的媒体：图片、视频、表情、语音。

`render_segments` 只负责把消息压成文字，媒体段会被压成 `[图片]` 这类占位符。
这里补上另一半：把原始段落里的 URL / 文件 / 表情摘要挑出来，供上层读取。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 卡片结构里真正装内容的那一层，按常见程度排
CARD_KEYS = ("detail_1", "news", "music", "video", "detail_2", "contact")


def parse_card(segment: dict[str, Any]) -> dict[str, Any] | None:
    """解析 `type=json` 的结构化卡片（B 站分享、小程序、音乐等）。

    QQ 把卡片塞成 `data.data` 里的一段 JSON 字符串，真正的标题/描述/封面
    在 `meta.<key>` 下。解析不出来就返回 None——不猜。
    """
    data = segment.get("data") or {}
    if not isinstance(data, dict):
        return None
    raw = data.get("data")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        card = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(card, dict):
        return None

    meta = card.get("meta") or {}
    for key in CARD_KEYS:
        info = meta.get(key)
        if not isinstance(info, dict):
            continue
        title = str(info.get("title") or "").strip()
        desc = str(info.get("desc") or "").strip()
        if not title and not desc:
            continue
        return {
            "kind": key,
            "app": str(card.get("app") or ""),
            "title": title,
            "desc": desc,
            "url": str(info.get("url") or info.get("jumpUrl") or info.get("qqdocurl") or ""),
            "preview": str(info.get("preview") or info.get("icon") or ""),
        }

    prompt = str(card.get("prompt") or "").strip()
    if prompt:
        return {
            "kind": "prompt",
            "app": str(card.get("app") or ""),
            "title": prompt,
            "desc": "",
            "url": "",
            "preview": "",
        }
    return None


def card_text(card: dict[str, Any]) -> str:
    """把卡片压成一句人话，供模型读。

    各类卡片的字段语义不一样：
    * `detail_1`（小程序）：`title` 往往是**平台名**（"哔哩哔哩"），内容在 `desc`；
    * `news` / `video`：`title` 是标题、`desc` 是简介，平铺即可；
    * `music`：`title` 是歌名、`desc` 是歌手，用「来自」连接。
    别用一个规则套所有类型，否则会读成「某个视频（来自某个视频）」这种。
    """
    kind = str(card.get("kind") or "")
    title = str(card.get("title") or "").strip()
    desc = str(card.get("desc") or "").strip()
    app = str(card.get("app") or "")

    platform = ""
    if kind == "detail_1" and title and desc and title != desc and len(title) <= 8:
        platform, content = title, desc
    elif kind == "music" and title and desc:
        platform, content = desc, title
    elif title and desc and title != desc:
        content = f"{title}｜{desc}"
    else:
        content = title or desc

    if not content:
        return ""

    low = (platform + app).lower()
    if "bili" in low or "哔哩哔哩" in platform:
        tag = "B站分享"
    elif kind == "music":
        tag = "音乐卡片"
    elif "miniapp" in app:
        tag = "小程序卡片"
    else:
        tag = "卡片"

    return f"[{tag}] {content}（来自{platform}）" if platform else f"[{tag}] {content}"

_QQ_FACE_HINT = re.compile(r"^\s*$")


def extract_media(message: Any) -> dict[str, list[dict[str, Any]]]:
    """从 OneBot 的 message 数组里挑出媒体段。

    返回 ``{"images": [...], "videos": [...], "records": [...], "faces": [...]}``。
    ``message`` 是 CQ 字符串时只能粗略解析，返回空列表——不猜。
    """
    out: dict[str, list[dict[str, Any]]] = {
        "images": [],
        "videos": [],
        "records": [],
        "faces": [],
        "cards": [],
    }
    if not isinstance(message, list):
        return out

    for seg in message:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        if not isinstance(data, dict):
            continue

        if kind == "image":
            out["images"].append(
                {
                    "url": str(data.get("url") or ""),
                    "file": str(data.get("file") or ""),
                    "summary": str(data.get("summary") or ""),
                }
            )
        elif kind == "video":
            out["videos"].append(
                {
                    "url": str(data.get("url") or ""),
                    "file": str(data.get("file") or ""),
                    "cover": str(data.get("cover") or ""),
                }
            )
        elif kind == "record":
            out["records"].append(
                {
                    "url": str(data.get("url") or ""),
                    "file": str(data.get("file") or ""),
                }
            )
        elif kind == "mface":
            # 商城表情带 summary（比如「[微笑]」），能直接当语气读
            summary = str(data.get("summary") or "").strip()
            if summary:
                out["faces"].append(summary)
        elif kind == "face":
            face_id = str(data.get("id") or "").strip()
            if face_id:
                out["faces"].append(f"QQ表情#{face_id}")
        elif kind == "json":
            card = parse_card(seg)
            if card:
                out["cards"].append(card)

    return out


def media_note(media: dict[str, list[dict[str, Any]]]) -> str:
    """媒体存在时给模型的一句提示（不含内容细节）。"""
    bits = []
    if media.get("images"):
        bits.append(f"{len(media['images'])} 张图片")
    if media.get("videos"):
        bits.append(f"{len(media['videos'])} 个视频")
    if media.get("records"):
        bits.append(f"{len(media['records'])} 段语音")
    if media.get("faces"):
        bits.append("表情")
    if media.get("cards"):
        bits.append(f"{len(media['cards'])} 张卡片")
    return "（对方还发了" + "、".join(bits) + "）" if bits else ""


__all__ = ["extract_media", "media_note"]
