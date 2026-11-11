from __future__ import annotations

import json
from pathlib import Path

from qq_agent.qq_watchdog import Watchdog


def make_watchdog(tmp_path: Path, **kwargs) -> Watchdog:
    return Watchdog(
        tmp_path / "ALERT.json",
        threshold_min=1,
        auto_restart=True,
        restart_after_min=0.05,
        qq_number="10000",
        napcat_home=str(tmp_path / "napcat"),
        **kwargs,
    )


def test_auto_restart_runs_once_and_resets(tmp_path: Path):
    calls: list[str] = []
    wd = make_watchdog(tmp_path)
    wd._restart_napcat = lambda: (calls.append("restart"), True)[1]
    wd._notify = lambda *args: calls.append("notify")

    assert wd.mark_down(now=100.0) is False
    assert wd.mark_down(now=104.0) is False   # restart, but not alert yet
    assert calls == ["restart"]
    assert wd.restarted is True

    assert wd.mark_down(now=108.0) is False
    assert calls == ["restart"]               # no second restart

    wd.mark_up()
    assert wd.restarted is False

    wd.mark_down(now=200.0)
    wd.mark_down(now=204.0)
    assert calls == ["restart", "restart"]    # a new outage may retry once


def test_alert_after_threshold_includes_restart_flag(tmp_path: Path):
    notes: list[str] = []
    wd = make_watchdog(tmp_path)
    wd._restart_napcat = lambda: True
    wd._notify = lambda title, body: notes.append(body)
    wd._write = lambda payload: notes.append(json.dumps(payload))

    wd.mark_down(now=100.0)
    wd.mark_down(now=200.0)
    assert any("已尝试自动重启" in note for note in notes)
    assert any("auto_restarted" in note for note in notes)


def test_auto_restart_disabled_by_default(tmp_path: Path):
    calls: list[str] = []
    wd = Watchdog(
        tmp_path / "ALERT.json",
        threshold_min=1,
        qq_number="10000",
        napcat_home=str(tmp_path / "napcat"),
    )
    wd._restart_napcat = lambda: (calls.append("restart"), True)[1]
    wd._notify = lambda *args: None
    wd.mark_down(now=100.0)
    wd.mark_down(now=200.0)
    assert calls == []


def test_restart_requires_launcher(tmp_path: Path):
    wd = make_watchdog(tmp_path)
    # napcat dir doesn't exist
    assert wd._restart_napcat() is False
