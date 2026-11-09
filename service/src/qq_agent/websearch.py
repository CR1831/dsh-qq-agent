"""联网查询（Web 搜索）。

给 agent 补一个"查实时信息"的工具：赛程、天气、新闻、价格这类东西，
本地 B 站索引里没有，只能上网查。

默认走 Bing 中国站（国内可直连，中文结果好），失败时降级到 DuckDuckGo Lite。
两个源都是解析 HTML，不需要 API key；解析失败返回空列表而不是抛错，
由调用方决定要不要提示"查不到"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from .config import WebSearchConfig
from .net import sanitize_proxy_env

sanitize_proxy_env()

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class WebSearchError(RuntimeError):
    pass


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str = ""

    def as_payload(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def parse_bing(html: str, limit: int = 5) -> list[WebResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[WebResult] = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if link is None:
            continue
        href = str(link.get("href") or "")
        title = " ".join(link.get_text(" ", strip=True).split())
        if not href.startswith("http") or not title:
            continue
        caption = block.select_one(".b_caption p") or block.select_one("p")
        snippet = " ".join(caption.get_text(" ", strip=True).split()) if caption else ""
        out.append(WebResult(title=title, url=href, snippet=snippet))
        if len(out) >= limit:
            break
    return out


def parse_ddg_lite(html: str, limit: int = 5) -> list[WebResult]:
    soup = BeautifulSoup(html, "lxml")
    out: list[WebResult] = []
    for link in soup.select("a.result-link"):
        href = str(link.get("href") or "")
        title = " ".join(link.get_text(" ", strip=True).split())
        if not href.startswith("http") or not title:
            continue
        snippet = ""
        row = link.find_parent("tr")
        if row is not None:
            cell = row.find_next_sibling("tr")
            if cell is not None:
                snippet = " ".join(cell.get_text(" ", strip=True).split())
        out.append(WebResult(title=title, url=href, snippet=snippet))
        if len(out) >= limit:
            break
    return out


class WebSearch:
    def __init__(self, cfg: WebSearchConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            timeout=cfg.timeout,
            headers={
                "User-Agent": cfg.user_agent or DEFAULT_UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(self, query: str, limit: int | None = None) -> list[WebResult]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit or self.cfg.max_results), 10))
        errors: list[str] = []
        for provider in self._providers():
            try:
                if provider == "bing":
                    results = await self._bing(query, limit)
                else:
                    results = await self._ddg(query, limit)
            except Exception as exc:  # 换下一个源
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
                continue
            if results:
                return results
            errors.append(f"{provider}: 0 条")
        if errors and self.cfg.raise_on_empty:
            raise WebSearchError("；".join(errors))
        return []

    def _providers(self) -> list[str]:
        provider = (self.cfg.provider or "bing").strip().lower()
        if provider == "duckduckgo":
            return ["duckduckgo", "bing"]
        return ["bing", "duckduckgo"]

    async def _bing(self, query: str, limit: int) -> list[WebResult]:
        url = f"https://cn.bing.com/search?q={quote(query)}&setlang=zh-CN"
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            raise WebSearchError(f"HTTP {resp.status_code}")
        return parse_bing(resp.text, limit)

    async def _ddg(self, query: str, limit: int) -> list[WebResult]:
        url = f"https://lite.duckduckgo.com/lite/?q={quote(query)}"
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            raise WebSearchError(f"HTTP {resp.status_code}")
        return parse_ddg_lite(resp.text, limit)


def build_search(cfg: WebSearchConfig) -> WebSearch | None:
    return WebSearch(cfg) if cfg.enabled else None


__all__ = [
    "WebSearch",
    "WebSearchError",
    "WebResult",
    "parse_bing",
    "parse_ddg_lite",
    "build_search",
]
