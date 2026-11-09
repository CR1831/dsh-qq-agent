"""情绪与认知调节：给出具体可做的措施，但绝不做诊断。

- 评估风险等级（high / medium / low / none）；
- 生成给模型的提示：先共情，再给 2–3 条具体措施，明确「我不是医生」；
- 高危时给号主一条紧急通知。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

HIGH_SIGNALS = (
    "自杀", "轻生", "自残", "自伤", "割腕", "跳楼", "上吊", "安眠药",
    "不想活", "活不下去", "结束自己", "死了算了", "一了百了", "遗书",
    "伤害自己", "想死", "不想醒过来",
)
MEDIUM_SIGNALS = (
    "抑郁", "焦虑", "失眠", "崩溃", "绝望", "没意义", "活着累", "压力好大",
    "想哭", "什么都不想做", "提不起劲", "心慌", "恐慌", "撑不住", "麻木",
    "没有价值", "自我怀疑",
)
LOW_SIGNALS = (
    "难过", "心烦", "好累", "郁闷", "emo", "委屈", "不开心", "丧", "烦躁",
)

MEASURES = (
    "做一分钟慢呼吸（吸 4 秒、停 2 秒、呼 6 秒），先把身体降下来",
    "用 5-4-3-2-1 落地法：说出眼前 5 样东西、4 样能摸到的、3 种声音",
    "把此刻最扎人的那句话写下来，再写一句「另一种可能的解释」",
    "挑一件五分钟能做完的小事做完，先找回一点掌控感",
    "喝水、洗把脸、下楼走五分钟，让身体先动起来",
    "给一个信得过的人发一句「我现在有点难受」，不用解释太多",
)

NO_DIAGNOSIS = (
    "我不做诊断、也不是医生，不会给你贴任何病名；"
    "能做的只是陪你理一理、给几条现在就能试的办法。"
)


@dataclass
class Risk:
    level: str = "none"          # high / medium / low / none
    signals: list[str] = field(default_factory=list)

    @property
    def is_high(self) -> bool:
        return self.level == "high"

    @property
    def needs_care(self) -> bool:
        return self.level in ("high", "medium")


def assess(text: str) -> Risk:
    body = (text or "").strip()
    if not body:
        return Risk()
    hits: list[str] = []
    level = "none"
    for word in HIGH_SIGNALS:
        if word in body:
            hits.append(word)
            level = "high"
    if level != "high":
        for word in MEDIUM_SIGNALS:
            if word in body:
                hits.append(word)
                level = "medium"
    if level == "none":
        for word in LOW_SIGNALS:
            if word in body:
                hits.append(word)
                level = "low"
    return Risk(level=level, signals=hits[:5])


def guide(risk: Risk, *, owner: bool = False) -> str:
    """给模型的额外要求：只给措施，不做诊断。"""
    if risk.level == "none":
        return ""
    measures = "；".join(MEASURES[:3])
    if risk.is_high:
        head = (
            "【情绪支持·高危】对方的话里出现了自伤/轻生的信号。第一句先请他停下来、"
            "远离危险的东西，并马上联系身边可信任的人；明确告诉他紧急情况拨 120 或 110。"
        )
    elif risk.level == "medium":
        head = "【情绪支持】对方情绪明显在低位，先接住，再给可执行的小步骤。"
    else:
        head = "【情绪支持】对方有点低落，安慰一句就好，不用大动干戈。"
    tail = (
        f"措施可以挑一两条说：{measures}。"
        f"{NO_DIAGNOSIS}"
        "不要说「别想太多」「一切都会好起来」这种空话，也不要追问细节刺激对方。"
    )
    if owner:
        tail += "对方是号主本人，语气可以更亲近，但仍然不要下诊断。"
    return head + tail


def alert_text(peer: str, nickname: str, message: str, risk: Risk) -> str:
    who = nickname or peer
    return (
        "🚨 紧急：有人私信里出现了轻生/自伤信号\n"
        f"来源：{who}（{peer}）\n"
        f"原话：「{(message or '').strip()[:80]}」\n"
        f"时间：{time.strftime('%m-%d %H:%M')}\n"
        "助手已经先回了他，给了安抚和求助建议；请尽快确认一下对方是否安全。"
    )


def crisis_line() -> str:
    return "如果有马上伤害自己的冲动，请立刻拨 120 或 110，或者叫身边的人陪着你。"


__all__ = [
    "HIGH_SIGNALS",
    "MEDIUM_SIGNALS",
    "LOW_SIGNALS",
    "MEASURES",
    "NO_DIAGNOSIS",
    "Risk",
    "assess",
    "guide",
    "alert_text",
    "crisis_line",
]
