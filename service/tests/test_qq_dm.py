from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from qq_agent.config import Config
from qq_agent.qq_dm import QQReplier, looks_like_grant

OWNER = 10001
BOT = 10000
FRIEND = 10002
GROUP = 20001


class FakeStore:
    def __init__(self):
        self.data = {}

    def meta_get(self, key):
        return self.data.get(key, "")

    def meta_set(self, key, value):
        self.data[key] = value


class FakeAgent:
    def __init__(self, text="嗯，在的。"):
        self.calls = 0
        self.text = text
        self.last_ctx = ""

    async def answer(self, *args, **kwargs):
        self.calls += 1
        self.last_ctx = kwargs.get("extra_context", "")
        return SimpleNamespace(text=self.text)


class FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, channel_id, text):
        self.sent.append((channel_id, text))
        return {"ok": True}


def make(tmp_path: Path, **qq_kwargs):
    cfg = Config()
    cfg.home = str(tmp_path)
    cfg.server.db_path = str(tmp_path / "data" / "qq" / "qq.db")
    cfg.qq.data_dir = str(tmp_path / "data" / "qq")
    cfg.qq.enabled = True
    cfg.qq.self_id = BOT
    cfg.qq.owner_uid = OWNER
    cfg.qq.friend_whitelist = [OWNER]
    cfg.qq.group_whitelist = []
    cfg.qq.quiet_hours = []
    cfg.qq.reply_delay_min = 0
    cfg.qq.reply_delay_max = 0
    cfg.qq.min_interval_seconds = 0
    cfg.qq.dry_run = True
    for key, value in qq_kwargs.items():
        setattr(cfg.qq, key, value)
    store = FakeStore()
    agent = FakeAgent()
    adapter = FakeAdapter()
    return QQReplier(cfg, store, agent, {"qq": adapter}), cfg, agent, adapter


def private(uid: int, text: str):
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": uid,
        "message": [{"type": "text", "data": {"text": text}}],
    }


def group(uid: int, text: str, *, at: bool = True):
    message = []
    if at:
        message.append({"type": "at", "data": {"qq": str(BOT)}})
    message.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": GROUP,
        "user_id": uid,
        "message": message,
    }


@pytest.mark.asyncio
async def test_stranger_private_ignored(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    assert await replier.handle(private(FRIEND, "在吗")) is None
    assert agent.calls == 0
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_owner_private_dry_run_does_not_send(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    body = await replier.handle(private(OWNER, "在吗"))
    assert body == "嗯，在的。"
    assert agent.calls == 1
    assert adapter.sent == []          # dry_run
    assert "测试大学" not in agent.last_ctx


@pytest.mark.asyncio
async def test_owner_private_send_when_dry_run_off(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path, dry_run=False)
    body = await replier.handle(private(OWNER, "在吗"))
    assert body == "嗯，在的。"
    assert adapter.sent == [(f"private:{OWNER}", "嗯，在的。")]


@pytest.mark.asyncio
async def test_group_requires_at(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path, group_whitelist=[GROUP], group_require_at=True)
    assert await replier.handle(group(FRIEND, "在吗", at=False)) is None
    assert agent.calls == 0


@pytest.mark.asyncio
async def test_group_with_at_replies(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path, group_whitelist=[GROUP], group_require_at=True)
    await replier.handle(group(FRIEND, "在吗", at=True))
    assert agent.calls == 1
    assert "群里" in agent.last_ctx


@pytest.mark.asyncio
async def test_group_not_whitelisted_ignored(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    assert await replier.handle(group(FRIEND, "在吗", at=True)) is None
    assert agent.calls == 0


@pytest.mark.asyncio
async def test_owner_note_has_no_private_facts(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    note = replier.role_note(str(OWNER), f"private:{OWNER}")
    assert "示例大学" not in note
    assert "18 岁" not in note
    assert "生日" not in note


@pytest.mark.asyncio
async def test_high_risk_gets_guidance(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    await replier.handle(private(OWNER, "我不想活了"))
    assert "情绪支持" in agent.last_ctx
    assert "120" in agent.last_ctx or "110" in agent.last_ctx


@pytest.mark.asyncio
async def test_friend_request_ignored(tmp_path: Path):
    replier, cfg, agent, adapter = make(tmp_path)
    event = {"post_type": "request", "request_type": "friend", "user_id": FRIEND}
    assert await replier.handle(event) is None
    assert adapter.sent == []


def test_grant_covers_find_me_but_not_refusal():
    assert looks_like_grant("可以找我")
    assert looks_like_grant("随时找我")
    assert looks_like_grant("发吧")
    assert not looks_like_grant("别找我了")
    assert not looks_like_grant("他刚才说发吧")
