"""Minimal OpenAI-compatible chat client (DeepSeek / OpenAI / vLLM / Ollama).

Only two things are needed from the provider: tool calling and JSON output, so
a hand-rolled httpx client keeps the dependency surface at exactly one package.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

import httpx

from .config import AgentConfig
from .models import Doc
from .net import sanitize_proxy_env
from .retrieval.text import excerpt

sanitize_proxy_env()


class LLMError(RuntimeError):
    pass


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    """Best-effort extraction of the first JSON object in a model reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return {}


class LLM:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.cfg.api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.cfg.timeout, connect=10.0),
                headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise LLMError("未配置 API key")
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": list(messages),
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        # 透传厂商私有参数（例如 deepseek-flash 的 {"thinking": {"type": "disabled"}}）
        if extra:
            payload.update(extra)
        try:
            resp = await self._http().post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"请求模型失败：{exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"模型返回 {resp.status_code}：{resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"模型响应格式异常：{data}") from exc

    # ------------------------------------------------------------- judge
    async def judge(self, query: str, docs: Sequence[Doc], *, threshold_note: str = "") -> dict[str, tuple[float, str]]:
        """Score each document's usefulness for ``query`` on 0..1."""
        if not docs:
            return {}
        lines = []
        for doc in docs:
            lines.append(
                json.dumps(
                    {
                        "id": doc.doc_id,
                        "platform": doc.platform,
                        "kind": doc.kind,
                        "author": doc.author_name,
                        "time": doc.when,
                        "content": excerpt(f"{doc.title}\n{doc.text}", 300),
                    },
                    ensure_ascii=False,
                )
            )
        prompt = (
            "你是信息筛选器。判断每条内容对用户查询是否真正有用，并给出 0 到 1 的相关度。\n"
            "标准：直接回答问题或提供关键事实=0.8-1.0；同主题但信息量低=0.4-0.7；"
            "广告、灌水、纯情绪、答非所问=0-0.3。\n"
            f"查询：{query}\n{threshold_note}\n"
            "只输出 JSON：{\"verdicts\":[{\"id\":\"...\",\"score\":0.0,\"reason\":\"不超过20字\"}]}\n"
            "每条都要给分，id 必须原样返回。\n\n待评估内容：\n" + "\n".join(lines)
        )
        message = await self.chat(
            [
                {"role": "system", "content": "你只输出严格 JSON，不写解释。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        data = parse_json_object(message.get("content") or "")
        out: dict[str, tuple[float, str]] = {}
        for item in data.get("verdicts", []) or []:
            try:
                doc_id = str(item.get("id"))
                score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            except (TypeError, ValueError):
                continue
            out[doc_id] = (score, str(item.get("reason", ""))[:60])
        return out


__all__ = ["LLM", "LLMError", "parse_json_object"]
