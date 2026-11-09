"""Optional desktop-control tools.

This module is only loaded when ``[desktop].enabled = true`` and the optional
DSH bundle ``dsh-qq-agent-desktop`` is installed.  Without it the core service
has no DSH control surface at all.

Every tool is owner-only, and every state-changing call requires an explicit
``confirm=true`` from the model after the owner asked for it.
"""

from __future__ import annotations

import asyncio
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
    background = bool(args.get("background"))
    timeout_ms = int(args.get("timeout_ms") or getattr(ctx.cfg.desktop, "timeout_ms", 90000))
    from . import dsh_bridge

    _configure(ctx)
    try:
        result = await asyncio.to_thread(
            dsh_bridge.run_task, text, timeout_ms=timeout_ms, background=background
        )
    except Exception as exc:
        return {"ok": False, "error": f"调用失败：{type(exc).__name__}: {exc}"}
    reply = str(result.get("reply") or "").strip()
    if result.get("ok") and reply:
        result["summary"] = reply
    return result


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
                "长任务把 background 设成 true，立刻拿任务号；之后用 /desk/result 查。"
                "可选桌面控制包。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instruction": {"type": "string", "description": "交给 DSH 的原话"},
                    "background": {"type": "boolean", "description": "长任务模式"},
                    "timeout_ms": {"type": "integer", "description": "同步模式最多等多久，默认 90000"},
                },
                "required": ["instruction"],
            },
            handler=tool_dsh_run,
        ),
    ]


__all__ = ["build_desktop_tools", "tool_dsh_control", "tool_dsh_run", "tool_dsh_status"]
