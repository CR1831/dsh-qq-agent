import time

from qq_agent.models import Doc
from qq_agent.store import Store, highlight, segment


def make(text: str, *, platform: str = "qq", channel: str = "group:1", ts: float | None = None, author: str = "u1"):
    return Doc.message(
        platform,
        channel,
        author,
        text,
        ts=1_700_000_000.0 if ts is None else ts,
        channel_name="测试群",
    )


def test_upsert_is_idempotent():
    store = Store(":memory:")
    docs = [make("原神 5.0 纳塔地图很大"), make("原神 5.0 纳塔地图很大")]
    assert store.upsert(docs) == 1
    assert store.upsert(docs) == 0
    assert store.count() == 1


def test_chinese_two_character_query():
    store = Store(":memory:")
    store.upsert(
        [
            make("原神的须弥地图真的大"),
            make("今天中午吃什么"),
            Doc(platform="bilibili", kind="video", doc_id="b:1", title="纳塔评测", text="纳塔地图对比须弥"),
        ]
    )
    rows = store.search("原神")
    assert rows, "两字中文查询必须能命中"
    assert "原神" in rows[0][0].text

    rows = store.search("纳塔 须弥")
    assert rows
    assert {r[0].platform for r in rows} == {"qq", "bilibili"}


def test_platform_filter_and_recent():
    store = Store(":memory:")
    store.upsert(
        [
            make("共同话题 原神", platform="qq"),
            Doc(platform="bilibili", kind="video", doc_id="b:1", title="共同话题 原神", text="视频简介"),
            make("较早的消息", ts=time.time() - 86400 * 3),
        ]
    )
    assert len(store.search("原神", platform="qq")) == 1
    assert len(store.search("原神", platform="bilibili")) == 1
    recent = store.recent(limit=2)
    assert len(recent) == 2 and recent[0].ts >= recent[1].ts


def test_highlight_returns_context():
    text = "前面一堆废话" * 20 + "纳塔地图实测" + "后面一堆废话" * 20
    snippet = highlight(text, "纳塔地图", width=60)
    assert "纳塔地图" in snippet
    assert len(snippet) <= 70


def test_segment_splits_cjk():
    assert segment("原神") == "原神"
    assert "纳塔" in segment("纳塔地图")


def test_stats_and_channels():
    store = Store(":memory:")
    store.upsert([make("a b c", channel="group:1"), make("d e f", channel="group:2")])
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["by_platform"]["qq"] == 2
    channels = store.channels("qq")
    assert {c["channel_id"] for c in channels} == {"group:1", "group:2"}
