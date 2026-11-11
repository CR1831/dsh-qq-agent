from __future__ import annotations

from pathlib import Path

import pytest

from qq_agent.config import load_config
from qq_agent.persona_card import PersonaError, load_persona

CARD = (
    "你是一个测试角色，说话简短，称呼对方为「你」。"
    "遇到重要的话题时句子会收紧，不用形容词堆砌。"
    "不编造事实，不替对方做重大决定，不写括号里的动作。"
    "这是一段足够长的测试设定，用来验证配置加载。"
)


def test_home_paths_are_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[qq]\nenabled = true\npersona_file = "persona.md"\n', encoding="utf-8"
    )
    cfg = load_config()
    assert Path(cfg.home) == tmp_path
    assert Path(cfg.qq.persona_file) == tmp_path / "persona.md"
    assert Path(cfg.server.db_path) == tmp_path / "data" / "qq" / "qq.db"
    assert Path(cfg.qq.data_dir) == tmp_path / "data" / "qq"
    assert cfg.qq.dry_run is True
    assert cfg.qq.auto_restart_napcat is False
    assert cfg.alert.email_to == ""


def test_utf8_bom_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_bytes(b"\xef\xbb\xbf[qq]\nenabled = true\n")
    cfg = load_config()
    assert cfg.qq.enabled is True


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("QQ_AGENT_QQ__DRY_RUN", "false")
    monkeypatch.setenv("QQ_AGENT_QQ__OWNER_UID", "10001")
    cfg = load_config()
    assert cfg.qq.dry_run is False
    assert cfg.qq.owner_uid == 10001


def test_missing_persona_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    cfg = load_config()
    with pytest.raises(PersonaError, match="找不到"):
        load_persona(cfg)


def test_persona_attached_to_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    (tmp_path / "persona.md").write_text("# 测试角色\n\n" + CARD, encoding="utf-8")
    cfg = load_config()
    card = load_persona(cfg)
    assert card.name == "测试角色"
    assert cfg.persona_text == card.text
    assert cfg.persona_name == "测试角色"


def test_no_builtin_persona_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A missing card must never fall back to a shipped character."""
    monkeypatch.setenv("QQ_AGENT_HOME", str(tmp_path))
    cfg = load_config()
    with pytest.raises(PersonaError):
        load_persona(cfg)
    assert cfg.persona_text == ""
