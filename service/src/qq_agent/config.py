"""Configuration for the QQ-only agent.

Load order: dataclass defaults <- ``config.toml`` <- environment variables.

The runtime home defaults to ``~/.dsh/qq-agent`` and holds the config, the
persona card, the data directory and the logs.  Nothing in the runtime home is
part of the repository.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .vision import VisionConfig

APP_NAME = "qq-agent"
DEFAULT_HOME = Path.home() / ".dsh" / "qq-agent"


@dataclass
class AgentConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.4
    max_tokens: int = 800
    max_tool_rounds: int = 4
    timeout: float = 60.0
    # With no API key the agent falls back to deterministic keyword retrieval.
    # That path is kept for tests and for a degraded mode.
    allow_heuristic_fallback: bool = True


@dataclass
class QQConfig:
    enabled: bool = False
    api_base: str = "http://127.0.0.1:3000"
    access_token: str = ""
    ws_url: str = "ws://127.0.0.1:3001"
    self_id: int = 0
    watch_groups: list[int] = field(default_factory=list)
    history_limit: int = 200
    live_fetch: bool = True

    # ---- account / persona -------------------------------------------------
    owner_uid: int = 0
    display_name: str = ""              # panel label; never parsed from the card
    persona: str = ""                   # inline card text (tests / advanced use)
    persona_file: str = ""              # default: <home>/persona.md
    knowledge_dir: str = ""             # optional *.md appended to the prompt
    use_profile_facts: bool = False     # inject age/school/birthday from QQ profile
    cross_chat_digest: bool = False     # let the owner ask about other conversations
    vision_hints: str = ""              # optional character hints for image reading
    owner_note: str = ""                # optional extra note for the owner chat
    data_dir: str = ""                  # default: <home>/data/qq

    # ---- outbound guard (all enforced in qq_guard, not in the prompt) ------
    dry_run: bool = True                # generate replies but do not send them
    friend_whitelist: list[int] = field(default_factory=list)
    group_whitelist: list[int] = field(default_factory=list)
    group_require_at: bool = True
    proactive: bool = False
    rate_per_minute: int = 1
    rate_per_hour: int = 20
    rate_per_day: int = 200
    min_interval_seconds: float = 30.0
    quiet_hours: list[int] = field(default_factory=lambda: [23, 0, 1, 2, 3, 4, 5, 6, 7])
    reply_delay_min: float = 1.0
    reply_delay_max: float = 5.0
    max_chars: int = 300
    sticker_min_minutes: float = 20.0
    alert_after_minutes: float = 10.0
    max_segments: int = 5
    split_private: bool = False
    history_turns: int = 8
    history_keep: int = 60
    history_peers: int = 20
    history_max_chars: int = 800

    # ---- NapCat watchdog (opt-in; off by default) --------------------------
    auto_restart_napcat: bool = False
    restart_napcat_after_minutes: float = 2.0
    napcat_home: str = ""                # directory containing launcher-user.bat
    napcat_launcher: str = "launcher-user.bat"


@dataclass
class WebSearchConfig:
    enabled: bool = True
    provider: str = "bing"
    timeout: float = 20.0
    max_results: int = 5
    user_agent: str = ""
    raise_on_empty: bool = False


@dataclass
class ServerConfig:
    # Kept for the copied QQ modules: the database path is the anchor for the
    # data directory (history.json, profiles.json, stickers, style memory).
    db_path: str = ""


@dataclass
class DesktopConfig:
    """Optional desktop-control package.  Disabled unless the extra is installed."""

    enabled: bool = False
    dsh_exe: str = ""
    dsh_home: str = ""
    session_id: str = "qq-agent"
    cwd: str = ""
    timeout_ms: int = 90000


@dataclass
class AlertConfig:
    """Optional email alert when NapCat stays offline past the threshold."""

    email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = True
    timeout: float = 15.0


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    qq: QQConfig = field(default_factory=QQConfig)
    websearch: WebSearchConfig = field(default_factory=WebSearchConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    desktop: DesktopConfig = field(default_factory=DesktopConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # Runtime-only values, filled by load_config().
    home: str = field(default=str(DEFAULT_HOME), repr=False)
    persona_text: str = field(default="", repr=False)
    persona_name: str = field(default="", repr=False)
    persona_source: str = field(default="", repr=False)


def _coerce(text: str) -> Any:
    lowered = text.strip()
    if lowered.lower() in {"true", "false"}:
        return lowered.lower() == "true"
    try:
        return int(lowered)
    except ValueError:
        pass
    try:
        return float(lowered)
    except ValueError:
        pass
    if lowered.startswith(("[", "{")):
        try:
            return json.loads(lowered)
        except ValueError:
            pass
    return text


def _apply_section(obj: Any, values: dict[str, Any]) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            continue
        current = getattr(obj, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply_section(current, value)
        else:
            setattr(obj, key, value)


def _apply_env(cfg: Config) -> None:
    prefix = "QQ_AGENT_"
    for section in fields(cfg):
        target = getattr(cfg, section.name)
        if not hasattr(target, "__dataclass_fields__"):
            continue
        env_prefix = f"{prefix}{section.name.upper()}__"
        for item in fields(target):
            raw = os.environ.get(env_prefix + item.name.upper())
            if raw is not None:
                setattr(target, item.name, _coerce(raw))


def _resolve(value: str, home: Path) -> str:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = home / path
    return str(path)


def default_home() -> Path:
    raw = os.environ.get("QQ_AGENT_HOME", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_HOME


def load_config(path: str | Path | None = None) -> Config:
    """Load the TOML config and normalise every runtime path under the home."""
    cfg = Config()
    home = default_home()
    cfg.home = str(home)

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    candidates.append(home / "config.toml")
    # Development convenience: running from a checkout with a local config.
    candidates.append(Path.cwd() / "config.toml")

    chosen = next((item for item in candidates if item.is_file()), None)
    if chosen is not None:
        raw = chosen.read_text(encoding="utf-8-sig")
        data = tomllib.loads(raw)
        for section, values in data.items():
            target = getattr(cfg, section, None)
            if target is not None and isinstance(values, dict):
                _apply_section(target, values)

    _apply_env(cfg)

    # Paths: everything relative is resolved against the runtime home, never
    # against the repository or the process cwd.
    home.mkdir(parents=True, exist_ok=True)
    cfg.qq.data_dir = _resolve(cfg.qq.data_dir or "data/qq", home)
    cfg.qq.persona_file = _resolve(cfg.qq.persona_file or "persona.md", home)
    cfg.qq.knowledge_dir = _resolve(cfg.qq.knowledge_dir, home) if cfg.qq.knowledge_dir else ""
    cfg.server.db_path = _resolve(cfg.server.db_path or "data/qq/qq.db", home)
    cfg.vision.cache_dir = _resolve(cfg.vision.cache_dir or "data/image_cache", home)
    cfg.desktop.dsh_home = _resolve(cfg.desktop.dsh_home, home) if cfg.desktop.dsh_home else ""
    cfg.desktop.cwd = _resolve(cfg.desktop.cwd, home) if cfg.desktop.cwd else ""
    return cfg


def ensure_dirs(cfg: Config) -> None:
    Path(cfg.home).mkdir(parents=True, exist_ok=True)
    Path(cfg.qq.data_dir).mkdir(parents=True, exist_ok=True)
    (Path(cfg.home) / "logs").mkdir(parents=True, exist_ok=True)
    if cfg.vision.enabled:
        Path(cfg.vision.cache_dir).mkdir(parents=True, exist_ok=True)


def public_dict(cfg: Config) -> dict[str, Any]:
    """Config values that are safe to show in the panel or logs."""
    return {
        "home": cfg.home,
        "qq": {
            "enabled": cfg.qq.enabled,
            "api_base": cfg.qq.api_base,
            "ws_url": cfg.qq.ws_url,
            "self_id": cfg.qq.self_id,
            "owner_uid": cfg.qq.owner_uid,
            "display_name": cfg.qq.display_name,
            "dry_run": cfg.qq.dry_run,
            "friend_whitelist": cfg.qq.friend_whitelist,
            "group_whitelist": cfg.qq.group_whitelist,
            "proactive": cfg.qq.proactive,
            "persona_file": cfg.qq.persona_file,
        },
        "agent": {
            "model": cfg.agent.model,
            "llm": bool(cfg.agent.api_key),
        },
        "vision": {"enabled": cfg.vision.enabled},
        "desktop": {"enabled": cfg.desktop.enabled},
        "alert": {"email": bool(cfg.alert.email_to)},
    }


__all__ = [
    "APP_NAME",
    "DEFAULT_HOME",
    "AgentConfig",
    "QQConfig",
    "WebSearchConfig",
    "ServerConfig",
    "DesktopConfig",
    "AlertConfig",
    "Config",
    "default_home",
    "ensure_dirs",
    "load_config",
    "public_dict",
]
