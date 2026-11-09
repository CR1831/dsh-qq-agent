"""A small status file the DSH panel can read without touching the data dir."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

STATUS_NAME = "status.json"
STALE_SECONDS = 90


def status_path(home: str | Path) -> Path:
    return Path(home).expanduser().resolve() / STATUS_NAME


def write_status(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["heartbeatAt"] = time.time()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)


def read_status(home: str | Path) -> dict[str, Any] | None:
    path = status_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["stale"] = bool(
        time.time() - float(data.get("heartbeatAt") or 0) > STALE_SECONDS
    )
    return data


def initial_status(cfg: Any, card: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "version": "0.1.0",
        "pid": os.getpid(),
        "startedAt": time.time(),
        "heartbeatAt": time.time(),
        "ws": False,
        "dryRun": bool(getattr(cfg.qq, "dry_run", True)),
        "qq": {
            "uin": str(getattr(cfg.qq, "self_id", "") or ""),
            "apiBase": str(getattr(cfg.qq, "api_base", "") or ""),
            "wsUrl": str(getattr(cfg.qq, "ws_url", "") or ""),
            "ownerUid": str(getattr(cfg.qq, "owner_uid", "") or ""),
            "displayName": str(getattr(cfg.qq, "display_name", "") or ""),
        },
        "persona": card.status() if card is not None else {
            "loaded": False,
            "name": "",
            "source": "",
            "format": "",
            "chars": 0,
            "warnings": [],
        },
        "whitelist": {
            "friends": len(getattr(cfg.qq, "friend_whitelist", None) or []),
            "groups": len(getattr(cfg.qq, "group_whitelist", None) or []),
        },
        "lastError": "",
    }


async def heartbeat(
    path: str | Path,
    data: dict[str, Any],
    *,
    interval: float = 20.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Rewrite the status file periodically until cancelled or stopped."""
    while stop is None or not stop.is_set():
        try:
            write_status(path, data)
        except OSError:
            pass
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = [
    "STATUS_NAME",
    "STALE_SECONDS",
    "initial_status",
    "heartbeat",
    "read_status",
    "status_path",
    "write_status",
]
