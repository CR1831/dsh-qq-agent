"""Tools the agent can call.

Each tool is a thin, well-typed wrapper over an adapter + the filter pipeline.
Keeping them small matters: the model sees the schemas, so a vague tool is a
vague agent.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .adapters.base import AdapterError, BaseAdapter
from .config import Config
from .llm import LLM, LLMError
from .models import Doc
from .retrieval.filter import FilterCriteria, filter_docs
from .retrieval.text import excerpt, strip_html
from .store import Store

PLATFORM_NAMES = {"qq": "QQ"}


@dataclass
class ToolContext:
    cfg: Config
    store: Store
    adapters: dict[str, BaseAdapter]
    llm: LLM
    websearch: Any | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    async def judge(self, query: str, docs: Sequence[Doc]) -> dict[str, tuple[float, str]]:
        return await self.llm.judge(query, docs)


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _since(value: Any) -> float | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m-%d %H:%M"):
        try:
            tm = time.strptime(text, fmt)
        except ValueError:
            continue
        year = tm.tm_year if "%Y" in fmt else time.localtime().tm_year
        return time.mktime((year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0, 0, -1))
    if text.endswith("h"):
        return time.time() - float(text[:-1]) * 3600
    if text.endswith("d"):
        return time.time() - float(text[:-1]) * 86400
    raise ValueError(f"无法解析时间：{value}")


def _item(doc: Doc, score: float = 0.0, snippet: str = "") -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "platform": doc.platform,
        "kind": doc.kind,
        "when": doc.when,
        "author": doc.author_name,
        "channel": doc.channel_name or doc.channel_id,
        "title": strip_html(doc.title)[:120],
        "text": excerpt(snippet or doc.text, 260),
        "url": doc.url,
        "score": round(score, 3),
    }


def _criteria(args: dict[str, Any], *, default_k: int = 15) -> FilterCriteria:
    return FilterCriteria(
        query=str(args.get("query") or ""),
        include_any=[str(x) for x in args.get("include") or []],
        exclude_any=[str(x) for x in args.get("exclude") or []],
        platforms=["qq"],
        kinds=[str(x) for x in args.get("kinds") or []],
        channel_id=args.get("channel_id") or None,
        since=_since(args.get("since")),
        min_chars=int(args.get("min_chars") or 4),
        min_relevance=float(args.get("min_relevance") or 0.0),
        top_k=int(args.get("limit") or default_k),
        per_author_cap=int(args.get("per_author_cap") or 3),
        use_llm_judge=bool(args.get("use_llm_judge")),
    )


# ------------------------------------------------------------------ search
async def tool_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    platforms = ["qq"]
    criteria = _criteria(args)
    limit = criteria.top_k

    candidates: list[Doc] = []
    notes: list[str] = []
    for platform in platforms:
        rows = await asyncio.to_thread(
            ctx.store.search,
            query,
            platform=platform,
            since=criteria.since,
            limit=max(limit * 6, 40),
        )
        candidates.extend(doc for doc, _rank, _snip in rows)

    if args.get("refresh"):
        for platform in platforms:
            adapter = ctx.adapters.get(platform)
            if adapter is None:
                continue
            try:
                fresh = await adapter.search(query, limit=limit * 2) if query else []
            except (AdapterError, NotImplementedError) as exc:
                notes.append(f"{PLATFORM_NAMES.get(platform, platform)} 实时检索失败：{exc}")
                continue
            if fresh:
                ctx.store.upsert(fresh)
                candidates.extend(fresh)
                notes.append(f"{PLATFORM_NAMES.get(platform, platform)} 实时命中 {len(fresh)} 条")

    if not candidates and query:
        return {
            "ok": True,
            "query": query,
            "summary": "本地索引中没有匹配内容，且未开启或无法实时检索。",
            "items": [],
            "notes": notes,
            "hint": "可先调用 social_recent 把目标会话的消息抓进索引，再检索。",
        }

    result = await filter_docs(
        candidates,
        criteria,
        judge=ctx.judge if criteria.use_llm_judge and ctx.llm.available else None,
    )
    return {
        "ok": True,
        "query": query,
        "summary": result.summary(),
        "items": [_item(d, result.scores.get(d.doc_id, 0.0)) for d in result.kept[:limit]],
        "dropped": {k: v for k, v in result.stats.items() if k not in ("input", "kept")},
        "notes": notes,
    }


# ------------------------------------------------------------------ recent
async def tool_recent(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    platform = "qq"
    channel_id = str(args.get("channel_id") or "")
    limit = int(args.get("limit") or 50)
    since = _since(args.get("since"))

    adapter = ctx.adapters.get(platform)
    docs: list[Doc] = []
    notes: list[str] = []
    if adapter is not None and channel_id:
        try:
            docs = await adapter.fetch_history(channel_id, limit=limit, since=since)
        except (AdapterError, NotImplementedError) as exc:
            notes.append(f"实时拉取失败：{exc}")
    if not docs:
        docs = await asyncio.to_thread(
            ctx.store.recent, platform=platform, channel_id=channel_id or None, since=since, limit=limit
        )
    if docs:
        ctx.store.upsert(docs)
    docs.sort(key=lambda d: d.ts)
    return {
        "ok": True,
        "count": len(docs),
        "summary": f"拿到 {len(docs)} 条消息",
        "items": [_item(d) for d in docs[-limit:]],
        "notes": notes,
    }


# ------------------------------------------------------------------- filter
async def tool_filter(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    doc_ids = [str(x) for x in args.get("doc_ids") or []]
    docs: list[Doc] = []
    for doc_id in doc_ids:
        found = await asyncio.to_thread(ctx.store.get, doc_id)
        if found:
            docs.append(found)
    if not docs:
        return {"ok": False, "error": "没有找到这些 doc_id"}
    criteria = _criteria(args, default_k=len(docs))
    result = await filter_docs(
        docs,
        criteria,
        judge=ctx.judge if criteria.use_llm_judge and ctx.llm.available else None,
    )
    return {
        "ok": True,
        "summary": result.summary(),
        "items": [_item(d, result.scores.get(d.doc_id, 0.0)) for d in result.kept],
        "dropped_detail": [{"doc_id": d.doc.doc_id, "stage": d.stage, "reason": d.reason} for d in result.dropped[:40]],
    }


# --------------------------------------------------------------- web search
async def tool_web_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.websearch is None:
        return {"ok": False, "error": "联网查询未启用（config 里 [websearch] enabled = true）"}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "需要 query"}
    limit = int(args.get("limit") or 5)
    results = await ctx.websearch.search(query, limit)
    items = [r.as_payload() for r in results]
    return {
        "ok": True,
        "query": query,
        "count": len(items),
        "summary": f"找到 {len(items)} 条结果" if items else "没有找到结果",
        "items": items,
        "hint": (
            "回答时引用链接，不要编造结果里没有的内容"
            if items
            else "可以直接说查不到，并给出替代建议；不要凭印象编赛程/日期/价格"
        ),
    }


# ------------------------------------------------------------------- stats
async def tool_stats(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    stats = await asyncio.to_thread(ctx.store.stats)
    channels = await asyncio.to_thread(ctx.store.channels)
    return {
        "ok": True,
        "platforms_enabled": [p for p, a in ctx.adapters.items() if getattr(a, "enabled", True)],
        "llm": "on" if ctx.llm.available else "heuristic-fallback",
        "index": stats,
        "channels": channels[:20],
    }


# -------------------------------------------------------------------- send
async def tool_qq_friends(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """查这个 QQ 号上的好友。

    分级看人下菜：
    - 主账号：给完整名单（QQ 号、昵称、备注）；
    - 其他人：只说有几个，不给名单——好友列表对朋友也是隐私。
    """
    adapter = ctx.adapters.get("qq")
    if adapter is None:
        return {"ok": False, "error": "QQ 适配器未启用（这台机器上没连 NapCat）"}

    owner = str(getattr(ctx.cfg.qq, "owner_uid", "") or "")
    channel = str(ctx._extra.get("channel_id") or "")
    self_id = str(getattr(ctx.cfg.qq, "self_id", "") or "")
    is_owner = bool(owner) and channel == f"private:{owner}"

    try:
        raw = await adapter.api("get_friend_list")
    except Exception as exc:
        return {"ok": False, "error": f"拉好友列表失败：{type(exc).__name__}: {exc}"}

    friends = [f for f in (raw or []) if str(f.get("user_id") or "") != self_id]
    if not friends:
        return {"ok": False, "error": "好友列表是空的——会话可能坏了，需要重启 NapCat 并重新扫码"}

    if not is_owner:
        return {
            "ok": True,
            "summary": f"这个号上有 {len(friends)} 个好友。",
            "count": len(friends),
            "hint": "名单是别人的事，别念。一句带过就行：有几个好友，名字不方便说。",
        }

    items = [
        {
            "user_id": str(f.get("user_id") or ""),
            "nickname": str(f.get("nickname") or ""),
            "remark": str(f.get("remark") or ""),
        }
        for f in friends
    ]
    lines = []
    for it in items:
        name = it["remark"] or it["nickname"] or it["user_id"]
        extra = f"（备注 {it['remark']}）" if it["remark"] and it["nickname"] else ""
        lines.append(f"- {it['user_id']} {it['nickname'] or '—'}{extra}")
    return {
        "ok": True,
        "summary": f"共 {len(items)} 个好友：\n" + "\n".join(lines),
        "count": len(items),
        "friends": items,
        "owner_uid": owner,
    }


def _load_local_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _birthday_text(f: dict[str, Any]) -> str:
    y = int(f.get("birthday_year") or 0)
    m = int(f.get("birthday_month") or 0)
    d = int(f.get("birthday_day") or 0)
    if not (m and d):
        return ""
    return f"{y}-{m:02d}-{d:02d}" if y else f"{m:02d}-{d:02d}"


async def tool_qq_friend_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """看好友最近怎么样：资料 + 画像 + 最近一次对话。

    「状态」不包含实时在线——NapCat 没有这个接口（`get_friend_online_status` 不支持），
    能给的只有：年龄/等级/生日、画像印象、消息统计、最后一次说话的时间与摘要。
    """
    owner = str(getattr(ctx.cfg.qq, "owner_uid", "") or "")
    channel = str(ctx._extra.get("channel_id") or "")
    if not owner or channel != f"private:{owner}":
        return {
            "ok": False,
            "error": "别人的事，我不替他们讲。",
            "hint": "一句挡回去就好，别解释规则，也别道歉。",
        }

    adapter = ctx.adapters.get("qq")
    if adapter is None:
        return {"ok": False, "error": "QQ 没连上，查不了。"}

    self_id = str(getattr(ctx.cfg.qq, "self_id", "") or "")
    try:
        raw = await adapter.api("get_friend_list")
    except Exception as exc:
        return {"ok": False, "error": f"拉好友列表失败：{type(exc).__name__}"}

    base = Path(ctx.cfg.server.db_path).expanduser().resolve().parent
    profiles = _load_local_json(base / "profiles.json")
    history = _load_local_json(base / "history.json")
    whitelist = {str(x) for x in (getattr(ctx.cfg.qq, "friend_whitelist", None) or [])}

    who = str(args.get("who") or "").strip()
    rows: list[dict[str, Any]] = []
    for f in raw or []:
        uid = str(f.get("user_id") or "")
        if not uid or uid == self_id:
            continue
        name = str(f.get("remark") or f.get("nickname") or uid)
        nick = str(f.get("nickname") or "")
        if who and who not in {uid, name, nick} and who not in name:
            continue

        prof = profiles.get(f"private:{uid}") or {}
        stats = prof.get("stats") or {}
        turns = history.get(f"private:{uid}") or []
        last = turns[-1] if turns else None
        rows.append(
            {
                "user_id": uid,
                "name": name,
                "nickname": nick,
                "age": f.get("age"),
                "level": f.get("level"),
                "birthday": _birthday_text(f),
                "impression": str(prof.get("impression") or ""),
                "msgs": int(stats.get("msgs") or 0),
                "avg_len": int(stats.get("avg_len") or 0),
                "last_talk": (
                    time.strftime("%m-%d %H:%M", time.localtime(float(last.get("ts") or 0)))
                    if last
                    else ""
                ),
                "last_role": str(last.get("role") or "") if last else "",
                "last_preview": str(last.get("content") or "")[:60] if last else "",
                "in_whitelist": uid in whitelist,
            }
        )

    if not rows:
        if who:
            return {"ok": False, "error": f"好友里没有「{who}」。"}
        return {"ok": False, "error": "好友列表是空的。"}

    rows.sort(key=lambda r: r["last_talk"] or "", reverse=True)

    lines = []
    for r in rows[:12]:
        bits = [f"{r['name']}（{r['user_id']}）"]
        meta = []
        if r["age"]:
            meta.append(f"{r['age']} 岁")
        if r["level"]:
            meta.append(f"Lv{r['level']}")
        if r["birthday"]:
            meta.append(f"生日 {r['birthday']}")
        if meta:
            bits.append("·".join(meta))
        if r["impression"]:
            bits.append(r["impression"])
        if r["msgs"]:
            bits.append(f"聊过 {r['msgs']} 句")
        if r["last_talk"]:
            said = "对方最后说" if r["last_role"] == "user" else "你最后回的是"
            bits.append(f"最近 {r['last_talk']}（{said}）")
        else:
            bits.append("还没聊过")
        if not r["in_whitelist"]:
            bits.append("不在白名单")
        lines.append(" ｜ ".join(bits))

    return {
        "ok": True,
        "summary": "\n".join(lines),
        "count": len(rows),
        "friends": rows[:12],
        "note": "没有实时在线状态可用，给的是最新资料与对话记录。",
    }


async def tool_send_sticker(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """按备注发一张收藏表情。"""
    from . import qq_guard, qq_sticker

    adapter = ctx.adapters.get("qq")
    if adapter is None:
        return {"ok": False, "error": "QQ 适配器未启用"}

    channel = str(ctx._extra.get("channel_id") or "")
    if not channel:
        return {"ok": False, "error": "不知道现在在跟谁说话，别乱发"}

    items = qq_sticker.load(qq_sticker.store_path(ctx.cfg.server.db_path))
    if not items:
        return {
            "ok": False,
            "error": "还没有收藏表情库",
            "hint": "先运行 python -m qq_agent qq --stickers 同步一次。",
        }

    if ctx.cfg.qq.dry_run:
        return {
            "ok": False,
            "blocked": True,
            "reason": "dry_run 已开启：只生成草稿，不实际发送",
        }

    decision = qq_guard.allow_sticker(ctx.cfg, ctx.store)
    if not decision.allow:
        return {
            "ok": False,
            "blocked": True,
            "reason": decision.reason,
            "hint": "别硬发。正常回一句话就可以了。",
        }

    mood = str(args.get("mood") or args.get("keyword") or "").strip()
    if not mood:
        return {"ok": False, "error": "得说想要什么情绪的表情"}

    item = qq_sticker.match(items, mood)
    if item is None:
        return {
            "ok": False,
            "error": f"备注里没有和「{mood}」对得上的表情",
            "available": [i.get("note") or "(无备注)" for i in items[:12]],
            "hint": "换一个更贴近的形容词；实在没有就别发。",
        }

    kind, _, target = channel.partition(":")
    message = [{"type": "image", "data": {"file": item["url"]}}]
    try:
        if kind == "group":
            await adapter.api("send_group_msg", group_id=int(target), message=message)
        else:
            await adapter.api("send_private_msg", user_id=int(target), message=message)
    except Exception as exc:
        return {"ok": False, "error": f"发送失败：{type(exc).__name__}: {exc}"}

    qq_guard.record_sticker(ctx.store)
    return {"ok": True, "summary": f"发了一张表情：{item.get('note') or ''}"}


async def tool_send(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    channel_id = str(args.get("channel_id") or "")
    text = str(args.get("text") or "").strip()
    if not channel_id or not text:
        return {"ok": False, "error": "需要 channel_id 和 text", "draft": text}

    if ctx.cfg.qq.dry_run:
        return {
            "ok": False,
            "blocked": True,
            "reason": "dry_run 已开启：只生成草稿，不实际发送",
            "draft": text,
        }

    from . import qq_guard

    guard = qq_guard.allow_send(ctx.cfg, ctx.store, channel_id, proactive=True)
    if not guard.allow:
        return {
            "ok": False,
            "blocked": True,
            "reason": guard.reason,
            "draft": text,
            "hint": (
                f"被拦了，原因就是这句：「{guard.reason}」——"
                "原样告诉用户，不要改写、不要自己猜别的原因。"
            ),
        }

    adapter = ctx.adapters.get("qq")
    if adapter is None:
        return {"ok": False, "error": "QQ 适配器未启用", "draft": text}
    if guard.delay > 0:
        await asyncio.sleep(guard.delay)  # 像人在打字，不是秒回
    try:
        receipt = await adapter.send(channel_id, text)
    except (AdapterError, NotImplementedError) as exc:
        return {"ok": False, "error": str(exc), "draft": text}
    qq_guard.record_send(ctx.store)
    return {"ok": True, "summary": "已发送", "receipt": receipt}


def _is_owner_channel(ctx: ToolContext) -> bool:
    owner = str(getattr(ctx.cfg.qq, "owner_uid", "") or "")
    return bool(owner) and str(ctx._extra.get("channel_id") or "") == f"private:{owner}"


async def tool_self_check(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Read the service's own status file; owner-only."""
    if not _is_owner_channel(ctx):
        return {
            "ok": False,
            "error": "我自己的事，不用跟外人汇报。",
            "hint": "一句带过，别解释。",
        }
    status_path = Path(ctx.cfg.home) / "status.json"
    data: dict[str, Any] = {}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    whitelist = list(getattr(ctx.cfg.qq, "friend_whitelist", None) or [])
    notes: list[str] = []
    if not data:
        notes.append("status.json 不存在：服务可能没启动")
    if not whitelist:
        notes.append("好友白名单为空：不会回复任何私聊")
    if data and not data.get("ws"):
        notes.append("WS 未连接：NapCat 可能掉登录")
    return {
        "ok": True,
        "summary": (
            f"服务：{'运行中' if data else '未启动'}；"
            f"WS：{'已连接' if data.get('ws') else '未连接'}；"
            f"白名单：{len(whitelist)} 人"
            + ("；" + "；".join(notes) if notes else "")
        ),
        "status": data,
        "whitelist": len(whitelist),
        "notes": notes,
    }


async def tool_read_log(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Read the tail of the service log; owner-only."""
    if not _is_owner_channel(ctx):
        return {"ok": False, "error": "日志不对外看。", "hint": "一句挡回去。"}
    try:
        count = int(args.get("lines") or 30)
    except (TypeError, ValueError):
        count = 30
    count = max(5, min(count, 200))
    keyword = str(args.get("grep") or "").strip()
    path = Path(ctx.cfg.home) / "logs" / "service.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError as exc:
        return {"ok": False, "error": f"读日志失败：{type(exc).__name__}: {exc}"}
    if keyword:
        lines = [ln for ln in lines if keyword in ln]
        if not lines:
            return {"ok": True, "summary": f"最近 {count} 行里没有「{keyword}」。", "lines": []}
    return {
        "ok": True,
        "summary": "\n".join(lines),
        "lines": lines,
        "count": len(lines),
        "keyword": keyword or None,
    }


def build_tools(cfg: Config | None = None) -> list[ToolSpec]:
    specs = [
        ToolSpec(
            name="social_search",
            description=(
                "在 QQ 本地索引里检索，并自动去重、关键词过滤、相关度排序。"
                "refresh=true 时会先向 NapCat 实时拉取再入库（会慢一些）。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "description": "检索词，支持空格分词；留空表示按时间取最近内容"},
                    "channel_id": {"type": "string", "description": "限定会话，如 group:123456 或 private:123456"},
                    "since": {"type": "string", "description": "起始时间，如 2026-08-01 或 72h / 7d"},
                    "limit": {"type": "integer", "description": "返回条数，默认 15"},
                    "include": {"type": "array", "items": {"type": "string"}, "description": "必须包含的任一关键词"},
                    "exclude": {"type": "array", "items": {"type": "string"}, "description": "命中即丢弃的关键词（广告、抽奖等）"},
                    "min_relevance": {"type": "number", "description": "相关度阈值 0-1，默认 0"},
                    "refresh": {"type": "boolean", "description": "是否先向平台实时拉取"},
                    "use_llm_judge": {"type": "boolean", "description": "是否让模型逐条判定相关性（更准但更慢）"},
                },
                "required": ["query"],
            },
            handler=tool_search,
        ),
        ToolSpec(
            name="social_recent",
            description="把某个 QQ 会话最近的消息抓进本地索引。用于先把原料攒够再检索。",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "channel_id": {"type": "string", "description": "group:123456 或 private:123456"},
                    "limit": {"type": "integer", "description": "默认 50"},
                    "since": {"type": "string", "description": "只取该时间之后"},
                },
                "required": ["channel_id"],
            },
            handler=tool_recent,
        ),
        ToolSpec(
            name="filter_items",
            description="对已入库的若干条内容按给定期望再筛一遍，返回保留项和每条被丢弃的具体原因。",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "doc_ids": {"type": "array", "items": {"type": "string"}},
                    "query": {"type": "string"},
                    "include": {"type": "array", "items": {"type": "string"}},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "min_relevance": {"type": "number"},
                },
                "required": ["doc_ids"],
            },
            handler=tool_filter,
        ),
        ToolSpec(
            name="web_search",
            description=(
                "联网搜索实时信息：赛程、天气、新闻、价格等本地索引里没有的东西。"
                "返回标题、链接和摘要；查到就引用链接，查不到就直说，不要凭印象编。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "description": "搜索词，尽量具体"},
                    "limit": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
            handler=tool_web_search,
        ),
        ToolSpec(
            name="social_stats",
            description="查看索引规模、已启用的适配器和可检索的会话列表。",
            parameters={"type": "object", "additionalProperties": False, "properties": {}},
            handler=tool_stats,
        ),
        ToolSpec(
            name="social_send",
            description=(
                "**主动**给 QQ 好友或群发一条消息（不是回复当前会话）。"
                "channel_id 写 private:QQ号 或 group:群号。"
                "只有号主明确说「发吧」才调用；受白名单、限流、免打扰和主动发送窗口约束。"
                "被拦时返回 blocked=true 和 reason——把 reason 原样说出来。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "channel_id": {"type": "string", "description": "private:QQ号 或 group:群号"},
                    "text": {"type": "string", "description": "要发送的内容，纯文本"},
                },
                "required": ["channel_id", "text"],
            },
            handler=tool_send,
        ),
        ToolSpec(
            name="qq_friends",
            description=(
                "查这个 QQ 号上的好友（谁加了这个号）。返回 QQ 号、昵称、备注。"
                "只在号主私信里可用；号主问起时给完整名单，其他人问只报数量。"
            ),
            parameters={"type": "object", "additionalProperties": False, "properties": {}},
            handler=tool_qq_friends,
        ),
        ToolSpec(
            name="qq_friend_status",
            description=(
                "看某个好友最近怎么样：年龄/等级/生日、你给他画的印象、聊过多少句、"
                "最后一次对话是什么时候。who 不填就给全部概览。只在号主私信里可用。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "who": {"type": "string", "description": "QQ 号、备注或昵称；留空表示全部"}
                },
            },
            handler=tool_qq_friend_status,
        ),
        ToolSpec(
            name="self_check",
            description=(
                "看她自己现在什么状态：服务心跳、WS 连接、白名单人数、最近错误。"
                "号主质疑「不回消息 / 变慢 / 报错」时先调这个。只读，只在号主私信里可用。"
            ),
            parameters={"type": "object", "additionalProperties": False, "properties": {}},
            handler=tool_self_check,
        ),
        ToolSpec(
            name="read_log",
            description="读服务日志的最后若干行（可只保留含某个关键词的行）。只读，只在号主私信里可用。",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "lines": {"type": "integer", "description": "读最后多少行，5–200，默认 30"},
                    "grep": {"type": "string", "description": "只保留包含这个词的行"},
                },
            },
            handler=tool_read_log,
        ),
        ToolSpec(
            name="send_sticker",
            description=(
                "发一张收藏的表情包，按备注里的情绪找（如「尴尬」「无语」「得意」）。"
                "要克制：两张之间至少隔 20 分钟，被拦了就别硬发。"
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mood": {"type": "string", "description": "想表达的情绪或画面"}
                },
                "required": ["mood"],
            },
            handler=tool_send_sticker,
        ),
    ]
    if cfg is not None and getattr(cfg.desktop, "enabled", False):
        try:
            from .desktop import build_desktop_tools

            specs.extend(build_desktop_tools())
        except Exception:
            # Optional extra missing or broken: keep the core toolset usable.
            pass
    return specs


class ToolRegistry:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.specs = {spec.name: spec for spec in build_tools(ctx.cfg)}

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.specs.values()]

    def describe(self) -> str:
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self.specs.values())

    async def call(self, name: str, raw_args: str | dict[str, Any]) -> dict[str, Any]:
        spec = self.specs.get(name)
        if spec is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"参数不是合法 JSON：{exc}"}
        else:
            args = raw_args or {}
        try:
            result = await spec.handler(self.ctx, args)
        except LLMError as exc:
            return {"ok": False, "error": f"模型调用失败：{exc}"}
        except Exception as exc:  # tool failures must not kill the turn
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return result


__all__ = ["ToolContext", "ToolRegistry", "ToolSpec", "build_tools"]
