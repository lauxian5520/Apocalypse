"""Adapter for providers speaking the OpenAI `/chat/completions` shape.

Covers DeepSeek, Zhipu (GLM), OpenAI and any custom endpoint. Ollama's
`/api/chat` is a different protocol with a different tool vocabulary and is
rejected up front rather than half-supported.

The defensive parsing here is inherited from `services/ai_service.py`, which
learned it the hard way; see the comments on each guard.
"""
import json
import logging
from typing import AsyncIterator

import httpx

from core.errors import ConfigurationError, UpstreamError
from core.providers import FORMAT_OLLAMA, auth_headers
from harness.llm.base import LLMDelta, LLMResult, LLMToolCall, LLMUsage

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 120
STREAM_TIMEOUT_SECONDS = 300
DEFAULT_MAX_TOKENS = 4096
TEMPERATURE = 0.3          # lower than the chat widget: tool arguments must be exact


class OpenAICompatibleAdapter:
    def __init__(self, cfg: dict, model: str = ""):
        if cfg["format"] == FORMAT_OLLAMA:
            raise ConfigurationError(
                "Harness 需要 OpenAI 兼容的工具调用接口，Ollama 的 /api/chat 暂不支持；"
                "请把 AI_PROVIDER 换成 deepseek / zhipu / openai / custom"
            )
        self._cfg = cfg
        self.model = model or cfg["model"]

    # ── streaming ────────────────────────────────────────────────

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        payload = self._payload(messages, tools, stream=True)
        # Ask for a usage block on the terminal chunk. Providers that do not
        # know the option ignore it; we fall back to zeros either way.
        payload["stream_options"] = {"include_usage": True}

        acc = _ToolCallAccumulator()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage = LLMUsage(model=self.model)
        finish_reason = ""

        try:
            async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SECONDS) as client:
                async with client.stream(
                    "POST", self._cfg["url"], headers=auth_headers(self._cfg), json=payload
                ) as resp:
                    await _raise_for_stream_status(resp, self._cfg["url"])

                    async for line in resp.aiter_lines():
                        chunk = _parse_sse_line(line)
                        if chunk is None:
                            continue

                        if chunk.get("usage"):
                            usage = _read_usage(chunk["usage"], self.model)

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}

                        text = delta.get("content") or ""
                        thought = delta.get("reasoning_content") or ""
                        if delta.get("tool_calls"):
                            acc.feed(delta["tool_calls"])
                        if text:
                            content_parts.append(text)
                        if thought:
                            reasoning_parts.append(thought)
                        if text or thought:
                            yield LLMDelta(content=text, reasoning=thought)
        except UpstreamError:
            raise
        except Exception as e:
            raise UpstreamError(f"AI API Error ({self._cfg['url']}): {_error_text(e)}")

        yield LLMDelta(result=LLMResult(
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=acc.finish(),
            usage=usage,
            finish_reason=finish_reason,
        ))

    # ── single shot ──────────────────────────────────────────────

    async def complete(self, messages: list[dict], max_tokens: int = 0) -> LLMResult:
        payload = self._payload(messages, None, stream=False)
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # The client is built inside the try because constructing it can fail
        # too (a broken proxy env var), and that used to escape as a raw 500.
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    self._cfg["url"], headers=auth_headers(self._cfg), json=payload
                )
                resp.raise_for_status()
                data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason") or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise UpstreamError(f"AI 返回格式无法解析 ({self._cfg['url']}): {e}")
        except Exception as e:
            raise UpstreamError(f"AI API Error ({self._cfg['url']}): {_error_text(e)}")

        # A thinking model spends its output budget on reasoning first, so too
        # small a cap returns nothing at all. Say so instead of handing back an
        # empty string the caller will quietly treat as a valid answer.
        if not content and finish_reason == "length":
            raise UpstreamError(
                f"模型在 max_tokens={max_tokens or DEFAULT_MAX_TOKENS} 内只产出了推理过程，"
                f"没有正文；{self.model} 需要更大的输出预算"
            )

        return LLMResult(
            content=content,
            reasoning=message.get("reasoning_content") or "",
            usage=_read_usage(data.get("usage") or {}, self.model),
            finish_reason=finish_reason,
        )

    def _payload(self, messages: list[dict], tools: list[dict] | None, stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "temperature": TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload


class _ToolCallAccumulator:
    """Reassembles tool calls that arrive split across stream chunks.

    The provider sends `function.arguments` a few characters at a time and only
    names the call once, on its first fragment. Everything is keyed by `index`,
    which is the only field present on every fragment.
    """

    def __init__(self):
        self._by_index: dict[int, LLMToolCall] = {}
        self._order: list[int] = []

    def feed(self, fragments: list[dict]) -> None:
        for frag in fragments:
            index = frag.get("index", 0)
            if index not in self._by_index:
                self._by_index[index] = LLMToolCall(id="", name="")
                self._order.append(index)
            call = self._by_index[index]

            if frag.get("id"):
                call.id = frag["id"]
            fn = frag.get("function") or {}
            if fn.get("name"):
                call.name = fn["name"]
            if fn.get("arguments"):
                call.arguments += fn["arguments"]

    def finish(self) -> list[LLMToolCall]:
        calls = [self._by_index[i] for i in self._order]
        for n, call in enumerate(calls):
            # A provider may omit the id entirely; the loop needs one to pair
            # the result back, so mint a deterministic stand-in.
            if not call.id:
                call.id = f"call_{n}"
        return [c for c in calls if c.name]


def _parse_sse_line(line: str) -> dict | None:
    """One SSE line to a JSON object, or None when it carries no payload."""
    line = line.strip()
    if not line or line.startswith(":"):      # blank or keep-alive comment
        return None
    # Some providers omit the space after "data:", so don't hard-code "data: ".
    if line.startswith("data:"):
        line = line[5:].lstrip()
    if not line or line == "[DONE]":
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _read_usage(raw: dict, model: str) -> LLMUsage:
    # DeepSeek reports cache hits under its own key; OpenAI nests them in
    # prompt_tokens_details. Read whichever is present.
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return LLMUsage(
        model=model,
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(cached or 0),
    )


async def _raise_for_stream_status(resp: httpx.Response, url: str) -> None:
    """raise_for_status() on a streamed response hides the provider's body.

    Read it explicitly first so the user sees "invalid api key" instead of a
    bare "Client error '401 Unauthorized'".
    """
    if resp.status_code < 400:
        return
    try:
        body = (await resp.aread()).decode("utf-8", "replace")
    except Exception:
        body = ""
    detail = f"HTTP {resp.status_code}"
    if body:
        detail += f": {body[:500]}"
    raise UpstreamError(f"AI API Error ({url}): {detail}")


def _error_text(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.text
        except Exception:
            body = ""
        if body:
            return f"HTTP {resp.status_code}: {body[:500]}"
        return f"HTTP {resp.status_code}"
    return str(exc) or exc.__class__.__name__
