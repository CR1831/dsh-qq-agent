"""QQ-only adapters."""

from __future__ import annotations

from ..config import Config
from .base import AdapterError, BaseAdapter
from .qq_onebot import QQOneBotAdapter

__all__ = ["AdapterError", "BaseAdapter", "QQOneBotAdapter", "build_adapters"]


def build_adapters(cfg: Config) -> dict[str, BaseAdapter]:
    if not getattr(cfg.qq, "enabled", False):
        return {}
    return {"qq": QQOneBotAdapter(cfg.qq)}
