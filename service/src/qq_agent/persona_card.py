"""Persona-card loading.

The plugin ships **no** persona.  A user points ``[qq].persona_file`` at their
own card and the service loads it at startup.  Supported formats:

* ``.md`` / ``.markdown`` / ``.txt`` — plain text / Markdown card;
* ``.json`` — SillyTavern V2/V3 card, or a flat object with the same fields;
* ``.png`` — SillyTavern PNG card (``chara`` / ``ccv3`` textual chunk).

The loader only produces text and a display name.  It never logs the card
content, and the panel never reads the card: the service publishes a small
status record instead.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MIN_CHARS = 80
MAX_CHARS = 20_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PNG_TEXT_BYTES = 10 * 1024 * 1024

PLACEHOLDERS = (
    "TODO",
    "<角色名>",
    "<角色名>",
    "{{name}}",
    "{{char}}",
    "在此填写",
    "请替换",
)

_HEADING = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)


class PersonaError(ValueError):
    """The card is missing, unreadable, or still a template."""


@dataclass
class PersonaCard:
    name: str
    text: str
    source: str
    format: str
    chars: int = 0
    warnings: list[str] = field(default_factory=list)

    def status(self) -> dict[str, Any]:
        return {
            "loaded": True,
            "name": self.name,
            "source": self.source,
            "format": self.format,
            "chars": self.chars,
            "warnings": list(self.warnings),
        }


def _clean(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _name_from_heading(text: str) -> str:
    matches = _HEADING.findall(text)
    if not matches:
        return ""
    return " ".join(matches[0].split())[:80]


def _validate(text: str, source: str) -> str:
    body = _clean(text)
    if "\x00" in body:
        raise PersonaError(f"{source}：人格卡里有 NUL 字符，不是文本文件")
    if len(body) < MIN_CHARS:
        raise PersonaError(
            f"{source}：人格卡太短（{len(body)} 字，至少 {MIN_CHARS} 字）。"
            "写清身份、说话方式和边界，再启动。"
        )
    if len(body) > MAX_CHARS:
        raise PersonaError(f"{source}：人格卡太长（{len(body)} 字，上限 {MAX_CHARS} 字）")
    upper = body.upper()
    for token in PLACEHOLDERS:
        if token.upper() in upper:
            raise PersonaError(
                f"{source}：人格卡里还有占位符「{token}」，先把模板替换成你自己的设定"
            )
    return body


# ---------------------------------------------------------------- plain text
def _load_text(path: Path) -> PersonaCard:
    raw = path.read_text(encoding="utf-8-sig")
    body = _validate(raw, str(path))
    name = _name_from_heading(body) or path.stem
    return PersonaCard(name=name, text=body, source=str(path), format="markdown", chars=len(body))


# ---------------------------------------------------------------- JSON / PNG
def _as_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PersonaError("人格卡 JSON 的根节点不是对象")
    data = raw.get("data")
    if isinstance(data, dict):
        return data
    return raw


def _field(data: dict[str, Any], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return _clean(value)
    return ""


def render_character_card(raw: Any) -> tuple[str, str, list[str]]:
    """Render a SillyTavern-style card object into a single prompt text."""
    data = _as_dict(raw)
    name = _field(data, "name", "char_name")[:80] or "未命名角色"
    warnings: list[str] = []

    sections: list[tuple[str, str]] = [
        ("角色设定", _field(data, "description", "char_persona")),
        ("性格", _field(data, "personality")),
        ("场景", _field(data, "scenario", "world_scenario")),
        ("系统指令", _field(data, "system_prompt")),
        ("补充指令", _field(data, "post_history_instructions")),
        ("开场白（只作语气参考，不要自动发送）", _field(data, "first_mes", "char_greeting")),
        ("示例对话（只作语气参考）", _field(data, "mes_example", "example_dialogue")),
    ]

    book = data.get("character_book") or data.get("characterBook")
    if isinstance(book, dict):
        entries = book.get("entries")
        if isinstance(entries, list) and entries:
            warnings.append("character_book 未展开，只保留角色主设定")
    if data.get("extensions"):
        warnings.append("extensions 已忽略")

    body = [f"# {name}", ""]
    for title, value in sections:
        if not value:
            continue
        body.append(f"## {title}")
        body.append(value)
        body.append("")
    text = "\n".join(body).strip()
    # A card with only a name and an empty description is not a card.
    if len(text) - len(f"# {name}") < 40:
        warnings.append("卡片主体内容很少，模型可能缺少稳定的行为规则")
    return text, name, warnings


def _load_json(path: Path) -> PersonaCard:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PersonaError(f"{path}：不是合法 JSON（{exc}）") from exc
    text, name, warnings = render_character_card(raw)
    body = _validate(text, str(path))
    return PersonaCard(
        name=name,
        text=body,
        source=str(path),
        format="sillytavern-json",
        chars=len(body),
        warnings=warnings,
    )


def _png_text_chunks(blob: bytes) -> dict[str, str]:
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PersonaError("文件不是 PNG")
    out: dict[str, str] = {}
    offset = 8
    total = len(blob)
    while offset + 8 <= total:
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        kind = blob[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if length > MAX_PNG_TEXT_BYTES or data_end + 4 > total:
            break
        chunk = blob[data_start:data_end]
        offset = data_end + 4  # skip CRC

        if kind == b"tEXt":
            keyword, _, text = chunk.partition(b"\x00")
            try:
                out[keyword.decode("latin-1")] = text.decode("latin-1")
            except Exception:
                continue
        elif kind == b"zTXt":
            keyword, _, rest = chunk.partition(b"\x00")
            if len(rest) < 1:
                continue
            try:
                out[keyword.decode("latin-1")] = zlib.decompress(rest[1:]).decode("latin-1")
            except Exception:
                continue
        elif kind == b"iTXt":
            # keyword \0 flag \0 method \0 language \0 translated \0 text
            parts = chunk.split(b"\x00", 5)
            if len(parts) != 6 or parts[1] == b"1":
                # compressed iTXt is rare; skip it rather than half-decoding
                continue
            keyword = parts[0].decode("latin-1")
            try:
                out[keyword] = parts[5].decode("utf-8")
            except Exception:
                continue
        if kind == b"IEND":
            break
    return out


def _decode_card_text(raw: str, source: str) -> Any:
    value = raw.strip()
    # SillyTavern stores base64.  A few exporters store raw JSON; support both.
    for candidate in (value,):
        try:
            decoded = base64.b64decode(candidate, validate=False).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass
    try:
        return json.loads(value)
    except ValueError as exc:
        raise PersonaError(f"{source}：PNG 里的角色卡不是 base64/JSON") from exc


def _load_png(path: Path) -> PersonaCard:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise PersonaError(f"{path}：PNG 太大（{size} 字节，上限 {MAX_FILE_BYTES}）")
    chunks = _png_text_chunks(path.read_bytes())
    raw = chunks.get("ccv3") or chunks.get("chara")
    if not raw:
        raise PersonaError(
            f"{path}：这张 PNG 里没有角色卡数据（缺少 chara / ccv3 文本块）。"
            "请确认它是 SillyTavern 导出的角色卡，而不是普通图片。"
        )
    data = _decode_card_text(raw, str(path))
    text, name, warnings = render_character_card(data)
    body = _validate(text, str(path))
    return PersonaCard(
        name=name,
        text=body,
        source=str(path),
        format="sillytavern-png",
        chars=len(body),
        warnings=warnings,
    )


# ---------------------------------------------------------------- public API
def load_persona_card(path: str | Path) -> PersonaCard:
    file = Path(path).expanduser()
    if not file.is_file():
        raise PersonaError(f"找不到人格卡：{file}")
    suffix = file.suffix.lower()
    if suffix == ".json":
        return _load_json(file)
    if suffix == ".png":
        return _load_png(file)
    if suffix in {".md", ".markdown", ".txt", ""}:
        return _load_text(file)
    # Unknown extension: try text, then JSON.  This keeps .card/.persona usable.
    try:
        return _load_text(file)
    except (UnicodeDecodeError, PersonaError):
        try:
            return _load_json(file)
        except PersonaError:
            raise PersonaError(
                f"{file}：不支持的人格卡格式（支持 .md/.txt/.json/.png）"
            ) from None


def load_persona(cfg: Any) -> PersonaCard:
    """Load the inline card or the configured file and attach it to ``cfg``."""
    inline = _clean(getattr(cfg.qq, "persona", "") or "")
    if inline:
        body = _validate(inline, "[qq].persona")
        card = PersonaCard(
            name=_name_from_heading(body) or "自定义角色",
            text=body,
            source="[qq].persona",
            format="inline",
            chars=len(body),
        )
    else:
        path = getattr(cfg.qq, "persona_file", "") or ""
        if not path:
            raise PersonaError("没有配置人格卡：设置 [qq].persona_file，或写 [qq].persona")
        card = load_persona_card(path)

    cfg.persona_text = card.text
    cfg.persona_name = card.name
    cfg.persona_source = card.source

    knowledge = _clean(getattr(cfg.qq, "knowledge_dir", "") or "")
    if knowledge:
        folder = Path(knowledge).expanduser()
        if folder.is_dir():
            chunks: list[str] = []
            for doc in sorted(folder.glob("*.md")):
                try:
                    body = doc.read_text(encoding="utf-8-sig").strip()
                except OSError:
                    continue
                if body:
                    chunks.append(body)
            if chunks:
                card.text = (
                    card.text.rstrip()
                    + "\n\n【知识附录 · 需要时直接取用，不必复述来源】\n"
                    + "\n\n".join(chunks)
                )
                card.chars = len(card.text)
                cfg.persona_text = card.text
    return card


__all__ = [
    "MIN_CHARS",
    "MAX_CHARS",
    "PersonaCard",
    "PersonaError",
    "load_persona",
    "load_persona_card",
    "render_character_card",
]
