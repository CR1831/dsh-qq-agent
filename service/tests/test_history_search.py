"""历史对话检索的测试（2026-11-10 补）。"""

import json
import time
from pathlib import Path

import pytest

from qq_agent import history_search


def _db(tmp_path: Path) -> Path:
    return tmp_path / "qq.db"


def _write(tmp_path: Path, data: dict) -> None:
    (tmp_path / "history.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _rows(*pairs, base_ts: float = 1_700_000_000.0):
    """pairs: (role, content) → 带递增时间戳的行。"""
    out = []
    for i, (role, content) in enumerate(pairs):
        out.append({"role": role, "content": content, "ts": base_ts + i * 60})
    return out


def test_search_finds_keyword_across_channels(tmp_path):
    _write(
        tmp_path,
        {
            "private:111": _rows(("user", "我最近在准备考试"), ("assistant", "什么考试")),
            "private:222": _rows(("user", "考试周累死了")),
        },
    )
    r = history_search.search(_db(tmp_path), "考试")
    assert r["ok"] is True
    # 三行都含「考试」：111 里两行 + 222 里一行
    assert r["total"] == 3
    assert {h["channel"] for h in r["hits"]} == {"private:111", "private:222"}


def test_search_filters_by_person(tmp_path):
    _write(
        tmp_path,
        {
            "private:111": _rows(("user", "考试的事")),
            "private:222": _rows(("user", "考试的事")),
        },
    )
    r = history_search.search(_db(tmp_path), "考试", person="111")
    assert r["total"] == 1
    assert r["hits"][0]["channel"] == "private:111"


def test_search_returns_context_neighbors(tmp_path):
    _write(
        tmp_path,
        {
            "private:111": _rows(
                ("user", "前面一句"),
                ("user", "中间命中这句"),
                ("assistant", "后面一句"),
            )
        },
    )
    r = history_search.search(_db(tmp_path), "命中")
    hit = r["hits"][0]
    assert hit["before"] == "前面一句"
    assert hit["after"] == "后面一句"


def test_search_no_result_is_ok(tmp_path):
    _write(tmp_path, {"private:111": _rows(("user", "随便说说"))})
    r = history_search.search(_db(tmp_path), "完全不存在的词")
    assert r["ok"] is True
    assert r["hits"] == []
    assert "没搜到" in r["summary"]


def test_search_empty_query_rejected(tmp_path):
    r = history_search.search(_db(tmp_path), "   ")
    assert r["ok"] is False


def test_search_missing_history_file(tmp_path):
    r = history_search.search(_db(tmp_path), "考试")
    assert r["ok"] is True
    assert r["hits"] == []


def test_search_sorted_newest_first(tmp_path):
    _write(
        tmp_path,
        {
            "private:111": [
                {"role": "user", "content": "考试旧", "ts": 100.0},
                {"role": "user", "content": "考试新", "ts": 200.0},
            ]
        },
    )
    r = history_search.search(_db(tmp_path), "考试")
    assert [h["content"] for h in r["hits"]] == ["考试新", "考试旧"]


def test_search_limit_capped(tmp_path):
    _write(tmp_path, {"private:111": _rows(*[("user", f"关键词 {i}") for i in range(50)])})
    r = history_search.search(_db(tmp_path), "关键词", limit=100)
    assert len(r["hits"]) <= 30  # 上限 30


def test_recent_returns_tail(tmp_path):
    _write(
        tmp_path,
        {"private:111": _rows(*[("user", f"第{i}句") for i in range(20)])},
    )
    r = history_search.recent(_db(tmp_path), "private:111", turns=5)
    assert r["ok"] is True
    assert r["count"] == 5
    assert r["total"] == 20
    assert "第19句" in r["summary"]
    assert "第0句" not in r["summary"]


def test_recent_accepts_bare_uid(tmp_path):
    _write(tmp_path, {"private:111": _rows(("user", "没有前缀也能查"))})
    r = history_search.recent(_db(tmp_path), "111", turns=5)
    assert r["count"] == 1


def test_recent_unknown_channel(tmp_path):
    _write(tmp_path, {"private:111": _rows(("user", "x"))})
    r = history_search.recent(_db(tmp_path), "private:999")
    assert r["ok"] is True
    assert r["turns"] == []


def test_fmt_time_handles_bad_values():
    assert history_search._fmt_time(0) == ""
    assert history_search._fmt_time(None) == ""
    assert history_search._fmt_time("abc") == ""
    assert history_search._fmt_time(1_700_000_000.0) != ""
