"""QQ adapter for OneBot 11 (NapCat / Lagrange / LLOneBot / go-cqhttp).

QQ has two very different doors:

1. **Official bot platform** (``bot.q.qq.com``) — needs an enterprise/individual
   developer registration, the bot must be added to a group, and the API is a
   different protocol (``v2`` + WebSocket gateway).
2. **OneBot 11** — a local implementation that drives an actual QQ client. It is
   what most个人 projects use, it exposes group history, and it is what this
   adapter speaks.

The adapter talks HTTP for actions and WebSocket for events, so it works with
the standard "HTTP API + 正向 WebSocket" deployment.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

import httpx

from ..config import QQConfig
from ..models import Doc
from .base import AdapterError, BaseAdapter, merge_history

_CQ_CODE = re.compile(r"\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]")


def parse_channel(channel_id: str) -> tuple[str, int]:
    """``"group:123456"`` -> ``("group", 123456)``."""
    kind, _, raw = str(channel_id).partition(":")
    if not raw:
        raw, kind = kind, "group"
    try:
        return kind, int(raw)
    except ValueError as exc:
        raise AdapterError(f"非法的频道 ID：{channel_id}") from exc


def render_segments(segments: Any) -> str:
    """Flatten an OneBot message (array or CQ string) into readable text."""
    if segments is None:
        return ""
    if isinstance(segments, str):
        return _CQ_CODE.sub(_cq_to_text, segments).strip()
    parts: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            parts.append(str(seg))
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "face":
            parts.append("[表情]")
        elif kind == "at":
            parts.append(f"@{data.get('name') or data.get('qq') or ''}")
        elif kind == "reply":
            parts.append("[回复]")
        elif kind == "record":
            parts.append("[语音]")
        elif kind == "video":
            parts.append("[视频]")
        elif kind == "file":
            parts.append(f"[文件:{data.get('name', '')}]")
        elif kind == "forward":
            parts.append("[合并转发]")
        elif kind == "json":
            parts.append("[卡片消息]")
        elif kind == "mface":
            parts.append(f"[表情:{data.get('summary', '')}]")
        else:
            parts.append(f"[{kind}]")
    return "".join(parts).strip()


def _cq_to_text(match: re.Match[str]) -> str:
    kind = match.group(1)
    mapping = {
        "image": "[图片]",
        "face": "[表情]",
        "record": "[语音]",
        "video": "[视频]",
        "at": "@",
        "reply": "[回复]",
        "file": "[文件]",
        "forward": "[合并转发]",
    }
    return mapping.get(kind, "")


def event_to_doc(event: dict[str, Any]) -> Doc | None:
    """Convert one OneBot ``post_type=message`` event into a Doc."""
    if event.get("post_type") != "message":
        return None
    message_type = event.get("message_type", "group")
    sender = event.get("sender") or {}
    if message_type == "group":
        channel_id = f"group:{event.get('group_id')}"
        channel_name = sender.get("card") or ""
    else:
        channel_id = f"private:{event.get('user_id')}"
        channel_name = ""
    text = render_segments(event.get("message"))
    if not text:
        return None
    return Doc.message(
        "qq",
        channel_id,
        str(event.get("user_id", "")),
        text,
        native_id=str(event.get("message_id", "")) or f"{event.get('time')}:{event.get('user_id')}",
        channel_name=channel_name,
        sender_name=sender.get("card") or sender.get("nickname") or str(event.get("user_id", "")),
        ts=float(event.get("time") or time.time()),
        meta={
            "message_type": message_type,
            "group_id": event.get("group_id"),
            "raw_message": event.get("raw_message"),
        },
    )


def history_payload_to_docs(payload: Any, channel_id: str, channel_name: str = "") -> list[Doc]:
    """Normalise ``get_group_msg_history`` / ``get_friend_msg_history`` output."""
    if isinstance(payload, dict):
        messages = payload.get("messages") or payload.get("data") or []
    else:
        messages = payload or []
    docs: list[Doc] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        text = render_segments(item.get("message") or item.get("raw_message"))
        if not text:
            continue
        sender = item.get("sender") or {}
        user_id = str(item.get("user_id") or item.get("sender_id") or sender.get("user_id") or "")
        docs.append(
            Doc.message(
                "qq",
                channel_id,
                user_id,
                text,
                native_id=str(item.get("message_id", "")),
                channel_name=channel_name,
                sender_name=sender.get("card") or sender.get("nickname") or user_id,
                ts=float(item.get("time") or 0),
                meta={"raw_message": item.get("raw_message")},
            )
        )
    docs.sort(key=lambda d: d.ts)
    return docs


class QQOneBotAdapter(BaseAdapter):
    platform = "qq"

    def __init__(self, cfg: QQConfig, *, timeout: float = 20.0) -> None:
        super().__init__(timeout=timeout)
        self.cfg = cfg
        self._ws_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._handler: Callable[[Doc], Awaitable[None]] | None = None
        self._history_cache: dict[str, tuple[float, list[Doc]]] = {}
        self._cache_ttl = 45.0

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    # ------------------------------------------------------------ transport
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.access_token:
            headers["Authorization"] = f"Bearer {self.cfg.access_token}"
        return headers

    async def api(self, action: str, **params: Any) -> Any:
        url = f"{self.cfg.api_base.rstrip('/')}/{action}"
        try:
            resp = await self._client.post(url, json=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise AdapterError(f"OneBot 未连接（{action}）：{exc}") from exc
        if resp.status_code >= 400:
            raise AdapterError(f"OneBot {action} 返回 {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "failed":
            raise AdapterError(f"OneBot {action} 失败：{data.get('wording') or data.get('retcode')}")
        return data.get("data") if isinstance(data, dict) else data

    # ------------------------------------------------------------- outbound
    async def send(self, channel_id: str, text: str) -> dict[str, Any]:
        kind, target = parse_channel(channel_id)
        if kind == "group":
            data = await self.api(
                "send_group_msg", group_id=target, message=text, auto_escape=True
            )
        else:
            data = await self.api(
                "send_private_msg", user_id=target, message=text, auto_escape=True
            )
        return {"ok": True, "message_id": (data or {}).get("message_id"), "channel_id": channel_id}

    # ------------------------------------------------------------ retrieval
    async def fetch_history(self, channel_id: str, limit: int = 100, since: float | None = None) -> list[Doc]:
        kind, target = parse_channel(channel_id)
        limit = max(1, min(int(limit), 500))
        if kind == "group":
            payload = await self.api("get_group_msg_history", group_id=target, count=limit)
            info = None
            try:
                info = await self.api("get_group_info", group_id=target)
            except AdapterError:
                info = None
            name = (info or {}).get("group_name", "")
        else:
            payload = await self.api("get_friend_msg_history", user_id=target, count=limit)
            name = ""
        docs = history_payload_to_docs(payload, channel_id, name)
        if since is not None:
            docs = [d for d in docs if d.ts >= since]
        return docs

    async def list_groups(self) -> list[dict[str, Any]]:
        data = await self.api("get_group_list")
        return [g for g in (data or []) if isinstance(g, dict)]

    async def search(self, query: str, limit: int = 20, *, groups: list[int] | None = None, refresh: bool = True) -> list[Doc]:
        """QQ has no server-side message search, so search across watched groups.

        The local FTS index (see ``tools.social_search``) is the primary path;
        this method exists for the "抓最新再搜" case.
        """
        targets = groups if groups is not None else self.cfg.watch_groups
        docs: list[Doc] = []
        for gid in targets:
            channel_id = f"group:{gid}"
            cached = self._history_cache.get(channel_id)
            if refresh or not cached or time.time() - cached[0] > self._cache_ttl:
                try:
                    fresh = await self.fetch_history(channel_id, self.cfg.history_limit)
                except AdapterError:
                    fresh = cached[1] if cached else []
                self._history_cache[channel_id] = (time.time(), fresh)
            docs.extend(self._history_cache.get(channel_id, (0, []))[1])
        docs = merge_history(docs)
        needle = query.strip()
        if not needle:
            return docs[-limit:]
        return [d for d in docs if needle in d.text][-limit:]

    # ------------------------------------------------------------- inbound
    async def start(self, on_message: Callable[[Doc], Awaitable[None]] | None = None) -> None:
        if not (self.cfg.enabled and self.cfg.ws_url):
            return
        self._handler = on_message
        self._stop.clear()
        self._ws_task = asyncio.create_task(self._ws_loop(), name="qq-onebot-ws")

    async def _ws_loop(self) -> None:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.cfg.ws_url,
                    additional_headers=self._headers(),
                    ping_interval=30,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        doc = event_to_doc(event)
                        if doc and self._handler:
                            await self._handler(doc)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stop.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def stop(self) -> None:
        self._stop.set()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None
        await self.close()


__all__ = [
    "QQOneBotAdapter",
    "event_to_doc",
    "history_payload_to_docs",
    "parse_channel",
    "render_segments",
]
