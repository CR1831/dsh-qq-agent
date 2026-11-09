"""Normalised data model shared by every platform adapter and the retriever.

Everything a platform can hand us — a chat message, a video, an article, a
comment, a danmaku line — is stored as one ``Doc``.  That keeps storage,
filtering and ranking platform agnostic: an adapter only has to translate its
native payload into ``Doc`` and the rest of the system works unchanged.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Platform = Literal["qq", "wechat", "bilibili"]
Kind = Literal["message", "video", "article", "comment", "danmaku", "dynamic", "profile"]


def _fingerprint(*parts: Any) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:20]


@dataclass
class Doc:
    doc_id: str
    platform: str
    kind: str
    text: str
    ts: float = 0.0
    channel_id: str = ""
    channel_name: str = ""
    author_id: str = ""
    author_name: str = ""
    title: str = ""
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ ids
    @staticmethod
    def make_id(platform: str, kind: str, native_id: str, text: str = "") -> str:
        """Stable id so re-ingesting the same payload is idempotent."""
        if native_id:
            return f"{platform}:{kind}:{native_id}"
        return f"{platform}:{kind}:{_fingerprint(platform, kind, text)}"

    @classmethod
    def message(
        cls,
        platform: str,
        channel_id: str,
        sender_id: str,
        text: str,
        *,
        native_id: str = "",
        channel_name: str = "",
        sender_name: str = "",
        ts: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "Doc":
        return cls(
            doc_id=cls.make_id(platform, "message", native_id or f"{channel_id}:{ts}:{sender_id}", text),
            platform=platform,
            kind="message",
            text=text.strip(),
            ts=ts if ts is not None else time.time(),
            channel_id=str(channel_id),
            channel_name=channel_name,
            author_id=str(sender_id),
            author_name=sender_name or str(sender_id),
            meta=meta or {},
        )

    # ---------------------------------------------------------------- views
    @property
    def when(self) -> str:
        if not self.ts:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts))

    def one_line(self, width: int = 120) -> str:
        body = " ".join(self.text.split())
        if len(body) > width:
            body = body[: width - 1] + "…"
        head = self.title or body
        if self.title:
            head = f"{self.title} | {body}" if body else self.title
        prefix = f"[{self.platform}/{self.kind}]"
        who = f" @{self.author_name}" if self.author_name and self.author_name != self.author_id else ""
        when = f" {self.when}" if self.when else ""
        url = f" {self.url}" if self.url else ""
        return f"{prefix}{when}{who}: {head}{url}".strip()

    def to_row(self) -> tuple:
        return (
            self.doc_id,
            self.platform,
            self.kind,
            self.channel_id,
            self.channel_name,
            self.author_id,
            self.author_name,
            self.title,
            self.text,
            self.url,
            float(self.ts or 0),
            json.dumps(self.meta, ensure_ascii=False),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "platform": self.platform,
            "kind": self.kind,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "title": self.title,
            "text": self.text,
            "url": self.url,
            "ts": self.ts,
            "when": self.when,
            "meta": self.meta,
        }


def dedupe_docs(docs: Iterable[Doc]) -> list[Doc]:
    seen: set[str] = set()
    out: list[Doc] = []
    for doc in docs:
        if doc.doc_id in seen:
            continue
        seen.add(doc.doc_id)
        out.append(doc)
    return out
