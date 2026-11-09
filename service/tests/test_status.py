from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from qq_agent.config import Config
from qq_agent.status import (
    STALE_SECONDS,
    initial_status,
    read_status,
    status_path,
    write_status,
)


class Card:
    def status(self):
        return {
            "loaded": True,
            "name": "测试角色",
            "source": "/tmp/persona.md",
            "format": "markdown",
            "chars": 120,
            "warnings": [],
        }


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.home = str(tmp_path)
    cfg.qq.enabled = True
    cfg.qq.self_id = 10000
    cfg.qq.owner_uid = 10001
    cfg.qq.friend_whitelist = [10001]
    cfg.qq.dry_run = True
    return cfg


def test_status_roundtrip(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    data = initial_status(cfg, Card())
    write_status(status_path(cfg.home), data)
    loaded = read_status(cfg.home)
    assert loaded is not None
    assert loaded["persona"]["name"] == "测试角色"
    assert loaded["dryRun"] is True
    assert loaded["stale"] is False


def test_stale_heartbeat(tmp_path: Path):
    import json

    cfg = make_cfg(tmp_path)
    data = initial_status(cfg, Card())
    data["heartbeatAt"] = time.time() - STALE_SECONDS - 5
    path = status_path(cfg.home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = read_status(cfg.home)
    assert loaded is not None
    assert loaded["stale"] is True


def test_missing_status(tmp_path: Path):
    assert read_status(tmp_path) is None
