from __future__ import annotations

from pathlib import Path

import pytest

from qq_agent.config import Config
from qq_agent.llm import LLM
from qq_agent.store import Store
from qq_agent.tools import ToolContext, ToolRegistry, build_tools


class FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, channel_id, text):
        self.sent.append((channel_id, text))
        return {"ok": True}


def make_ctx(tmp_path: Path, *, channel: str = "", dry_run: bool = True):
    cfg = Config()
    cfg.home = str(tmp_path)
    cfg.server.db_path = str(tmp_path / "data" / "qq" / "qq.db")
    cfg.qq.data_dir = str(tmp_path / "data" / "qq")
    cfg.qq.enabled = True
    cfg.qq.owner_uid = 10001
    cfg.qq.self_id = 10000
    cfg.qq.friend_whitelist = [10001]
    cfg.qq.dry_run = dry_run
    cfg.qq.quiet_hours = []
    cfg.qq.min_interval_seconds = 0
    cfg.qq.reply_delay_min = 0
    cfg.qq.reply_delay_max = 0
    store = Store(str(tmp_path / "data" / "qq" / "qq.db"))
    adapter = FakeAdapter()
    ctx = ToolContext(
        cfg=cfg,
        store=store,
        adapters={"qq": adapter},
        llm=LLM(cfg.agent),
        websearch=None,
    )
    if channel:
        ctx._extra["channel_id"] = channel
    return ctx, adapter, store


def test_core_toolset_is_qq_only():
    names = {spec.name for spec in build_tools(Config())}
    assert "social_search" in names
    assert "social_send" in names
    assert "qq_friends" in names
    assert "bilibili_video" not in names
    assert "wechat_article" not in names
    assert "dsh_run" not in names
    assert "send_file" not in names


def test_desktop_tools_are_opt_in():
    cfg = Config()
    cfg.desktop.enabled = True
    names = {spec.name for spec in build_tools(cfg)}
    assert {"dsh_status", "dsh_control", "dsh_run"} <= names


@pytest.mark.asyncio
async def test_social_send_dry_run_blocks(tmp_path: Path):
    ctx, adapter, store = make_ctx(tmp_path, channel="private:10001", dry_run=True)
    try:
        result = await ToolRegistry(ctx).call(
            "social_send", {"channel_id": "private:10001", "text": "hi"}
        )
        assert result["blocked"] is True
        assert "dry_run" in result["reason"]
        assert adapter.sent == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_social_send_owner_whitelist(tmp_path: Path):
    ctx, adapter, store = make_ctx(tmp_path, channel="private:10001", dry_run=False)
    try:
        result = await ToolRegistry(ctx).call(
            "social_send", {"channel_id": "private:10002", "text": "hi"}
        )
        assert result["blocked"] is True
        assert adapter.sent == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_self_check_owner_only(tmp_path: Path):
    ctx, adapter, store = make_ctx(tmp_path, channel="private:99999")
    try:
        registry = ToolRegistry(ctx)
        denied = await registry.call("self_check", {})
        assert denied["ok"] is False
        ctx._extra["channel_id"] = "private:10001"
        allowed = await registry.call("self_check", {})
        assert allowed["ok"] is True
        assert "白名单" in allowed["summary"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_read_log_owner_only(tmp_path: Path):
    ctx, adapter, store = make_ctx(tmp_path, channel="private:10001")
    try:
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "service.log").write_text("line one\nline two\n", encoding="utf-8")
        result = await ToolRegistry(ctx).call("read_log", {"lines": 5, "grep": "two"})
        assert result["ok"] is True
        assert "line two" in result["summary"]
    finally:
        store.close()
