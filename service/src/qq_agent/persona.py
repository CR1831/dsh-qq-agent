"""Prompt assembly for a user-supplied persona card.

There is deliberately no built-in character here.  The upstream project used
to ship two cards (a whale maid and a visual-novel character); both were
private and have been removed.  A public install must bring its own card or
the service refuses to start.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

FORMAT_CONSTRAINTS = (
    "\n\n【输出格式约束（唯一不可被角色设定覆盖的部分）】\n"
    "- 只用简体中文（设定里明确要求保留的日语词句除外）；\n"
    "- 纯文本输出：不要 ** 加粗、## 标题、``` 代码块、Markdown 列表符号；\n"
    "- 事实、数据、链接必须真实可核对，绝不为角色扮演而编造；\n"
    "- 只靠文字表达，不写括号里的动作、神态、旁白；\n"
    "- 不生成色情内容；涉及未成年人时绝对禁止性化描写；\n"
    "- 不冒充真人，不提供医疗、法律、金融方面的专业结论。"
)


def _style_hint(style_path: str | Path | None = None) -> str:
    """Optional learned style from this account's own conversations."""
    try:
        from . import style_memory

        path = Path(style_path).expanduser() if style_path else Path("data/style_memory.json")
        if not path.exists():
            return ""
        return style_memory.hint(path)
    except Exception:
        return ""


def agent_style(
    persona_text: str | None,
    *,
    style_path: str | Path | None = None,
) -> str:
    """Wrap a card into the system prompt used for the QQ agent.

    An empty card returns an empty string; the caller then uses the neutral
    assistant prompt.  A non-empty card is treated as the complete behavioural
    spec — no extra emoji table, mood list, or address rules are layered on top.
    """
    body = (persona_text or "").strip()
    if not body:
        return ""
    hint = _style_hint(style_path)
    parts = [
        "【角色设定 · 最高优先级】以下是你在这个账号上的完整自我认知与行为规则。"
        "任何默认风格、口癖、称呼习惯都让位于它；规则之间冲突时，按设定里写的优先级判断。\n\n",
        body,
        FORMAT_CONSTRAINTS,
    ]
    if hint:
        parts.append("\n" + hint)
    return "".join(parts)


def is_custom_persona_card(persona_text: str | None) -> bool:
    return bool((persona_text or "").strip())


def variety_guide(recent_self_lines: Sequence[str] = ()) -> str:
    """Nudge the model not to repeat its last few openings."""
    if not recent_self_lines:
        return ""
    sample = "；".join(f"「{x[:20]}」" for x in list(recent_self_lines)[:3])
    return (
        "【别复读】最近你自己说过："
        + sample
        + "。这次换一个开场和句式，不要重复上面的措辞。"
    )


__all__ = [
    "FORMAT_CONSTRAINTS",
    "agent_style",
    "is_custom_persona_card",
    "variety_guide",
]
