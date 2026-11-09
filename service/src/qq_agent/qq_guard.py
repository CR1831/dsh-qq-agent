"""QQ 发送护栏：限流 / 免打扰 / 随机延迟 / 好友白名单。

OneBot 协议模拟（Lagrange、LLOneBot）不像注入式那样动客户端，
但风控看的还是**行为**：真人不会 24 小时秒回，也不会一分钟发十条。

所以这些约束做成代码里的硬闸，而不是靠提示词自觉——
模型想发，也得先过这里。

设计取向：
- **失败关闭**：`friend_whitelist` 空着就一律不接私聊（宁可少说，别误发）；
- 只统计最近 24 小时的发送记录，存在 meta 里，不新增表；
- 返回的 `delay` 由调用方 `await asyncio.sleep()`，护栏本身不阻塞。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any

LOG_KEY = "qq_send_log"
KEEP_SECONDS = 24 * 3600


@dataclass
class GuardDecision:
    allow: bool
    reason: str = ""
    delay: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"allow": self.allow, "reason": self.reason, "delay": round(self.delay, 2)}


def _history(store: Any, *, now: float) -> list[float]:
    raw = store.meta_get(LOG_KEY) or ""
    try:
        rows = json.loads(raw) if raw else []
    except Exception:
        rows = []
    if not isinstance(rows, list):
        return []
    cutoff = now - KEEP_SECONDS
    out: list[float] = []
    for item in rows:
        try:
            stamp = float(item)
        except (TypeError, ValueError):
            continue
        if stamp >= cutoff:
            out.append(stamp)
    return out


def _save(store: Any, rows: list[float]) -> None:
    try:
        store.meta_set(LOG_KEY, json.dumps(rows[-800:]))
    except Exception as exc:
        # 写不进去等于限流失效，但不能因此让发送失败——只留痕
        print(f"[qq_guard] 发送记录写入失败：{type(exc).__name__}: {exc}", flush=True)


def parse_channel(channel_id: str) -> tuple[str, str]:
    """``private:123`` / ``group:456``（也接受不带前缀的私聊号）。"""
    kind, _, target = str(channel_id or "").partition(":")
    if not target:
        return "private", kind
    return kind or "private", target


def allow_send(
    cfg: Any,
    store: Any,
    channel_id: str,
    *,
    proactive: bool = False,
    draft: bool = False,
    now: float | None = None,
) -> GuardDecision:
    """能不能发。`proactive=True` 表示不是回复而是主动推送。

    `draft=True` 只检查接入与白名单，跳过免打扰、限流和随机延迟——dry-run
    需要连续生成草稿，不能被发送频次卡住。
    """
    moment = time.time() if now is None else now
    qq = cfg.qq
    kind, target = parse_channel(channel_id)

    if not getattr(qq, "enabled", False):
        return GuardDecision(False, "QQ 未启用")

    if kind == "private":
        whitelist = {str(x) for x in (getattr(qq, "friend_whitelist", None) or [])}
        if not whitelist:
            return GuardDecision(False, "没配置好友白名单（qq.friend_whitelist），默认不接私聊")
        if str(target) not in whitelist:
            return GuardDecision(False, f"{target} 不在 QQ 好友白名单里")

    if kind == "group":
        whitelist = {str(x) for x in (getattr(qq, "group_whitelist", None) or [])}
        if not whitelist:
            return GuardDecision(False, "没配置群白名单（qq.group_whitelist），默认不在群里说话")
        if str(target) not in whitelist:
            return GuardDecision(False, f"{target} 不在 QQ 群白名单里")

    if draft:
        return GuardDecision(True, "", 0.0)

    if proactive and not getattr(qq, "proactive", False):
        # 没全局开主动发送时，看有没有临时授权窗口（主账号说「发吧」才开）
        until = proactive_until(store)
        if moment >= until:
            remain_hint = ""
            return GuardDecision(
                False,
                "未开启主动发送（qq.proactive = false）；"
                "需要在私信里说「发吧」临时授权 5 分钟" + remain_hint,
            )

    hour = time.localtime(moment).tm_hour
    if hour in set(getattr(qq, "quiet_hours", None) or []):
        return GuardDecision(False, f"{hour} 点在免打扰时段")

    rows = _history(store, now=moment)
    # 两条之间的最小间隔：比「每分钟 N 条」更贴近真人——真人不会连珠炮
    interval = float(getattr(qq, "min_interval_seconds", 0) or 0)
    if interval > 0 and rows:
        gap = moment - max(rows)
        if gap < interval:
            return GuardDecision(
                False, f"距上一条还不到 {int(interval)} 秒（还差 {int(interval - gap)} 秒）"
            )
    sent_minute = sum(1 for t in rows if moment - t < 60)
    sent_hour = sum(1 for t in rows if moment - t < 3600)
    if sent_minute >= max(1, int(getattr(qq, "rate_per_minute", 1) or 1)):
        return GuardDecision(False, "每分钟发送已达上限")
    if sent_hour >= max(1, int(getattr(qq, "rate_per_hour", 20) or 20)):
        return GuardDecision(False, "每小时发送已达上限")
    if len(rows) >= max(1, int(getattr(qq, "rate_per_day", 200) or 200)):
        return GuardDecision(False, "今天发送已达上限")

    low = max(0.0, float(getattr(qq, "reply_delay_min", 0) or 0))
    high = max(low, float(getattr(qq, "reply_delay_max", low) or low))
    return GuardDecision(True, "", random.uniform(low, high))


def record_send(store: Any, *, now: float | None = None) -> None:
    """发送成功后记一笔，限流才有依据。"""
    moment = time.time() if now is None else now
    rows = _history(store, now=moment)
    rows.append(moment)
    _save(store, rows)


# ---------------------------------------------------------------- 临时授权
PROACTIVE_KEY = "qq_proactive_until"


def proactive_until(store: Any) -> float:
    """临时授权的截止时间戳；没授权返回 0。"""
    raw = store.meta_get(PROACTIVE_KEY) or ""
    try:
        return float(raw) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0


def grant_proactive(store: Any, *, minutes: float = 5.0, now: float | None = None) -> float:
    """开一个临时窗口，允许主动发送。返回截止时间戳。

    主账号说「发吧」之类才触发——这样她不会自己找人说话，
    但在你明确要求的那一刻又确实能发出去。
    """
    moment = time.time() if now is None else now
    until = moment + max(0.5, float(minutes)) * 60
    try:
        store.meta_set(PROACTIVE_KEY, str(until))
    except Exception:
        pass
    return until


def clear_proactive(store: Any) -> None:
    try:
        store.meta_set(PROACTIVE_KEY, "0")
    except Exception:
        pass


# ---------------------------------------------------------------- 表情
STICKER_KEY = "qq_sticker_last"


def allow_sticker(cfg: Any, store: Any, *, now: float | None = None) -> GuardDecision:
    """表情比普通消息更需要克制——只按「距上次多久」拦。

    真人发收藏表情的频率很低，隔几分钟就甩一个反而像机器人。
    """
    moment = time.time() if now is None else now
    gap_min = float(getattr(cfg.qq, "sticker_min_minutes", 20.0) or 20.0)
    if gap_min <= 0:
        return GuardDecision(True)
    raw = store.meta_get(STICKER_KEY) or ""
    try:
        last = float(raw) if raw else 0.0
    except (TypeError, ValueError):
        last = 0.0
    if last and moment - last < gap_min * 60:
        remain = int(gap_min - (moment - last) / 60) or 1
        return GuardDecision(
            False, f"距上次发表情还不到 {int(gap_min)} 分钟（还差 {remain} 分钟）"
        )
    return GuardDecision(True)


def record_sticker(store: Any, *, now: float | None = None) -> None:
    moment = time.time() if now is None else now
    try:
        store.meta_set(STICKER_KEY, str(moment))
    except Exception:
        pass


def status(cfg: Any, store: Any, *, now: float | None = None) -> dict[str, Any]:
    moment = time.time() if now is None else now
    qq = cfg.qq
    rows = _history(store, now=moment)
    return {
        "enabled": bool(getattr(qq, "enabled", False)),
        "proactive": bool(getattr(qq, "proactive", False)),
        "whitelist": [str(x) for x in (getattr(qq, "friend_whitelist", None) or [])],
        "quiet_hours": list(getattr(qq, "quiet_hours", None) or []),
        "delay": [
            float(getattr(qq, "reply_delay_min", 0) or 0),
            float(getattr(qq, "reply_delay_max", 0) or 0),
        ],
        "sent": {
            "minute": sum(1 for t in rows if moment - t < 60),
            "hour": sum(1 for t in rows if moment - t < 3600),
            "day": len(rows),
        },
        "limits": {
            "per_minute": int(getattr(qq, "rate_per_minute", 1) or 1),
            "per_hour": int(getattr(qq, "rate_per_hour", 20) or 20),
            "per_day": int(getattr(qq, "rate_per_day", 200) or 200),
        },
    }


__all__ = [
    "GuardDecision",
    "LOG_KEY",
    "PROACTIVE_KEY",
    "STICKER_KEY",
    "allow_send",
    "allow_sticker",
    "clear_proactive",
    "grant_proactive",
    "parse_channel",
    "proactive_until",
    "record_send",
    "record_sticker",
    "status",
]
