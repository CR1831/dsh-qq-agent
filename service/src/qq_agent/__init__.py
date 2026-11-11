"""QQ-only agent: OneBot 11 ingestion, retrieval, and guarded replies."""

from __future__ import annotations

from .net import sanitize_proxy_env

sanitize_proxy_env()

from .agent import AgentReply, QQAgent  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .models import Doc  # noqa: E402
from .store import Store  # noqa: E402

__version__ = "0.4.0"

__all__ = ["QQAgent", "AgentReply", "Config", "load_config", "Doc", "Store", "__version__"]
