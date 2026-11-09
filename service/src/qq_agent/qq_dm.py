"""QQ 回复器：收 OneBot 事件、过护栏、生成回复。

链路：

    NapCat（注入官方 NTQQ）— OneBot 11
      ├─ HTTP 127.0.0.1:3000：发消息（send_private_msg）
      └─ WS   127.0.0.1:3001：收事件

这里只做三件事：**收事件、过护栏、生成回复**。人格来自用户自己放的卡；
没有卡时服务不会启动，也不会退回任何人设。

安全边界：
- 只处理私聊白名单和群白名单里的会话；
- 发送前必须过 ``qq_guard``（白名单 / 限流 / 免打扰 / 随机延迟）；
- ``[qq].dry_run`` 打开时只生成草稿，不实际发送。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from .adapters.qq_onebot import render_segments
from .config import Config
from .qq_media import card_text, extract_media, media_note
from .qq_safety import alert_text, screen_risk
from . import qq_profile
from . import qq_watchdog
from . import wellbeing

SEGMENT_SEP = "||"

# 主账号说这些，表示"这次可以主动发"——开一个 5 分钟窗口
_GRANT_RE = re.compile(
    r"^\s*((你)?去发吧|(你)?去发|发吧|可以发|发过去|发给他|给他发|发一条|可以发吧|那就发吧)"
)


def looks_like_grant(text: str) -> bool:
    """是不是一句「你去发吧」式的授权。

    只认**短句开头**：16 字以内、以授权词起头。
    这样「发吧，就一句」算数，而「他刚才说发吧」这种转述不算。
    """
    body = str(text or "").strip()
    return bool(body) and len(body) <= 16 and bool(_GRANT_RE.match(body))

# render_segments 给媒体留的占位符：它们不是内容，别让模型当成对方说的话
_MEDIA_PLACEHOLDERS = {
    "[卡片消息]",
    "[图片]",
    "[图片]",
    "[视频]",
    "[语音]",
    "[表情]",
    "[回复]",
    "[合并转发]",
    "[文件]",
    "[分享]",
}


def _log(message: str) -> None:
    """带时间戳的日志——排查「某条消息为什么没回」时全靠它。"""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

OWNER_NOTE = (
    "对方是号主，正跟你私聊。不要自我介绍，也不要解释设定。"
    "想知道这号上加过谁，用 qq_friends 查；查完直接说，别念一串 QQ 号。"
)
FRIEND_NOTE = (
    "对方是号上的好友，不熟。保持分寸：不主动交代来历或设定，被追问就简单带过；"
    "涉及发送、发布、花钱这类动作，先问号主。"
)


def join_segments(text: str) -> str:
    """把 `||` 分隔的句子合并成一整段。

    模型还是按角色卡那样用 `||` 断句——那是她的呼吸节奏，不是发送单位。
    这里把它拼回自然段落：句末有标点就直接接，没有就补一个逗号。
    """
    raw = (text or "").strip()
    if SEGMENT_SEP not in raw:
        return raw
    parts = [p.strip() for p in raw.split(SEGMENT_SEP) if p.strip()]
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        if out.endswith(("。", "！", "？", "…", "，", "、", "；", "：")):
            out += part
        else:
            out += "，" + part
    return out


def split_segments(text: str, *, limit: int = 5) -> list[str]:
    """把一条回复拆成「一句一条」。

    优先按角色卡里约定的 ``||`` 拆；模型没照做时退化成按换行拆，
    再退化成按句末标点拆。太碎的并回去，超过上限的压进最后一句。
    一句不想说时，模型给的「……」会原样保留成一条。
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # 两级拆分：先按 || 切段，每段再按换行切——两级都要生效，
    # 否则 "a||b\nc" 会把 b 和 c 黏在一起。
    parts: list[str] = []
    for chunk in raw.split(SEGMENT_SEP):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        parts.extend(lines if len(lines) > 1 else [chunk])
    if not parts:
        parts = [raw]

    if len(parts) == 1 and len(parts[0]) > 20:
        pieces = re.findall(r"[^。！？!?…]+[。！？!?…]*", parts[0])
        parts = [p.strip() for p in pieces if p.strip()] or parts

    merged: list[str] = []
    for part in parts:
        # 只有纯标点（"。" "！" 这类）才并回上一句，单字句子要保留
        if merged and not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", part):
            merged[-1] += part
        else:
            merged.append(part)

    if limit > 0 and len(merged) > limit:
        merged = merged[: limit - 1] + ["".join(merged[limit - 1 :])]
    return merged


class QQReplier:
    """一条命：收 OneBot 私聊事件 → 生成回复 → 过护栏发出去。"""

    def __init__(self, cfg: Config, store: Any, agent: Any, adapters: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.agent = agent
        self.adapters = adapters or {}
        self.adapter = self.adapters.get("qq")
        self._history_path = Path(cfg.server.db_path).expanduser().resolve().parent / "history.json"
        self._history: dict[str, list[dict[str, Any]]] = self._load_history()
        self._profiles_path = qq_profile.profile_path(cfg.server.db_path)
        self._profiles: dict[str, dict[str, Any]] = qq_profile.load(self._profiles_path)

    # ------------------------------------------------------------ 对话历史
    def _load_history(self) -> dict[str, list[dict[str, Any]]]:
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # 迁移：早期版本只用纯 QQ 号当 key，现在私聊一律带 private: 前缀
        migrated: dict[str, list[dict[str, Any]]] = {}
        for key, rows in data.items():
            if not isinstance(rows, list):
                continue
            new_key = key if ":" in str(key) else f"private:{key}"
            migrated[new_key] = rows
        return migrated

    def _save_history(self) -> None:
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(self._history, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _recent(self, uid: str) -> list[dict[str, Any]]:
        """带进 prompt 的最近几轮；多了会挤掉角色卡的分量。"""
        keep = max(2, int(getattr(self.cfg.qq, "history_turns", 8) or 8))
        return list(self._history.get(str(uid), []))[-keep:]

    def _remember(self, uid: str, role: str, content: str, *, save: bool = True) -> None:
        # 统一成字符串 key：调用方传 int 时不会另建一份历史
        uid = str(uid)
        # 单条太长会挤掉角色卡的分量，存之前先收一收
        body = str(content or "")
        cap = max(200, int(getattr(self.cfg.qq, "history_max_chars", 800) or 800))
        if len(body) > cap:
            body = body[:cap] + "…"
        rows = self._history.setdefault(uid, [])
        rows.append({"role": role, "content": body, "ts": time.time()})
        limit = max(10, int(getattr(self.cfg.qq, "history_keep", 60) or 60))
        self._history[uid] = rows[-limit:]
        # 联系人太多时裁掉最久没说话的，别让内存和文件无限长
        max_peers = max(4, int(getattr(self.cfg.qq, "history_peers", 20) or 20))
        if len(self._history) > max_peers:
            stale = sorted(
                self._history,
                key=lambda k: max((float(i.get("ts") or 0) for i in self._history[k]), default=0.0),
            )
            for key in stale[: len(self._history) - max_peers]:
                self._history.pop(key, None)
        if save:
            self._save_history()

    # ------------------------------------------------------------ 身份
    @property
    def owner_uid(self) -> str:
        return str(getattr(self.cfg.qq, "owner_uid", "") or "")

    def role_note(self, uid: str, channel: str = "") -> str:
        base = OWNER_NOTE if uid == self.owner_uid else FRIEND_NOTE
        if uid == self.owner_uid:
            extra = str(getattr(self.cfg.qq, "owner_note", "") or "").strip()
            if extra:
                base += "\n" + extra
        portrait = qq_profile.describe(
            self._profiles.get(channel) if channel else None,
            include_facts=bool(getattr(self.cfg.qq, "use_profile_facts", False)),
        )
        return base + ("\n" + portrait if portrait else "")

    # ------------------------------------------------------------ 好友画像
    def _birthday_note(self, uid: str, *, now: time.struct_time | None = None) -> str:
        """今天正好是号主生日时给一句提示；资料读取默认关闭。"""
        if uid != self.owner_uid or not getattr(self.cfg.qq, "use_profile_facts", False):
            return ""
        profile = self._profiles.get(f"private:{uid}") or {}
        month = profile.get("birthday_month")
        day = profile.get("birthday_day")
        if not month or not day:
            return ""
        try:
            month_i, day_i = int(month), int(day)
        except (TypeError, ValueError):
            return ""
        today = now or time.localtime()
        if today.tm_mon != month_i or today.tm_mday != day_i:
            return ""
        return (
            "今天是他的生日（他 QQ 资料里填的）。可以提一句，"
            "但别隆重——你不是会张罗这种事的人，一句「生日啊」加一句实在话就够。"
        )

    def _touch_profile(self, channel: str, name: str = "") -> None:
        """聊天后更新统计（零成本）；印象刷新交给 _refresh_profile。"""
        rows = self._history.get(channel, [])
        entry = self._profiles.setdefault(channel, {})
        entry["stats"] = qq_profile.stats_from(rows)
        if name and not entry.get("name"):
            entry["name"] = name
        entry.setdefault("updated", 0)
        qq_profile.save(self._profiles_path, self._profiles)

    async def _refresh_profile(self, channel: str) -> None:
        """够格了才让模型写一句印象——低频，不占正常回复的路。"""
        entry = self._profiles.get(channel)
        stats = (entry or {}).get("stats") or {}
        if not qq_profile.needs_refresh(entry, stats):
            return
        rows = self._history.get(channel, [])
        llm = getattr(self.agent, "llm", None)
        if llm is None or not getattr(llm, "available", False):
            return
        impression = await qq_profile.build_impression(rows, llm)
        if not impression:
            return
        entry = self._profiles.setdefault(channel, {})
        entry["impression"] = impression
        entry["updated"] = time.time()
        qq_profile.save(self._profiles_path, self._profiles)
        _log(f"画像更新 {channel}（{len(impression)} 字）")

    def allowed(self, uid: str) -> bool:
        whitelist = {str(x) for x in (getattr(self.cfg.qq, "friend_whitelist", None) or [])}
        return bool(uid) and uid in whitelist

    def _priority(self, event: dict[str, Any]) -> int:
        """0 = 主账号（插队），1 = 其他人（按到达顺序）。"""
        uid = str(event.get("user_id") or "")
        return 0 if uid and uid == self.owner_uid else 1

    def _at_me(self, event: dict[str, Any]) -> bool:
        """群里看这条消息有没有 @ 到自己。"""
        target = str(getattr(self.cfg.qq, "self_id", "") or "")
        if not target:
            return False
        for seg in event.get("message") or []:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            qq = str((seg.get("data") or {}).get("qq") or "")
            if qq and qq == target:
                return True
        return False

    def _group_allowed(self, group_id: str) -> bool:
        whitelist = {str(x) for x in (getattr(self.cfg.qq, "group_whitelist", None) or [])}
        return bool(group_id) and group_id in whitelist

    # ------------------------------------------------------------ 跨会话视角
    def _others_note(self, channel: str) -> str:
        """主账号问起别人时，给一个「知道大概、但不摊开」的概况。

        参数是**会话 key**（`private:xxx`），不是裸 QQ 号——
        早先按 uid 比，导致主账号自己也被列进「其他人」里。
        只给：谁、多久前、几句、开头几个字。
        不给原文、不给评价——细节仍然属于那段对话本身。
        """
        if channel != f"private:{self.owner_uid}":
            return ""
        rows: list[tuple[float, str, int, str, dict[str, Any]]] = []
        for other, items in self._history.items():
            if other == channel or not items:
                continue
            user_msgs = [str(i.get("content") or "") for i in items if i.get("role") == "user"]
            if not user_msgs:
                continue
            # 用画像里的名字，别丢个 QQ 号给她——她不知道那串数字是谁
            profile = self._profiles.get(other) or {}
            label = str(profile.get("name") or "").strip()
            if not label:
                bare = str(other).replace("private:", "")
                label = f"群 {bare}" if str(other).startswith("group:") else bare
            stamps = [float(i.get("ts") or 0) for i in items]
            rows.append(
                (max(stamps) if stamps else 0.0, label, len(user_msgs), user_msgs[-1], profile)
            )
        if not rows:
            return ""
        rows.sort(key=lambda r: r[0], reverse=True)
        lines = ["【其他会话的概况 · 只知道大概，别主动汇报】"]
        for when, label, count, last, profile in rows[:5]:
            age = (time.time() - when) / 3600 if when else 999.0
            ago = "刚刚" if age < 1 else f"{age:.0f} 小时前"
            line = f"- {label}：{ago}找过，{count} 句"
            # 画像里的「怎么说话」可以给号主参考——那不是对话内容，是相处感受
            impression = str(profile.get("impression") or "").strip()
            if impression:
                line += f"。印象：{impression}"
            lines.append(line)
        lines.append(
            "只是方向，不是原文：有没有找过、大概聊什么可以说；原话不复述，人不评价，不用主动提。"
            "名字对不上是谁，先用 qq_friends 对一下昵称备注。"
        )
        return "\n".join(lines)

    async def _alert_owner(self, from_uid: str, label: str, text: str) -> None:
        """别人的消息触发风险词时，私信告诉主账号——但不擅自回复对方。"""
        if not self.owner_uid or self.adapter is None or from_uid == self.owner_uid:
            return
        try:
            await self.adapter.send(
                f"private:{self.owner_uid}", alert_text(from_uid, label, text)
            )
            _log(f"已向主账号报告 {from_uid}：{label}")
        except Exception as exc:
            _log(f"报告失败：{type(exc).__name__}: {exc}")

    async def _alert_crisis(self, from_uid: str, text: str, risk: Any) -> None:
        """高危情绪信号：单独发一条给号主，不混进普通安全词。"""
        if not self.owner_uid or self.adapter is None or from_uid == self.owner_uid:
            return
        try:
            body = wellbeing.alert_text(from_uid, "", text, risk)
            await self.adapter.send(f"private:{self.owner_uid}", body)
            _log(f"已向主账号报告 {from_uid} 的情绪高危信号")
        except Exception as exc:
            _log(f"高危报告失败：{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ 媒体
    async def _describe_media(self, media: dict[str, list[dict[str, Any]]]) -> str:
        """把图片读成文字；读不到就返回空，上层退化成占位提示。"""
        bits: list[str] = []
        images = media.get("images") or []
        if images:
            _log(f"收到 {len(images)} 张图片，开始读取")
        for index, item in enumerate(images[:2], 1):
            url = str(item.get("url") or "")
            if not url:
                _log(f"第 {index} 张图没有 url（file={item.get('file')!r}）")
                continue
            try:
                from .vision import Vision

                vision = Vision(self.cfg.vision, self.cfg.agent)
                errors: list[str] = []
                hints = str(getattr(self.cfg.qq, "vision_hints", "") or "").strip()
                prompt = "先用一句话说清画面里有什么。不要猜人物身份，也不要补全看不清的字。"
                if hints:
                    prompt += "\n" + hints
                desc = await vision.describe(url, prompt=prompt, errors=errors)
                if desc:
                    clean = desc.strip().replace("**", "").replace("__", "")
                    bits.append(f"[图片] {clean}")
                    _log(f"第 {index} 张图读到 {len(clean)} 字")
                else:
                    _log(f"第 {index} 张图没读出来：{('；'.join(errors) or '原因未知')}")
            except Exception as exc:
                _log(f"第 {index} 张图读图异常：{type(exc).__name__}: {exc}")
        if media.get("faces"):
            bits.append("[表情] " + "、".join(media["faces"][:3]))
        # 卡片（B 站分享、小程序、音乐）：标题和描述直接就是内容
        for card in (media.get("cards") or [])[:2]:
            line = card_text(card)
            if line:
                bits.append(line)
                _log("卡片消息已读入")
        return "\n".join(bits)

    # ------------------------------------------------------------ 单条
    async def handle(self, event: dict[str, Any]) -> str | None:
        """处理一条 OneBot 事件；不回就返回 None。"""
        post_type = event.get("post_type")
        if post_type != "message":
            # 好友申请 / 群邀请 / 群管理通知：一律不处理，只留痕。
            # 这是硬边界——她只回私聊，不碰好友关系与群管理。
            if post_type in ("request", "notice"):
                sub = event.get("request_type") or event.get("notice_type") or ""
                who = event.get("user_id") or event.get("group_id") or ""
                # 「正在输入」这类通知刷屏，不记；好友申请/群邀请必须留痕
                if sub not in ("notify", "input_status", "online_status"):
                    _log(f"忽略 {post_type}/{sub}（来自 {who}）：只回私聊，不批好友、不进群")
            return None
        message_type = event.get("message_type")
        uid = str(event.get("user_id") or "")
        if not uid or uid == str(getattr(self.cfg.qq, "self_id", "") or ""):
            return None

        if message_type == "group":
            group_id = str(event.get("group_id") or "")
            if not self._group_allowed(group_id):
                return None
            # 群里默认只被 @ 才说话——不然会刷屏，也最容易被风控盯上
            if getattr(self.cfg.qq, "group_require_at", True) and not self._at_me(event):
                return None
            channel = f"group:{group_id}"
        elif message_type == "private":
            if not self.allowed(uid):
                return None
            channel = f"private:{uid}"
        else:
            return None

        media = extract_media(event.get("message"))
        if message_type == "group":
            # 群消息里 @自己 是呼名，不是内容：按段剔除，别用正则去砍正文
            kept = [
                seg
                for seg in (event.get("message") or [])
                if not (isinstance(seg, dict) and seg.get("type") == "at")
            ]
            text = render_segments(kept).strip()
        else:
            text = render_segments(event.get("message")).strip()
        if not text and not any(media.values()):
            _log(f"{uid} 的消息是空的（既没有文字也没有媒体），跳过")
            return None
        _log(f"收到 {channel} 的 {uid}（{len(text)} 字{'' if text else '，只有媒体'}）")

        # 别人发来的风险内容：先报告主账号，再照常回话（不擅自替人做主）
        risk = screen_risk(text)
        if risk:
            await self._alert_owner(uid, risk, text)

        from . import qq_guard

        decision = qq_guard.allow_send(
            self.cfg,
            self.store,
            channel,
            draft=bool(getattr(self.cfg.qq, "dry_run", True)),
        )
        if not decision.allow:
            _log(f"跳过 {channel}：{decision.reason}")
            return None

        # 组装这次要带的额外上下文：角色关系 + 画像 + 主账号的他人概况 + 媒体读数
        extra_bits = [self.role_note(uid, channel)]
        if message_type == "private" and uid == self.owner_uid:
            if getattr(self.cfg.qq, "cross_chat_digest", False):
                others = self._others_note(channel)
                if others:
                    extra_bits.append(others)
            birthday = self._birthday_note(uid)
            if birthday:
                extra_bits.append(birthday)
            # 「发吧」这类短句 = 授权主动发送 5 分钟
            if looks_like_grant(text):
                until = qq_guard.grant_proactive(self.store, minutes=5)
                deadline = time.strftime("%H:%M", time.localtime(until))
                extra_bits.append(
                    f"他刚授权了：接下来 5 分钟（到 {deadline}）你可以主动给好友发消息。"
                    "**现在就能发**——直接调 social_send(platform=\"qq\", "
                    "channel_id=\"private:对方QQ号\", text=\"要发的话\")，"
                    "不用再问、不用说发不出去。发完把结果告诉他。"
                )
                _log(f"主动发送窗口已开，到 {deadline}")
        if message_type == "group":
            extra_bits.append(
                "群里。别人不知道你是谁，不用介绍；私聊里的事一句都别带进来。"
            )
        care = wellbeing.assess(text)
        if care.needs_care:
            extra_bits.append(wellbeing.guide(care, owner=(uid == self.owner_uid)))
            if care.is_high:
                await self._alert_crisis(uid, text, care)
        media_desc = await self._describe_media(media)
        if media_desc:
            extra_bits.append(media_desc)
        elif any(media.values()):
            extra_bits.append(media_note(media))
        extra_context = "\n".join(bit for bit in extra_bits if bit)

        # 媒体读数挨着用户输入放，权重比 extra_context 高——
        # 否则历史里若有「我看不到图」这类旧对话，会把新图压过去。
        plain = text.strip()
        if plain in _MEDIA_PLACEHOLDERS or all(
            seg in _MEDIA_PLACEHOLDERS for seg in re.split(r"(?<=\])", plain) if seg.strip()
        ):
            plain = ""      # 只剩占位符 = 没有真正的文字内容
        if media_desc:
            user_text = f"{plain}\n\n{media_desc}".strip() if plain else media_desc
        else:
            user_text = plain or media_note(media)

        # 先让模型开始想，再等够延迟——总耗时约等于「延迟」而不是「延迟 + 生成」
        history = self._recent(channel)
        answer_task = asyncio.create_task(
            self.agent.answer(
                user_text,
                platform="qq",
                channel_id=channel,
                fast=True,
                extra_context=extra_context,
                history=history,
            )
        )
        if decision.delay > 0:
            await asyncio.sleep(decision.delay)
        reply = await answer_task
        body = (reply.text or "").strip()
        limit = int(getattr(self.cfg.qq, "max_chars", 300) or 300)
        body = body[:limit]
        if not body:
            _log(f"{uid} 的回复是空的，不发")
            return None

        # Dry-run: keep the conversation moving and leave the draft in the log,
        # but never call the OneBot send API.
        if getattr(self.cfg.qq, "dry_run", True):
            _log(f"[dry-run] {channel}（草稿 {len(body)} 字，未发送）")
            self._remember(channel, "user", user_text, save=False)
            self._remember(channel, "assistant", body, save=True)
            return body

        if self.adapter is None:
            _log("适配器未启用，无法发送")
            return None

        # 私聊默认合成一整段发（[qq].split_private）；群聊仍然拆成几句，
        # 群里连着刷小气泡比私聊更吵。角色卡的 || 是她的呼吸，不是发送单位。
        wants_split = message_type == "group" or bool(
            getattr(self.cfg.qq, "split_private", False)
        )
        if wants_split:
            parts = split_segments(
                body, limit=int(getattr(self.cfg.qq, "max_segments", 5) or 5)
            )
        else:
            merged = join_segments(body)
            parts = [merged] if merged else []
        sent: list[str] = []
        for index, part in enumerate(parts):
            try:
                await self.adapter.send(channel, part)
            except Exception as exc:
                # 中途断了就别继续发剩下的——宁可不说完，也不能变成半截话
                _log(f"第 {index + 1} 段发送失败（{type(exc).__name__}），停止后续发送")
                break
            sent.append(part)
            if index < len(parts) - 1:
                await asyncio.sleep(random.uniform(0.8, 2.0))
        if not sent:
            return None
        # 一次回复只记一笔，避免多句把限流额度瞬间吃光
        qq_guard.record_send(self.store)
        # 记进对话历史：存模型真正看到的输入，纯图片消息才不会记成空字符串
        self._remember(channel, "user", user_text, save=False)
        self._remember(channel, "assistant", body, save=True)
        # 更新画像统计；印象够格了才低频刷新一次
        sender = str((event.get("sender") or {}).get("nickname") or "")
        self._touch_profile(channel, sender)
        try:
            await self._refresh_profile(channel)
        except Exception as exc:
            _log(f"画像刷新失败：{type(exc).__name__}: {exc}")
        _log(f"已回复 {channel}（{len(sent)}/{len(parts)} 句 / {len(body)} 字，历史 {len(history)} 条）")
        return body

    # ------------------------------------------------------------ 补漏
    async def catch_up(self, *, minutes: float = 15.0) -> int:
        """补拉重启窗口期漏掉的消息。

        只在「该会话最后一条是对方发的、且发生在最近 N 分钟内」时补——
        否则会把自己刚回过的话再回一遍。
        """
        if self.adapter is None:
            return 0
        api = getattr(self.adapter, "api", None)
        if api is None:
            return 0
        self_id = str(getattr(self.cfg.qq, "self_id", "") or "")
        cutoff = time.time() - minutes * 60
        handled = 0
        for peer in list(getattr(self.cfg.qq, "friend_whitelist", None) or []):
            uid = str(peer)
            if not uid or uid == self_id:
                continue
            try:
                payload = await api("get_friend_msg_history", user_id=int(uid), count=6)
            except Exception:
                continue
            messages = (
                payload.get("messages") if isinstance(payload, dict) else payload
            ) or []
            if not messages:
                continue
            last = messages[-1]
            sender = str(last.get("user_id") or last.get("sender_id") or "")
            stamp = float(last.get("time") or 0)
            if sender != uid or stamp < cutoff:
                continue  # 最后一条不是他发的，或者已经太久了，不补
            stamp_text = time.strftime("%H:%M:%S", time.localtime(stamp))
            _log(f"补拉 {uid} 漏掉的一条（{stamp_text}）")
            event = {
                "post_type": "message",
                "message_type": "private",
                "user_id": int(uid),
                "sender": last.get("sender") or {},
                "message": last.get("message") or [],
                "time": stamp,
            }
            try:
                if await self.handle(event):
                    handled += 1
            except Exception as exc:
                _log(f"补拉失败：{type(exc).__name__}: {exc}")
        if handled:
            _log(f"补拉完毕，处理了 {handled} 条")
        return handled

    # ------------------------------------------------------------ 常驻
    async def _dsh_bg_loop(self, interval: float = 15.0) -> None:
        """Poll background DSH tasks and notify the owner when they finish.

        Only started when the optional desktop package is enabled.  Errors do
        not stop the loop; the next tick retries.
        """
        from . import dsh_bg

        while True:
            try:
                await asyncio.sleep(interval)
                done = await asyncio.to_thread(dsh_bg.tick, self.cfg.server.db_path)
                for item in done:
                    uid = self.owner_uid
                    if not uid or self.adapter is None:
                        continue
                    channel = f"private:{uid}"
                    segments = split_segments(str(item.get("notice") or ""), limit=5)
                    for index, segment in enumerate(segments):
                        if index:
                            await asyncio.sleep(1.2)
                        await self.adapter.send(channel, segment)
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"后台任务轮询出错：{type(exc).__name__}: {exc}")

    async def listen(
        self,
        *,
        timeout: float | None = None,
        on_event: Any = None,
        on_state: Any = None,
        retry: float = 30.0,
    ) -> int:
        """连 NapCat 的 WS 收事件。断线自动重连；`timeout` 秒后退出（None = 一直跑）。

        事件进优先级队列：**主账号的消息永远排在别人前面**。
        单工人串行处理，这样限流计数和发送顺序都是确定的。
        """
        import websockets

        token = str(getattr(self.cfg.qq, "access_token", "") or "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = str(getattr(self.cfg.qq, "ws_url", "") or "")
        if not url:
            raise RuntimeError("没配置 qq.ws_url")

        queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=100)
        seq = 0
        handled = 0
        caught_up = False
        watchdog = qq_watchdog.Watchdog(
            qq_watchdog.alert_path(self.cfg.server.db_path),
            threshold_min=float(getattr(self.cfg.qq, "alert_after_minutes", 10) or 10),
        )
        stop = asyncio.Event()

        def enqueue(priority: int, event: dict[str, Any]) -> None:
            """主账号插队；队列满了丢最旧的一条，并留痕。"""
            nonlocal seq
            seq += 1
            item = (priority, seq, event)
            try:
                queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                pass
            try:
                dropped = queue.get_nowait()
                queue.task_done()
                _log(f"队列已满，丢弃最旧的一条（{dropped[2].get('user_id')}）")
            except Exception:
                pass
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                _log("队列仍然满，这条丢弃")

        async def worker() -> None:
            nonlocal handled
            while not stop.is_set():
                try:
                    _prio, _seq, event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                try:
                    if await self.handle(event):
                        handled += 1
                except Exception as exc:  # 单条失败不拖垮整条链路
                    _log(f"处理失败：{type(exc).__name__}: {exc}")
                finally:
                    queue.task_done()

        worker_task = asyncio.create_task(worker(), name="qq-reply-worker")
        bg_task = None
        if getattr(self.cfg.desktop, "enabled", False):
            bg_task = asyncio.create_task(self._dsh_bg_loop(), name="dsh-bg-poller")
        started = time.time()

        def state(value: bool) -> None:
            if on_state is None:
                return
            try:
                on_state(value)
            except Exception:
                pass

        try:
            while True:
                if timeout is not None and time.time() - started > timeout:
                    _log("到达时限，退出")
                    break
                try:
                    async with websockets.connect(
                        url, additional_headers=headers, ping_interval=30, max_size=8 * 1024 * 1024
                    ) as conn:
                        _log(f"已连接 {url}")
                        state(True)
                        dropped = watchdog.mark_up()
                        if dropped >= 60:
                            _log(f"NapCat 恢复了（断了 {dropped / 60:.0f} 分钟）")
                        # 连上后补一次：进程重启的空档里收到的消息不会重推，只能主动拉
                        if not caught_up:
                            caught_up = True
                            try:
                                await self.catch_up()
                            except Exception as exc:
                                _log(f"补拉出错：{type(exc).__name__}: {exc}")
                        while True:
                            if timeout is not None and time.time() - started > timeout:
                                _log("到达时限，退出")
                                return handled
                            try:
                                raw = await asyncio.wait_for(conn.recv(), timeout=5)
                            except asyncio.TimeoutError:
                                continue
                            except Exception as exc:
                                _log(f"连接断了：{type(exc).__name__}")
                                break
                            try:
                                event = json.loads(raw)
                            except (TypeError, ValueError):
                                continue
                            if on_event is not None:
                                on_event(event)
                            enqueue(self._priority(event), event)
                    state(False)
                except Exception as exc:
                    state(False)
                    _log(f"连不上 NapCat（{type(exc).__name__}），{retry:.0f} 秒后重试")
                    if watchdog.mark_down():
                        _log(f"⚠ 已掉线超过 {watchdog.threshold / 60:.0f} 分钟，已报警")
                if timeout is not None and time.time() - started > timeout:
                    break
                await asyncio.sleep(retry)
            return handled
        finally:
            state(False)
            stop.set()
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
            if bg_task is not None:
                bg_task.cancel()
                try:
                    await bg_task
                except (asyncio.CancelledError, Exception):
                    pass


__all__ = ["FRIEND_NOTE", "OWNER_NOTE", "QQReplier"]
