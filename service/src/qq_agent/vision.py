"""读图：让摘要能「看」封面。

B 站视频除了标题、简介、字幕，还有一张封面（接口里的 ``pic``）。封面上的大字
往往才是视频真正的标题，画面主体说明了内容类型，这些信息纯文本摘要拿不到。

三条现实约束决定了实现方式：

1. 目前 DeepSeek 只有 ``deepseek-flash`` 接受图片输入，``deepseek-chat`` 会直接
   报 400。所以视觉调用单开一套模型配置，不占用主摘要模型，主摘要质量不变。
2. ``deepseek-flash`` 默认开思考模式。图生文这种任务会把 token 全烧在思维链上
   （实测 max_tokens=220 时 completion 全是 reasoning，正文为空），所以这里显式
   关掉 thinking。
3. 图片按 token 计费（单张上限 1024）。所以优先用 B 站图床自带的缩略参数
   （``@480w_270h_1c.jpg``，约 12 KB）并设 ``detail=low``，一张图约 300 token。

图生文的文字会落盘缓存：同一张封面只花一次 token，重复观看不再计费。
所有失败路径都返回空字符串/None —— 一张图坏了不能把整份简报拖垮。
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .net import sanitize_proxy_env

sanitize_proxy_env()

# 图生文的提示词：只要看得见的东西，明确禁止推测和编造。
COVER_PROMPT = (
    "这是 B 站视频的封面图。用两三句中文客观描述三件事："
    "① 画面主体是什么；② 封面上的大字写了什么（通常是视频真标题，要照抄）；"
    "③ 有没有系列名、栏目名或期数标识。"
    "只描述图片里看得见的内容，不评价、不推测视频讲了什么、不补全看不清的字。"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_BILI_IMG = re.compile(r"^https?://[\w.-]*(?:hdslb|biliimg)\.com/", re.I)
_URL_SUFFIX = re.compile(r"@[^/?]*$")

# 图床各认各的 Referer：带错会被直接拒（QQ 图床见 B 站 Referer 就 400）
_REFERERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:hdslb|biliimg)\.com", re.I), "https://www.bilibili.com/"),
    (re.compile(r"(?:qq\.com|gtimg\.cn|qpic\.cn|tencent\.com)", re.I), "https://im.qq.com/"),
)


def _referer_for(url: str) -> str:
    for pattern, referer in _REFERERS:
        if pattern.search(url):
            return referer
    return ""


@dataclass
class VisionConfig:
    enabled: bool = False            # 公开版默认关：读图会把图片发给视觉模型
    model: str = "deepseek-flash"    # 目前唯一支持图片输入的模型
    base_url: str = ""               # 留空 = 用 [agent].base_url
    api_key: str = ""                # 留空 = 用 [agent].api_key
    detail: str = "low"              # low 会缩到 512×512，最省 token
    max_tokens: int = 320
    timeout: float = 25.0
    thumb: str = "@480w_270h_1c.jpg"  # B 站图床缩略参数，原图 240 KB → 12 KB
    max_bytes: int = 4_000_000        # 超过这个大小就放弃（防止下到奇怪的东西）
    cache_dir: str = "data/image_cache"
    cache_days: int = 14              # 缓存保留天数


def sniff_mime(data: bytes) -> str:
    """按文件头判断图片类型（模型也按内容判断，不信任扩展名）。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def to_data_url(data: bytes) -> str:
    """编码成 OpenAI 兼容的 data URL（deepseek-flash 支持 base64 内联）。"""
    return f"data:{sniff_mime(data)};base64," + base64.b64encode(data).decode("ascii")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:20]


class Vision:
    """图生文。``agent`` 是 AgentConfig，用来兜底 base_url / api_key。"""

    def __init__(self, cfg: VisionConfig, agent: Any, *, client: Any | None = None) -> None:
        self.cfg = cfg
        self.agent = agent
        self._client = client          # 测试可注入；生产用一次性 httpx 客户端

    # ------------------------------------------------------------- readiness
    @property
    def available(self) -> bool:
        return bool(self.cfg.enabled and self._key())

    def _key(self) -> str:
        return str(self.cfg.api_key or getattr(self.agent, "api_key", "") or "").strip()

    def _base(self) -> str:
        return str(
            self.cfg.base_url or getattr(self.agent, "base_url", "") or "https://api.deepseek.com/v1"
        ).rstrip("/")

    # ----------------------------------------------------------------- paths
    @property
    def root(self) -> Path:
        return Path(self.cfg.cache_dir)

    def _img_path(self, url: str) -> Path:
        return self.root / "img" / _sha1(url)

    def _desc_path(self, url: str, prompt: str = "") -> Path:
        # 缓存键必须带上提示词：同一张图用「封面」和「聊天图片」两种问法，
        # 描述不该互相串（曾经只按 URL 缓存，拿到过另一种问法的结果）
        key = _sha1(url + "\x00" + prompt) if prompt else _sha1(url)
        return self.root / "desc" / (key + ".txt")

    # ------------------------------------------------------------------ urls
    def thumb_url(self, url: str) -> str:
        """B 站图床加缩略参数；本地路径和 data URL 原样返回。

        原图动辄 240 KB，缩略后约 12 KB —— token 一样（都按尺寸算，上限 1024），
        但下载快一个量级，夜里跑简报不会卡在图片上。
        """
        text = str(url or "").strip()
        if not text:
            return ""
        if not text.startswith(("http://", "https://")):
            return text
        text = re.sub(r"^http://", "https://", text, flags=re.I)
        if self.cfg.thumb and _BILI_IMG.match(text):
            text = _URL_SUFFIX.sub("", text.rstrip("/")) + self.cfg.thumb
        return text

    # ----------------------------------------------------------------- fetch
    async def fetch(self, url: str) -> bytes | None:
        """抓图（带磁盘缓存）。任何失败都返回 None。"""
        target = self.thumb_url(url)
        if not target:
            return None
        if target.startswith("data:"):
            payload = target.split(",", 1)[-1]
            try:
                return base64.b64decode(payload)
            except Exception:
                return None
        if not target.startswith(("http://", "https://")):
            path = Path(target)
            try:
                return path.read_bytes() if path.is_file() else None
            except OSError:
                return None

        cached = self._img_path(target)
        if cached.is_file():
            try:
                return cached.read_bytes()
            except OSError:
                pass

        try:
            data = await self._download(target)
        except Exception:
            return None
        if not data or len(data) > self.cfg.max_bytes:
            return None
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        except OSError:
            pass                     # 缓存写不进去也要能继续用
        return data

    async def _download(self, url: str) -> bytes:
        # Referer 要跟着图床走：B 站图床认自己的，QQ 图床带着 B 站的会直接 400
        headers = {"User-Agent": USER_AGENT}
        referer = _referer_for(url)
        if referer:
            headers["Referer"] = referer
        if self._client is not None:
            resp = await self._client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(
                timeout=self.cfg.timeout, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content

    # -------------------------------------------------------------- describe
    async def describe(
        self,
        url: str,
        *,
        prompt: str = COVER_PROMPT,
        cache: bool = True,
        errors: list[str] | None = None,
    ) -> str:
        """看图说人话。失败返回空字符串（原因写进 ``errors`` 便于排查）。"""
        target = self.thumb_url(url)
        if not target:
            return ""
        desc = self._desc_path(target, prompt) if cache else None
        if desc is not None and desc.is_file():
            try:
                cached = desc.read_text(encoding="utf-8").strip()
            except OSError:
                cached = ""
            if cached:
                return cached

        data = await self.fetch(target)
        if not data:
            if errors is not None:
                errors.append("图片下载失败")
            return ""
        try:
            text = await self._ask(prompt, data)
        except Exception as exc:
            if errors is not None:
                errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            return ""
        text = " ".join(str(text or "").split())[:400]
        if text and desc is not None:
            try:
                desc.parent.mkdir(parents=True, exist_ok=True)
                desc.write_text(text, encoding="utf-8")
            except OSError:
                pass
        return text

    async def cover(self, url: str) -> str:
        """视频封面 → 一段描述（最常见的用法）。"""
        return await self.describe(url, prompt=COVER_PROMPT)

    async def _ask(self, prompt: str, data: bytes) -> str:
        if not self._key():
            return ""
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": to_data_url(data), "detail": self.cfg.detail},
                        },
                    ],
                }
            ],
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.2,
            "stream": False,
            # 关键：flash 默认开思考，图生文会把 token 全烧在思维链上
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"}
        url = f"{self._base()}/chat/completions"
        if self._client is not None:
            resp = await self._client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self.cfg.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(f"视觉模型返回 {resp.status_code}：{resp.text[:200]}")
        body = resp.json()
        message = (body.get("choices") or [{}])[0].get("message") or {}
        return str(message.get("content") or "")

    # ---------------------------------------------------------------- upkeep
    def purge_old(self) -> int:
        """删掉超过 cache_days 的图片与描述缓存，返回删除的文件数。"""
        cutoff = time.time() - max(1, int(self.cfg.cache_days)) * 86400
        removed = 0
        for folder in (self.root / "img", self.root / "desc"):
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed


__all__ = ["COVER_PROMPT", "Vision", "VisionConfig", "sniff_mime", "to_data_url"]
