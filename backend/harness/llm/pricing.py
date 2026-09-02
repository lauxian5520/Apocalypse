"""Cost estimation from `llm/usage` events.

The rate table is a data file. An unknown model yields `None`, which the UI
renders as "—": a blank is honest, an invented number is not.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from harness.llm.base import LLMUsage

logger = logging.getLogger(__name__)

PRICING_FILE = Path(__file__).resolve().parent.parent / "data" / "pricing.json"
PER_MILLION = 1_000_000


@lru_cache()
def _table() -> dict:
    try:
        with open(PRICING_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("models", {})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[harness] pricing table unreadable (%s); costs will show as unknown", e)
        return {}


def estimate_cost(usage: LLMUsage) -> float | None:
    """USD for one request, or None when the model has no published rate here."""
    rates = _table().get(usage.model)
    if not rates:
        return None
    return (
        usage.cached_tokens * rates.get("cache_hit", 0.0)
        + usage.uncached_prompt_tokens * rates.get("input", 0.0)
        + usage.completion_tokens * rates.get("output", 0.0)
    ) / PER_MILLION


def summarize(usage_events: list[dict]) -> dict:
    """Roll `llm/usage` event payloads up into a session total."""
    prompt = completion = cached = 0
    cost: float | None = 0.0

    for raw in usage_events:
        usage = LLMUsage(
            model=raw.get("model", ""),
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            cached_tokens=int(raw.get("cached_tokens") or 0),
        )
        prompt += usage.prompt_tokens
        completion += usage.completion_tokens
        cached += usage.cached_tokens

        one = estimate_cost(usage)
        # One unpriced request makes the whole total unknowable. Say so rather
        # than quietly reporting a partial sum as if it were complete.
        if one is None:
            cost = None
        elif cost is not None:
            cost += one

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "total_tokens": prompt + completion,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "requests": len(usage_events),
    }
