"""长任务队列：投递后不等，跑完主动告诉号主。

为什么单独一个模块：`dsh_run` 的同步模式最多挂几分钟，跑得久的活
（写文档、查资料、批处理）必须"先收下、后回报"，否则 QQ 那条消息就一直卡着。

存储：`data/qq/dsh_tasks.json`（跟着 QQ 隔离目录走，重启不丢）。
轮询：`QQ 服务` 的监听循环里每 15 秒 tick 一次（见 qq_dm._dsh_bg_loop）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import dsh_bridge

MAX_TASKS = 40          # 只留最近 40 条，别把文件撑爆
TAKE_SECONDS = 30 * 60  # 超过 30 分钟还没结果的，标成超时不再追


def _path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / "dsh_tasks.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    items = sorted(data.items(), key=lambda kv: kv[1].get("submittedAt", 0), reverse=True)[:MAX_TASKS]
    try:
        path.write_bytes(
            json.dumps(dict(items), ensure_ascii=False, indent=1).encode("utf-8")
        )
    except OSError:
        pass


def adopt(
    db_path: str | Path, text: str, *, session_id: str = "desk-bridge"
) -> dict[str, Any]:
    """把一次**已经投递过**但没跑完的任务登记进队列，之后由 tick 继续追。

    同步模式超时后会走这里——不然 DSH 跑完了也没人回头通知（2026-11-09 踩过）。
    """
    path = _path(db_path)
    data = _load(path)

    # 同一个会话里已经有一条在盯了，别重复登记
    for task in data.values():
        if task.get("status") == "pending" and task.get("sessionId") == session_id:
            return {"ok": True, "taskId": task["id"], "alreadyWatching": True}

    task_id = time.strftime("T%m%d-%H%M%S") + f"-{len(data) % 100:02d}"
    data[task_id] = {
        "id": task_id,
        "text": text,
        "sessionId": session_id,
        "submittedAt": time.time(),
        "status": "pending",
        "noticed": False,
        "adopted": True,
    }
    _save(path, data)
    return {"ok": True, "taskId": task_id, "sessionId": session_id}


def submit(
    db_path: str | Path,
    text: str,
    *,
    session_id: str = "desk-bridge",
    preset: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    """投一条长任务。成功返回任务记录（含 id）。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "指令是空的"}

    result = dsh_bridge.run_task(
        text,
        background=True,
        timeout_ms=20000,
        session_id=session_id,
        preset=preset,
        workdir=workdir,
    )
    if not result.get("ok"):
        return result

    path = _path(db_path)
    data = _load(path)
    task_id = time.strftime("T%m%d-%H%M%S") + f"-{len(data) % 100:02d}"
    data[task_id] = {
        "id": task_id,
        "text": text,
        "sessionId": result.get("sessionId") or session_id,
        "preset": preset or "",
        "workdir": workdir or "",
        "submittedAt": time.time(),
        "status": "pending",
        "noticed": False,
    }
    _save(path, data)
    return {
        "ok": True,
        "taskId": task_id,
        "sessionId": data[task_id]["sessionId"],
        "preset": preset or "standard",
        "workdir": workdir or "",
        "summary": f"已收下（任务号 {task_id}），跑完我会告诉你。",
    }


def _label(text: str) -> str:
    """给任务起个短名字，通知里指代用——别把任务号念给号主听。"""
    flat = " ".join((text or "").split())
    return flat[:14] + ("…" if len(flat) > 14 else "")


def tick(db_path: str | Path) -> list[dict[str, Any]]:
    """检查所有 pending 任务。返回这一轮刚完成、需要通知的条目。"""
    path = _path(db_path)
    data = _load(path)
    if not data:
        return []

    finished: list[dict[str, Any]] = []
    dirty = False
    now = time.time()

    for task in data.values():
        if task.get("status") != "pending":
            continue
        label = _label(str(task.get("text") or ""))
        age = now - float(task.get("submittedAt") or 0)
        if age > TAKE_SECONDS:
            task["status"] = "timeout"
            task["finishedAt"] = now
            dirty = True
            finished.append(
                {
                    "id": task["id"],
                    "text": task["text"],
                    "status": "timeout",
                    # 别用「任务 T1109-xxx 超时了」这种通知腔
                    "notice": f"「{label}」没动静了||半小时没回音，估计卡住了",
                }
            )
            continue

        res = dsh_bridge.check_result(
            str(task.get("sessionId") or "desk-bridge"),
            preset=(str(task.get("preset") or "") or None),
        )
        if not res.get("ok"):
            continue  # DSH 没开/重启中，下轮再试

        if res.get("finished") and res.get("reply"):
            task["status"] = "done"
            task["finishedAt"] = now
            task["result"] = str(res.get("reply"))[:4000]
            dirty = True
            finished.append(
                {
                    "id": task["id"],
                    "text": task["text"],
                    "status": "done",
                    "reply": task["result"],
                    "notice": f"「{label}」弄完了||{task['result'][:1200]}",
                }
            )

    if dirty:
        _save(path, data)
    return finished


def pending_count(db_path: str | Path) -> int:
    data = _load(_path(db_path))
    return sum(1 for t in data.values() if t.get("status") == "pending")


__all__ = ["submit", "adopt", "tick", "pending_count"]
