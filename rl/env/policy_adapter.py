"""The training-time policy: a `ModelAdapter` that owns its own tokenization.

`adapter.py` is for evaluation and lets the server do the templating. This one
cannot, and the reason is the whole point of the module.

If rollout goes through `/v1/chat/completions`, vLLM applies the chat template
server-side — including its own normalisation of tool-call arguments, which
changes between releases. The trainer then re-applies a template locally to
compute the loss. Those two renderings must agree token for token, and there is
no way to check that they do; they simply differ sometimes, and when they do the
gradient is computed against text the policy never emitted. No error, no
warning. (`rl/env/template.py` documents the specific `| tojson` case that bites.)

So the server is taken out of the templating business entirely:

1. `derive_messages()` builds the message list — still the single projection.
2. `template.encode()` applies the **pinned** template locally, producing
   `prompt_token_ids`.
3. Those integers are POSTed to **`/v1/completions`**, which does no templating.
4. `logprobs` comes back with the sampled token ids and their log-probabilities,
   so `logp_old` is recorded at the moment of sampling rather than recomputed
   later (recomputing gives the *current* policy's numbers, making the
   importance ratio identically 1 and the clipping silently dead).
5. The tool call is parsed out of the raw text here, because nothing else did.

The mismatch becomes structurally impossible: one tokenizer, one template, one
code path, both sides.

Not tested against a live server in this repository — there is no GPU here. What
*is* tested offline is everything that can be: the template application, the
tool-call parser, and the round-trip that the recorded ids re-decode to the text
the server returned.
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from harness.llm.base import LLMDelta, LLMResult, LLMToolCall, LLMUsage

from rl.env import template

logger = logging.getLogger(__name__)

# Qwen2.5 emits calls as <tool_call>\n{"name": …, "arguments": {…}}\n</tool_call>,
# one block per call. Non-greedy so several in one message parse separately.
TOOL_CALL_BLOCK = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_TOKENS = 1024


@dataclass
class SampledStep:
    """What the trainer needs about one generation, recorded as it happened."""

    prompt_token_ids: list[int] = field(default_factory=list)
    completion_token_ids: list[int] = field(default_factory=list)
    logp_old: list[float] = field(default_factory=list)
    text: str = ""
    finish_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "prompt_token_ids": self.prompt_token_ids,
            "completion_token_ids": self.completion_token_ids,
            "logp_old": self.logp_old,
            "text": self.text,
            "finish_reason": self.finish_reason,
        }


def parse_tool_calls(text: str) -> tuple[str, list[LLMToolCall]]:
    """Split raw generated text into visible content and tool calls.

    Returns `arguments` as a JSON **string**, matching what
    `OpenAICompatibleAdapter` produces and what the event log stores — so the
    rest of the harness cannot tell which adapter ran. `template.normalize()`
    converts it back to an object at tokenisation time; keeping the two
    representations in their established places is what lets a trajectory
    collected here be replayed by anything else.
    """
    calls: list[LLMToolCall] = []
    for index, match in enumerate(TOOL_CALL_BLOCK.finditer(text)):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            # The model wrote a malformed call. Keep it: the registry will
            # reject it and the model will see the error, which is real
            # behaviour the trajectory should contain.
            logger.debug("[policy] unparseable tool_call block: %s", match.group(1)[:120])
            continue
        name = payload.get("name")
        if not name:
            continue
        arguments = payload.get("arguments", {})
        calls.append(LLMToolCall(
            id=f"call_{index}_{uuid.uuid4().hex[:8]}",
            name=str(name),
            arguments=json.dumps(arguments, ensure_ascii=False)
            if not isinstance(arguments, str) else arguments,
        ))

    content = TOOL_CALL_BLOCK.sub("", text).strip()
    return content, calls


class PolicyAdapter:
    """A vLLM-backed policy that controls its own tokenization."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 1.0,
        top_p: float = 1.0,
        seed: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        self.url = f"{base}/completions"
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed
        self.timeout = timeout

        # One entry per generation, in order. The rollout engine harvests these
        # after a turn and stores them beside the trajectory.
        self.steps: list[SampledStep] = []
        self.generation_seconds = 0.0

    # ── ModelAdapter protocol ─────────────────────────────────────
    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        yield LLMDelta(result=await self._generate(messages, tools))

    async def complete(self, messages: list[dict], max_tokens: int = 0) -> LLMResult:
        return await self._generate(messages, None, max_tokens=max_tokens)

    # ── implementation ────────────────────────────────────────────
    async def _generate(
        self, messages: list[dict], tools: list[dict] | None, max_tokens: int = 0
    ) -> LLMResult:
        import time

        prompt_ids = template.encode(messages, tools, add_generation_prompt=True)

        payload = {
            "model": self.model,
            "prompt": prompt_ids,           # token ids: the server templates nothing
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "logprobs": 0,                  # echo each sampled token's log-prob
            "skip_special_tokens": False,   # <|im_end|> must survive into the text
        }
        if self.seed is not None:
            payload["seed"] = self.seed

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.url, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"vLLM HTTP {response.status_code}: {response.text[:300]}")
            body = response.json()
        self.generation_seconds += time.monotonic() - started

        choice = (body.get("choices") or [{}])[0]
        text = choice.get("text") or ""
        step = SampledStep(
            prompt_token_ids=prompt_ids,
            completion_token_ids=_completion_ids(choice),
            logp_old=_completion_logprobs(choice),
            text=text,
            finish_reason=choice.get("finish_reason") or "",
        )
        self._verify(step)
        self.steps.append(step)

        content, calls = parse_tool_calls(text)
        usage = body.get("usage") or {}
        return LLMResult(
            content=content,
            tool_calls=calls,
            usage=LLMUsage(
                model=body.get("model") or self.model,
                prompt_tokens=int(usage.get("prompt_tokens") or len(prompt_ids)),
                completion_tokens=int(usage.get("completion_tokens") or len(step.completion_token_ids)),
            ),
            finish_reason=step.finish_reason,
        )

    def _verify(self, step: SampledStep) -> None:
        """Catch a mismatch at the moment it happens, not at training time.

        Two things must hold, and both are cheap: one log-prob per sampled
        token, and the recorded ids decoding back to the text the server
        returned. A trajectory failing either is unusable for training, and it
        is far better to find out here — with the request still in hand — than
        in a loss curve three days later.
        """
        if step.logp_old and len(step.logp_old) != len(step.completion_token_ids):
            raise RuntimeError(
                f"logprobs 数量 {len(step.logp_old)} 与 completion token 数 "
                f"{len(step.completion_token_ids)} 不一致——无法对齐 logp_old"
            )
        if not step.completion_token_ids:
            return
        decoded = template.tokenizer().decode(
            step.completion_token_ids, skip_special_tokens=False)
        if decoded != step.text:
            raise RuntimeError(
                "记录的 token id 解码结果与服务端返回的文本不一致——"
                f"分词器或模板与服务端不同。\n  解码: {decoded[:160]!r}\n  返回: {step.text[:160]!r}"
            )

    def drain(self) -> list[SampledStep]:
        """Take the generations recorded so far and reset."""
        steps, self.steps = self.steps, []
        return steps


def _completion_ids(choice: dict) -> list[int]:
    logprobs = choice.get("logprobs") or {}
    ids = logprobs.get("token_ids")
    if ids:
        return [int(i) for i in ids]
    # Older vLLM builds omit `token_ids`; re-tokenising the returned text is the
    # fallback, and `_verify` still checks it round-trips.
    text = choice.get("text") or ""
    return template.tokenizer().encode(text, add_special_tokens=False) if text else []


def _completion_logprobs(choice: dict) -> list[float]:
    logprobs = choice.get("logprobs") or {}
    values = logprobs.get("token_logprobs") or []
    return [float(v) for v in values if v is not None]
