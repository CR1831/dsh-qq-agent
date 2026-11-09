"""QQ 侧的安全筛查：别让危险内容悄无声息地过去。

只做关键词初筛——快、可解释、不需要额外 API。
命中就往主账号私信里发一条提醒，把判断权交回给人。
"""

from __future__ import annotations

import re

RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"自杀|轻生|不想活|活不下去|结束自己|自残|割腕"), "自伤风险"),
    (re.compile(r"毒品|冰毒|海洛因|大麻|吸毒|摇头丸"), "涉毒"),
    (re.compile(r"赌博|博彩|下注|洗钱|跑分"), "违法"),
    (re.compile(r"杀猪盘|刷单|返利|投资群|稳赚"), "诈骗"),
    (re.compile(r"裸照|约炮|开房|未成年.{0,6}裸"), "涉黄"),
    (re.compile(r"炸弹|爆炸物|买枪|制枪|管制品"), "危险物"),
]


def screen_risk(text: str) -> str:
    """命中返回风险类别，没命中返回空串。"""
    body = str(text or "")
    if not body:
        return ""
    for pattern, label in RISK_PATTERNS:
        if pattern.search(body):
            return label
    return ""


def alert_text(from_uid: str, label: str, text: str, *, limit: int = 120) -> str:
    """给主账号的提醒文案。原文截断，够判断就行。"""
    snippet = str(text or "").strip().replace("\n", " ")
    if len(snippet) > limit:
        snippet = snippet[:limit] + "…"
    return (
        f"【安全提醒】{from_uid} 刚发来一条消息，触发「{label}」。\n"
        f"原文（截断）：{snippet}\n"
        "你看要不要处理，我没擅自回他。"
    )


__all__ = ["RISK_PATTERNS", "alert_text", "screen_risk"]
