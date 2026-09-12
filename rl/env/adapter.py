"""The evaluation-time policy: a `ModelAdapter` built for rollouts, not chat.

This is the second seam the harness's Protocol design pays off on. The shipped
`OpenAICompatibleAdapter` is right for a website and wrong here in three ways:
`TEMPERATURE` is hard-coded to 0.3, there is no `seed`, and it streams.

**Not streaming is the interesting one.** `ModelAdapter.stream()` is only
required to be an async iterator ending in a delta that carries the assembled
`LLMResult`. Yielding exactly one such delta is legal, and reading
`harness/loop/agent.py:169-176` shows what the loop then does: the first delta
has `result is not None`, so the chunk buffer drains empty and it returns
immediately. The trajectory contains **zero `assistant/chunk` events** and the
loop makes no per-token generator hops. A rollout wants the finished turn, not
a typewriter, and the log stays small enough that re-reading it once per step —
which the loop does — costs nothing.

The same adapter serves any OpenAI-compatible endpoint, so the API baselines and
a local vLLM server run through one code path. Token-exact training generation
is a *different* adapter (`policy_adapter.py`, Phase 4) because it must control
tokenization end to end; this one is for evaluation, where the provider's own
template is what you want.
"""
import json
import logging
from typing import AsyncIterator

import httpx

from harness.llm.base import LLMDelta, LLMResult, LLMToolCall, LLMUsage

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_TOKENS = 2048
MAX_RETRIES = 3


class EvalAdapter:
    """One configured endpoint, called without streaming.

    `sampling` is passed through to the provider verbatim, so a caller can set
    `temperature`, `top_p`, `seed` or anything else the endpoint understands
    without this class growing a parameter for each.
    """

    def __init__(
        self,
        *,
        url: str,
        model: str,
        api_key: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        **sampling,
    ):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.sampling = sampling
        # Wall-clock spent inside the provider, so the rollout engine can report
        # the generation/environment split without a hook point.
        self.generation_seconds = 0.0
        self.requests = 0

    @classmethod
    def from_settings(cls, model: str = "", **sampling) -> "EvalAdapter":
        """Use whatever `AI_PROVIDER` resolves to — the API-baseline path."""
        from core.providers import provider_config, require_configured

        cfg = provider_config()
        require_configured(cfg)
        return cls(
            url=cfg["url"],
            model=model or cfg["model"],
            api_key=cfg.get("key", ""),
            **sampling,
        )

    @classmethod
    def for_vllm(cls, base_url: str, model: str, **sampling) -> "EvalAdapter":
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return cls(url=f"{base}/chat/completions", model=model, api_key="EMPTY", **sampling)

    # ── ModelAdapter protocol ─────────────────────────────────────
    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        """One turn, delivered as a single final delta. See the module docstring."""
        result = await self._request(messages, tools)
        yield LLMDelta(result=result)

    async def complete(self, messages: list[dict], max_tokens: int = 0) -> LLMResult:
        return await self._request(messages, None, max_tokens=max_tokens or self.max_tokens)

    # ── implementation ────────────────────────────────────────────
    async def _request(
        self, messages: list[dict], tools: list[dict] | None, max_tokens: int = 0
    ) -> LLMResult:
        import time

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens or self.max_tokens,
            **self.sampling,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started = time.monotonic()
        try:
            body = await self._post(payload, headers)
        finally:
            self.generation_seconds += time.monotonic() - started
            self.requests += 1

        return self._assemble(body)

    async def _post(self, payload: dict, headers: dict) -> dict:
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.url, json=payload, headers=headers)
                    if resp.status_code >= 400:
                        detail = resp.text[:300]
                        # A 4xx other than 429 will fail identically forever;
                        # retrying it just hides the real error behind a timeout.
                        if resp.status_code < 500 and resp.status_code != 429:
                            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
                        last = RuntimeError(f"HTTP {resp.status_code}: {detail}")
                    else:
                        return resp.json()
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                last = e
            import asyncio
            await asyncio.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"provider 不可达，重试 {MAX_RETRIES} 次后放弃：{last}")

    def _assemble(self, body: dict) -> LLMResult:
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}

        tool_calls = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function") or {}
            arguments = fn.get("arguments", "")
            if isinstance(arguments, dict):
                # Some servers helpfully pre-parse it. The loop and the log both
                # expect the raw JSON text the model wrote, so put it back.
                arguments = json.dumps(arguments, ensure_ascii=False)
            tool_calls.append(LLMToolCall(
                id=raw.get("id") or f"call_{i}",
                name=fn.get("name", ""),
                arguments=arguments or "",
            ))

        raw_usage = body.get("usage") or {}
        cached = raw_usage.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)

        return LLMResult(
            content=message.get("content") or "",
            reasoning=message.get("reasoning_content") or "",
            tool_calls=[c for c in tool_calls if c.name],
            usage=LLMUsage(
                model=body.get("model") or self.model,
                prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                cached_tokens=int(cached or 0),
            ),
            finish_reason=choice.get("finish_reason") or "",
        )
