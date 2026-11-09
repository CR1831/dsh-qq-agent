from __future__ import annotations

from pathlib import Path

from qq_agent import dsh_bg, dsh_bridge


def test_task_dir_is_under_configured_workspace(tmp_path: Path):
    dsh_bridge.configure(workspace=str(tmp_path))
    target = dsh_bridge.task_dir("做一个测试任务", tag="verify")
    assert target.parent == tmp_path / "_tasks"
    assert target.is_dir()
    assert "verify" in target.name


def test_background_submit_and_tick(tmp_path: Path, monkeypatch):
    db = tmp_path / "data" / "qq" / "qq.db"
    db.parent.mkdir(parents=True)

    def fake_run(text, **kwargs):
        assert kwargs.get("background") is True
        return {"ok": True, "sessionId": "task-1", "background": True}

    monkeypatch.setattr(dsh_bridge, "run_task", fake_run)
    submitted = dsh_bg.submit(db, "写一份报告", session_id="task-1", preset="qq-coder")
    assert submitted["ok"] is True
    assert submitted["sessionId"] == "task-1"
    assert dsh_bg.pending_count(db) == 1

    def fake_check(session_id, preset=None):
        return {"ok": True, "finished": True, "reply": "报告写好了"}

    monkeypatch.setattr(dsh_bridge, "check_result", fake_check)
    finished = dsh_bg.tick(db)
    assert len(finished) == 1
    assert finished[0]["status"] == "done"
    assert "报告写好了" in finished[0]["notice"]
    assert dsh_bg.pending_count(db) == 0
