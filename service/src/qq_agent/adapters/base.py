"""Adapter contract.

An adapter is a bidirectional translator for exactly one platform:

* inbound  — platform event  -> ``Doc`` (+ optional reply hook)
* outbound — text            -> platform API call
* retrieval— platform query  -> ``list[Doc]``

Adapters never contain business logic (no filtering, no prompting).  That keeps
the filtering pipeline in one place and makes adding a new platform a matter of
one file.
"""

from __future__ import annotations

import abc
import time
from typing import Any, Awaitable, Callable, Sequence

import httpx

from ..models import Doc
from ..net import sanitize_proxy_env

sanitize_proxy_env()


class AdapterError(RuntimeError):
    pass


class RateLimiter:
    """Simple minimum-interval gate, used to stay polite with platform APIs."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, min_interval)
        self._last = 0.0

    async def wait(self) -> None:
        if not self.min_interval:
            return
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            import asyncio

            await asyncio.sleep(self.min_interval - delta)
        self._last = time.monotonic()


class BaseAdapter(abc.ABC):
    platform: str = "base"

    def __init__(self, *, timeout: float = 20.0, user_agent: str | None = None) -> None:
        headers = {"User-Agent": user_agent} if user_agent else None
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)
        self._closed = False

    @property
    def enabled(self) -> bool:
        return True

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()

    # ------------------------------------------------------------ lifecycle
    async def start(self, on_message: Callable[[Doc], Awaitable[None]] | None = None) -> None:
        """Begin receiving events. Default: nothing to do (webhook-driven)."""

    async def stop(self) -> None:
        await self.close()

    # ------------------------------------------------------------- outbound
    @abc.abstractmethod
    async def send(self, channel_id: str, text: str) -> dict[str, Any]:
        """Send a plain-text message; return a platform receipt."""

    # ------------------------------------------------------------ retrieval
    @abc.abstractmethod
    async def fetch_history(self, channel_id: str, limit: int = 100, since: float | None = None) -> list[Doc]:
        """Pull recent messages from one channel."""

    async def search(self, query: str, limit: int = 20, **kwargs: Any) -> list[Doc]:
        """Search inside the platform. Adapters without an API return []."""
        return []

    # ------------------------------------------------------------- helpers
    async def _get_json(self, url: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.platform} 请求失败：{exc}") from exc
        if resp.status_code >= 400:
            raise AdapterError(f"{self.platform} 返回 {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise AdapterError(f"{self.platform} 返回非 JSON：{resp.text[:200]}") from exc

    async def _post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        try:
            resp = await self._client.post(url, json=payload, **kwargs)
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.platform} 请求失败：{exc}") from exc
        if resp.status_code >= 400:
            raise AdapterError(f"{self.platform} 返回 {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise AdapterError(f"{self.platform} 返回非 JSON：{resp.text[:200]}") from exc


def merge_history(*groups: Sequence[Doc]) -> list[Doc]:
    seen: set[str] = set()
    merged: list[Doc] = []
    for group in groups:
        for doc in group:
            if doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            merged.append(doc)
    merged.sort(key=lambda d: d.ts)
    return merged


__all__ = ["BaseAdapter", "AdapterError", "RateLimiter", "merge_history"]
