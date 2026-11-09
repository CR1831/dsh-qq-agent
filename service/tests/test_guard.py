from __future__ import annotations

from qq_agent.config import Config
from qq_agent.qq_guard import allow_send


class FakeStore:
    def __init__(self):
        self.data = {}

    def meta_get(self, key):
        return self.data.get(key, "")

    def meta_set(self, key, value):
        self.data[key] = value


def make_cfg(**kwargs) -> Config:
    cfg = Config()
    cfg.qq.enabled = True
    cfg.qq.owner_uid = 10001
    cfg.qq.self_id = 10000
    cfg.qq.friend_whitelist = [10001]
    cfg.qq.group_whitelist = [20001]
    cfg.qq.quiet_hours = []
    cfg.qq.min_interval_seconds = 0
    cfg.qq.reply_delay_min = 0
    cfg.qq.reply_delay_max = 0
    for key, value in kwargs.items():
        setattr(cfg.qq, key, value)
    return cfg


def test_empty_whitelist_fails_closed():
    cfg = make_cfg(friend_whitelist=[])
    decision = allow_send(cfg, FakeStore(), "private:10001")
    assert decision.allow is False
    assert "白名单" in decision.reason


def test_whitelisted_private_allowed():
    decision = allow_send(make_cfg(), FakeStore(), "private:10001")
    assert decision.allow is True


def test_stranger_private_blocked():
    decision = allow_send(make_cfg(), FakeStore(), "private:10002")
    assert decision.allow is False


def test_quiet_hours_blocked():
    cfg = make_cfg(quiet_hours=[12])
    decision = allow_send(cfg, FakeStore(), "private:10001", now=0)
    # localtime(0) is not guaranteed to be 12; use a timestamp built from now.
    import time

    stamp = time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, -1))
    decision = allow_send(cfg, FakeStore(), "private:10001", now=stamp)
    assert decision.allow is False
    assert "免打扰" in decision.reason


def test_proactive_requires_grant():
    cfg = make_cfg(proactive=False)
    decision = allow_send(cfg, FakeStore(), "private:10001", proactive=True)
    assert decision.allow is False
    assert "主动发送" in decision.reason


def test_group_whitelist():
    cfg = make_cfg()
    assert allow_send(cfg, FakeStore(), "group:20001").allow is True
    assert allow_send(cfg, FakeStore(), "group:29999").allow is False


def test_draft_skips_quiet_and_rate_but_keeps_whitelist():
    cfg = make_cfg(quiet_hours=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23])
    assert allow_send(cfg, FakeStore(), "private:10001", draft=True).allow is True
    assert allow_send(cfg, FakeStore(), "private:10002", draft=True).allow is False

