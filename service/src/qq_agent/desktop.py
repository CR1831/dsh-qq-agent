"""Optional desktop-control tools.

This module is only loaded when ``[desktop].enabled = true`` and the optional
DSH bundle ``dsh-qq-agent-desktop`` is installed.  Without it the core service
has no DSH control surface at all.

Every tool is owner-only, and every state-changing call requires an explicit
``confirm=true`` from the model after the owner asked for it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


def _configured(ctx: Any) -> bool:
    return bool(getattr(ctx.cfg.desktop, "enabled", False))


def _owner(ctx: Any) -> bool:
    owner = str(getattr(ctx.cfg.qq, "owner_uid", "") or "")
    channel = str(ctx._extra.get("channel_id") or "")
    return bool(owner) and channel == f"private:{owner}"


def _deny() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "这台机器的事，只在号主私信里处理。",
        "hint": "一句挡回去，别解释规则。",
    }


def _configure(ctx: Any) -> None:
    from . import dsh_bridge

    dsh_bridge.configure(
        dsh_exe=str(getattr(ctx.cfg.desktop, "dsh_exe", "") or ""),
        dsh_home=str(getattr(ctx.cfg.desktop, "dsh_home", "") or ""),
        workspace=str(getattr(ctx.cfg.desktop, "cwd", "") or ""),
    )


async def tool_dsh_status(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    if not _configured(ctx) or not _owner(ctx):
        return _deny()
    from . import dsh_bridge

    _configure(ctx)
    try:
        return await asyncio.to_thread(dsh_bridge.status)
    except Exception as exc:
        return {"ok": False, "error": f"查询失败：{type(exc).__name__}: {exc}"}


async def tool_dsh_control(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    if not _configured(ctx) or not _owner(ctx):
        return _deny()
    action = str(args.get("action") or "").strip().lower()
    confirm = bool(args.get("confirm"))
    if action not in {"restart", "start", "stop"}:
        return {"ok": False, "error": f"不认识的动作：{action}（restart/start/stop）"}
    from . import dsh_bridge

    _configure(ctx)
    try:
        return await asyncio.to_thread(dsh_bridge.control, action, confirm=confirm)
    except Exception as exc:
        return {"ok": False, "error": f"执行失败：{type(exc).__name__}: {exc}"}


async def tool_dsh_run(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    if not _configured(ctx) or not _owner(ctx):
        return _deny()
    text = str(args.get("instruction") or "").strip()
    if not text:
        return {"ok": False, "error": "指令是空的"}
    try:
        timeout_ms = int(args.get("timeout_ms") or getattr(ctx.cfg.desktop, "timeout_ms", 90000))
    except (TypeError, ValueError):
        timeout_ms = 90000
    timeout_ms = max(10000, min(timeout_ms, 280000))

    background = bool(args.get("background"))
    session = str(args.get("session") or "").strip()
    preset = str(args.get("preset") or "").strip()
    isolate = bool(args.get("isolate"))
    from . import dsh_bg, dsh_bridge

    _configure(ctx)
    default_session = str(getattr(ctx.cfg.desktop, "session_id", "") or "qq-agent")

    workdir: str | None = None
    if isolate:
        session = session or f"task-{time.strftime('%m%d-%H%M%S')}"
        try:
            workdir = str(dsh_bridge.task_dir(text, tag=session))
        except OSError:
            workdir = None

    if background:
        try:
            return await asyncio.to_thread(
                dsh_bg.submit,
                ctx.cfg.server.db_path,
                text,
                session_id=session or default_session,
                preset=preset or None,
                workdir=workdir,
            )
        except Exception as exc:
            return {"ok": False, "error": f"投递失败：{type(exc).__name__}: {exc}"}

    try:
        result = await asyncio.to_thread(
            dsh_bridge.run_task,
            text,
            timeout_ms=timeout_ms,
            session_id=session or None,
            preset=preset or None,
            workdir=workdir,
        )
    except Exception as exc:
        return {"ok": False, "error": f"调用失败：{type(exc).__name__}: {exc}"}

    # A sync call that did not finish is still a task: put it in the queue so
    # the service can notify the owner when DSH is done.
    if result.get("ok") and not result.get("finished"):
        try:
            adopted = await asyncio.to_thread(
                dsh_bg.adopt,
                ctx.cfg.server.db_path,
                text,
                session_id=str(result.get("sessionId") or session or default_session),
            )
            result["adopted"] = adopted
            if adopted.get("ok"):
                result["summary"] = (
                    f"这活比预期久，我先帮你盯着（任务 {adopted.get('taskId')}），跑完私信你。"
                )
        except Exception as exc:
            result["adoptError"] = f"{type(exc).__name__}: {exc}"

    reply = str(result.get("reply") or "").strip()
    if result.get("ok") and reply:
        result["summary"] = reply
    elif result.get("ok") and not result.get("summary"):
        result["summary"] = result.get("hint") or "投过去了，但这轮没读到回复。"
    return result


async def tool_dsh_result(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    if not _configured(ctx) or not _owner(ctx):
        return _deny()
    session_id = str(args.get("session_id") or getattr(ctx.cfg.desktop, "session_id", "qq-agent"))
    preset = str(args.get("preset") or "").strip() or None
    from . import dsh_bridge

    _configure(ctx)
    try:
        return await asyncio.to_thread(dsh_bridge.check_result, session_id, preset)
    except Exception as exc:
        return {"ok": False, "error": f"查询失败：{type(exc).__name__}: {exc}"}


def build_desktop_tools() -> list[Any]:
    from .tools import ToolSpec

    return [
        ToolSpec(
            name="dsh_status",
            description=(
                "查 DeepSeek Harness 的运行状态：是否在跑、进程数、内存、GUI 端口、最近会话。"
                "只读；只在号主私信里可用。可选桌面控制包。"
            ),
            parameters={"type": "object", "additionalProperties": False, "properties": {}},
            handler=tool_dsh_status,
        ),
        ToolSpec(
            name="dsh_control",
            description=(
                "从外部操纵 DSH：restart / start / stop。"
                "restart 会中断 DSH 里正在进行的会话，必须先跟号主确认，"
                "他明确同意后才把 confirm 传成 true；没确认就只问一句。"
                "只在号主私信里可用。可选桌面控制包。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["restart", "start", "stop"]},
                    "confirm": {"type": "boolean", "description": "号主已明确同意才传 true"},
                },
                "required": ["action"],
            },
            handler=tool_dsh_control,
        ),
        ToolSpec(
            name="dsh_run",
            description=(
                "把号主的指令交给 DSH 里的 agent 执行，并把结果带回来。"
                "适合查资料、改文件、跑脚本。只在号主私信里可用。"
                "session 填新名字 = 开独立会话；preset 选能力档；"
                "isolate=true 自动开新会话和新工作目录。"
                "长任务把 background 设成 true，立即返回任务号，跑完会主动私信号主；"
                "也可以用 dsh_result 查。可选桌面控制包。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instruction": {"type": "string", "description": "交给 DSH 的原话"},
                    "background": {"type": "boolean", "description": "长任务模式；完成/超时会通知号主"},
                    "timeout_ms": {"type": "integer", "description": "同步模式最多等多久，默认 90000"},
                    "session": {"type": "string", "description": "会话名；新名字 = 独立上下文"},
                    "preset": {"type": "string", "description": "DSH agent 能力档，例如 qq-worker / qq-coder；不填用 standard"},
                    "isolate": {"type": "boolean", "description": "自动开新会话 + 新任务目录"},
                },
                "required": ["instruction"],
            },
            handler=tool_dsh_run,
        ),
        ToolSpec(
            name="dsh_result",
            description=(
                "查一个 background=true 的 DSH 任务有没有跑完、有没有新回复。"
                "只在号主私信里可用。可选桌面控制包。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "后台任务的会话 id；不填用 [desktop].session_id",
                    },
                    "preset": {
                        "type": "string",
                        "description": "可选：任务使用的 agent preset",
                    },
                },
            },
            handler=tool_dsh_result,
        ),
    ]


__all__ = [
    "build_desktop_tools",
    "tool_dsh_control",
    "tool_dsh_result",
    "tool_dsh_run",
    "tool_dsh_status",
]
