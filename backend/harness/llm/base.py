"""The model-adapter seam.

One vocabulary for talking to a provider, so the agent loop never learns a
wire format. Swapping `OpenAICompatibleAdapter` for another implementation is
a one-line change in `harness/context.py`.
"""
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class LLMToolCall:
    """One assembled tool call. `arguments` is raw JSON text, as the model wrote it."""

    id: str
    name: str
    arguments: str = ""

    def to_wire(self) -> dict:
        """The shape that goes back into an assistant message."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class LLMUsage:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0          # prompt tokens served from the provider's cache

    @property
    def uncached_prompt_tokens(self) -> int:
        return max(0, self.prompt_tokens - self.cached_tokens)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass
class LLMResult:
    """The finished assistant turn, assembled from the stream."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = ""


@dataclass
class LLMDelta:
    """One streamed fragment.

    Every delta but the last carries incremental text. The final delta carries
    `result` instead — one generator, so callers cannot forget to collect the
    assembled turn.
    """

    content: str = ""
    reasoning: str = ""
    result: LLMResult | None = None


@runtime_checkable
class ModelAdapter(Protocol):
    def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        """Stream one assistant turn, ending with a delta that carries the result."""
        ...

    async def complete(self, messages: list[dict], max_tokens: int = 0) -> LLMResult:
        """Non-streaming single shot — used for titles and compaction summaries."""
        ...
