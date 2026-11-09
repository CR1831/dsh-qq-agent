from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from qq_agent.persona_card import (
    MAX_CHARS,
    PersonaError,
    load_persona_card,
    render_character_card,
)

LONG = (
    "你是一个测试角色，说话简短，称呼对方为「你」。"
    "遇到重要的话题时句子会收紧，不用形容词堆砌。"
    "不编造事实，不替对方做重大决定，不写括号里的动作。"
    "这是一段足够长的测试设定，用来验证最小长度与渲染结果。"
)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png_card(card: dict) -> bytes:
    payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"tEXt", b"chara\x00" + payload)
        + _chunk(b"IEND", b"")
    )


def test_markdown_card(tmp_path: Path):
    path = tmp_path / "persona.md"
    path.write_text("# 测试角色\n\n" + LONG, encoding="utf-8")
    card = load_persona_card(path)
    assert card.name == "测试角色"
    assert card.format == "markdown"
    assert card.chars >= 80
    assert "测试角色" in card.text


def test_missing_card(tmp_path: Path):
    with pytest.raises(PersonaError, match="找不到"):
        load_persona_card(tmp_path / "nope.md")


def test_template_placeholder_rejected(tmp_path: Path):
    path = tmp_path / "persona.md"
    path.write_text("# <角色名>\n" + LONG + " TODO", encoding="utf-8")
    with pytest.raises(PersonaError, match="占位符"):
        load_persona_card(path)


def test_short_card_rejected(tmp_path: Path):
    path = tmp_path / "persona.md"
    path.write_text("太短了", encoding="utf-8")
    with pytest.raises(PersonaError, match="太短"):
        load_persona_card(path)


def test_too_long_rejected(tmp_path: Path):
    path = tmp_path / "persona.md"
    path.write_text("很长" * (MAX_CHARS // 2 + 10), encoding="utf-8")
    with pytest.raises(PersonaError, match="太长"):
        load_persona_card(path)


def test_sillytavern_json(tmp_path: Path):
    path = tmp_path / "persona.json"
    path.write_text(
        json.dumps(
            {
                "spec": "chara_card_v2",
                "data": {
                    "name": "测试角色",
                    "description": LONG,
                    "personality": "简短、直接。",
                    "scenario": "私聊。",
                    "first_mes": "在。",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    card = load_persona_card(path)
    assert card.name == "测试角色"
    assert card.format == "sillytavern-json"
    assert "## 性格" in card.text
    assert "## 开场白" in card.text


def test_sillytavern_png(tmp_path: Path):
    path = tmp_path / "persona.png"
    path.write_bytes(
        _png_card(
            {
                "spec": "chara_card_v2",
                "data": {"name": "PNG 角色", "description": LONG, "personality": "平静。"},
            }
        )
    )
    card = load_persona_card(path)
    assert card.name == "PNG 角色"
    assert card.format == "sillytavern-png"


def test_plain_png_rejected(tmp_path: Path):
    path = tmp_path / "plain.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IEND", b""))
    with pytest.raises(PersonaError, match="没有角色卡数据"):
        load_persona_card(path)


def test_render_character_card_ignores_book_with_warning():
    text, name, warnings = render_character_card(
        {
            "name": "角色",
            "description": LONG,
            "character_book": {"entries": [{"keys": ["x"], "content": "y"}]},
        }
    )
    assert name == "角色"
    assert "character_book" in " ".join(warnings)
    assert "角色" in text
