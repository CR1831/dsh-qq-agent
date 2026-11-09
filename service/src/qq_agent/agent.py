"""The QQ agent: a bounded tool-calling loop plus an offline fallback."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .config import Config
from .llm import LLM, LLMError
from .persona import agent_style, is_custom_persona_card
from .store import Store
from .tools import ToolContext, ToolRegistry
from .websearch import build_search

SYSTEM_PROMPT = """你是运行在 QQ 私聊里的助手。你有检索、联网和发送工具，但它们只是手段，不是话术。

底线：
- 工具没给的信息就是没有。查不到就直说，不要编造数据、链接、时间或他人言论。
- 需要外部事实（新闻、天气、价格、作品信息）时查一次，够了就停。
- 只有用户明确要求「发出去／回复他」时才调用 social_send；被拦时把原因原样说出来。
- 不冒充真人，不提供医疗、法律、金融方面的专业结论。
- 纯文本输出：不用 ** ## ``` 这些 Markdown 符号，不写括号里的动作神态。
- 默认一两句话；能一句说完就一句。需要列举时才展开，最多 300 字。
"""

SYSTEM_PROMPT_PERSONA = """你正在以一个人格身份和人聊天。你有检索与联网工具，但那些是你的手段，不是你的话术。

底线（不可被角色设定覆盖）：
- 你就是那个角色，不要说「作为 AI」「索引里没有」「检索结果」这类话，也不要报工具名。
- 工具没给的信息就是没有。查不到就按角色会有的说法讲，不要编造数据、链接、时间、他人言论。
- 需要外部事实（新闻、天气、价格、作品信息）时查一次，够了就停。
- 纯文本输出：不用 ** ## ``` 这些 Markdown 符号，不写括号里的动作神态。
- 长度跟着角色设定走；一般很短，别为了显得完整而铺陈。
- 不生成色情内容；涉及未成年人时绝对禁止性化描写。
"""

_STOPWORDS = re.compile(
    r"(帮我|帮忙|请|麻烦|你|能不能|可以|一下|查查|查一下|搜一下|搜搜|找找|找一下|看看|看下|"
    r"整理|汇总|总结|统计|分析|最近|近期|这几天|群里|群聊|大家|都在|关于|有关|讨论|聊|说了什么|"
    r"什么|哪些|哪个|怎么|多少|为啥|是否|在|正在|"
    r"是什么|有什么|有没有|怎么样|如何|的|了|吗|呢|啊|吧|和|跟|与)"
)


@dataclass
class AgentReply:
    text: str
    citations: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "llm"
    meta: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "citations": self.citations,
            "mode": self.mode,
            "tool_calls": self.tool_calls,
            "meta": self.meta,
        }


def _extract_query(text: str) -> str:
    cleaned = _STOPWORDS.sub(" ", text)
    cleaned = re.sub(r"[？?！!。，,；;：:]+", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip() or text.strip()


class QQAgent:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        adapters: dict[str, Any] | None = None,
        *,
        llm: LLM | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.adapters = adapters or {}
        self.llm = llm or LLM(cfg.agent)
        self.websearch = build_search(cfg.websearch)
        self.ctx = ToolContext(
            cfg=cfg,
            store=store,
            adapters=self.adapters,
            llm=self.llm,
            websearch=self.websearch,
        )
        self.tools = ToolRegistry(self.ctx)

    def _style_path(self) -> Path:
        return Path(self.cfg.qq.data_dir).expanduser().resolve() / "style_memory.json"

    async def answer(
        self,
        text: str,
        *,
        platform: str | None = "qq",
        channel_id: str | None = None,
        extra_context: str = "",
        fast: bool = False,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentReply:
        text = (text or "").strip()
        if not text:
            return AgentReply("请说一下你想说什么。", mode="empty")
        if self.llm.available:
            try:
                return await self._llm_answer(
                    text,
                    platform=platform,
                    channel_id=channel_id,
                    extra_context=extra_context,
                    fast=fast,
                    history=history,
                )
            except LLMError as exc:
                if not self.cfg.agent.allow_heuristic_fallback:
                    raise
                reply = await self._heuristic_answer(text, platform=platform, channel_id=channel_id)
                reply.meta["llm_error"] = str(exc)
                return reply
        return await self._heuristic_answer(text, platform=platform, channel_id=channel_id)

    async def _llm_answer(
        self,
        text: str,
        *,
        platform: str | None,
        channel_id: str | None,
        extra_context: str,
        fast: bool = False,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentReply:
        now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        self.ctx._extra["platform"] = platform or ""
        self.ctx._extra["channel_id"] = channel_id or ""

        context_bits = [f"当前时间：{now}"]
        if channel_id:
            context_bits.append(f"会话：{channel_id}")
        if extra_context:
            context_bits.append(extra_context)

        card = self.cfg.persona_text
        base_prompt = SYSTEM_PROMPT_PERSONA if is_custom_persona_card(card) else SYSTEM_PROMPT
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": agent_style(card, style_path=self._style_path())
                + base_prompt
                + "\n"
                + "\n".join(context_bits),
            },
        ]
        for item in history or []:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})

        calls: list[dict[str, Any]] = []
        citations: list[str] = []
        last_content = ""
        rounds = 2 if fast else max(1, self.cfg.agent.max_tool_rounds)

        for _round in range(rounds):
            message = await self.llm.chat(messages, tools=self.tools.schemas())
            content = (message.get("content") or "").strip()
            if content:
                last_content = content
            tool_calls = message.get("tool_calls") or []
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            if not tool_calls:
                break
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                result = await self.tools.call(name, fn.get("arguments") or "{}")
                calls.append({"name": name, "args": fn.get("arguments"), "ok": bool(result.get("ok"))})
                for item in result.get("items") or []:
                    doc_id = item.get("doc_id")
                    if doc_id and doc_id not in citations:
                        citations.append(doc_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "content": json.dumps(result, ensure_ascii=False)[:8000],
                    }
                )
        else:
            if not last_content:
                last_content = await self._final_from_tools(messages, calls)

        return AgentReply(
            text=last_content.strip() or "没有生成有效回答。",
            citations=citations,
            tool_calls=calls,
            mode="llm",
            meta={"rounds": len(calls)},
        )

    async def _final_from_tools(
        self, messages: list[dict[str, Any]], calls: list[dict[str, Any]]
    ) -> str:
        if not calls:
            return "这次没查到靠谱的，换个说法再试试。"
        try:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "（系统）工具轮次已用完。请只根据上面已经拿到的工具结果，"
                        "用两三句话直接回答之前的问题；没有可用结果就直说没查到。"
                    ),
                }
            )
            reply = await self.llm.chat(messages, temperature=0.5, max_tokens=400)
            return (reply.get("content") or "").strip() or "这次没查到靠谱的。"
        except Exception:
            return "这次没查到靠谱的。"

    async def _heuristic_answer(
        self, text: str, *, platform: str | None, channel_id: str | None
    ) -> AgentReply:
        query = _extract_query(text)
        args: dict[str, Any] = {"query": query, "limit": 8, "refresh": True}
        if channel_id:
            args["channel_id"] = channel_id
        result = await self.tools.call("social_search", args)
        items = result.get("items") or []
        if not items and channel_id:
            await self.tools.call("social_recent", {"channel_id": channel_id, "limit": 80})
            result = await self.tools.call("social_search", args)
            items = result.get("items") or []
        if not items:
            return AgentReply(
                text=f"索引里没找到和「{query}」相关的内容。可以换个更短的关键词再试。",
                mode="heuristic",
                meta={"query": query},
            )
        lines = [f"关于「{query}」，筛出 {len(items)} 条（{result.get('summary', '')}）："]
        for idx, item in enumerate(items, 1):
            who = f" {item['author']}" if item.get("author") else ""
            title = item.get("title") or ""
            body = item.get("text") or ""
            head = f"{title} — {body}" if title else body
            lines.append(f"{idx}. [{item.get('when') or '--'}]{who}：{head}")
        if result.get("notes"):
            lines.append("备注：" + "；".join(result["notes"]))
        return AgentReply(
            text="\n".join(lines),
            citations=[item["doc_id"] for item in items],
            tool_calls=[{"name": "social_search", "ok": True}],
            mode="heuristic",
            meta={"query": query, "summary": result.get("summary")},
        )

    async def close(self) -> None:
        await self.llm.close()
        if self.websearch is not None:
            await self.websearch.close()
        for adapter in self.adapters.values():
            close = getattr(adapter, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass


__all__ = ["QQAgent", "AgentReply", "SYSTEM_PROMPT", "SYSTEM_PROMPT_PERSONA"]
