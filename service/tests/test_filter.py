import time

import pytest

from qq_agent.models import Doc
from qq_agent.retrieval.filter import FilterCriteria, filter_docs


def doc(text: str, *, kind: str = "message", platform: str = "qq", author: str = "u1", ts: float | None = None, title: str = ""):
    return Doc(
        doc_id=Doc.make_id(platform, kind, f"{author}:{text[:12]}"),
        platform=platform,
        kind=kind,
        text=text,
        title=title,
        author_id=author,
        author_name=author,
        ts=ts if ts is not None else time.time(),
    )


@pytest.mark.asyncio
async def test_exclude_keywords_dropped_with_reason():
    docs = [
        doc("纳塔地图确实比须弥大，跑图累"),
        doc("【抽奖】关注送 648 原石，点击链接领取"),
    ]
    result = await filter_docs(docs, FilterCriteria(query="纳塔", exclude_any=["抽奖", "点击链接"]))
    assert len(result.kept) == 1
    assert any(d.stage == "keyword" for d in result.dropped)


@pytest.mark.asyncio
async def test_near_duplicate_of_short_message():
    docs = [
        doc("原神 5.0 的纳塔地图真的比须弥大吗？我跑图跑到吐", author="a"),
        doc("原神 5.0 纳塔地图真的比须弥大吗？我跑图跑到吐", author="b"),
    ]
    result = await filter_docs(docs, FilterCriteria(query="纳塔"))
    assert len(result.kept) == 1
    assert any(d.stage == "near_duplicate" for d in result.dropped)


@pytest.mark.asyncio
async def test_since_filter_and_short_text():
    now = time.time()
    docs = [doc("很久以前聊过纳塔地图", ts=now - 86400 * 10), doc("刚聊到纳塔地图", ts=now)]
    result = await filter_docs(docs, FilterCriteria(query="纳塔", since=now - 86400))
    assert len(result.kept) == 1
    assert any(d.stage == "time" for d in result.dropped)


@pytest.mark.asyncio
async def test_top_k_and_author_cap():
    docs = [doc(f"纳塔地图讨论第 {i} 条，内容各不相同 {i * 7}", author=f"u{i % 4}") for i in range(12)]
    result = await filter_docs(docs, FilterCriteria(query="纳塔", top_k=3, per_author_cap=1))
    assert len(result.kept) == 3
    assert len({d.author_id for d in result.kept}) == 3


@pytest.mark.asyncio
async def test_llm_judge_can_reject():
    docs = [doc("纳塔地图实测面积 1.4 倍", author="a"), doc("今天天气不错", author="b")]

    async def judge(query, batch):
        return {d.doc_id: (0.9 if "地图" in d.text else 0.1, "不相关") for d in batch}

    result = await filter_docs(
        docs,
        FilterCriteria(query="纳塔", use_llm_judge=True, llm_min_score=0.5),
        judge=judge,
    )
    assert len(result.kept) == 1
    assert any(d.stage == "llm" for d in result.dropped)


@pytest.mark.asyncio
async def test_judge_failure_degrades_gracefully():
    docs = [doc("纳塔地图实测面积 1.4 倍", author="a")]

    async def judge(query, batch):
        raise RuntimeError("provider down")

    result = await filter_docs(docs, FilterCriteria(query="纳塔", use_llm_judge=True), judge=judge)
    assert len(result.kept) == 1
    assert result.stats.get("llm_error") == 1


@pytest.mark.asyncio
async def test_report_is_explainable():
    docs = [doc("纳塔地图实测"), doc("【广告】加微信领原石")]
    result = await filter_docs(docs, FilterCriteria(query="纳塔", exclude_any=["广告"]))
    assert "保留" in result.summary()
    assert "过滤" in result.summary()
    payload = result.as_payload()
    assert payload["stats"]["kept"] == 1
