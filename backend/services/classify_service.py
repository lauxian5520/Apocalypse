"""AI categorisation of feed items, with an on-disk cache.

Lifted out of the feeds router: deciding when a cached classification is still
valid, and what to fall back to when the model is unavailable, is business
logic, not request handling.
"""
import json
import logging
import os

from services import ai_service, feed_service

logger = logging.getLogger(__name__)

# Feeds whose freshness is tracked by a separate source file.
_SOURCE_FILES = {
    ("github", "daily"): "github_daily",
    ("github", "weekly"): "github_weekly",
    ("github", "monthly"): "github_monthly",
    ("huggingface", None): "huggingface",
}


def _cache_path(source: str, period: str | None) -> str:
    suffix = f"_{period}" if period else ""
    return feed_service.feed_path(f"classified_{source}{suffix}")


def _source_saved_at(source: str, period: str | None) -> str:
    name = _SOURCE_FILES.get((source, period))
    if not name:
        return ""
    path = feed_service.feed_path(name)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    return str(data.get("updated_at") or data.get("savedAt") or "")


def _unwrap_items(payload) -> tuple[list[dict], str]:
    """Accept a scraper payload as a bare list or an {updated_at, data} envelope."""
    if isinstance(payload, list):
        return payload, ""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data, str(payload.get("updated_at") or "")
    return [], ""


def _fallback(items: list[dict]) -> dict:
    return {"categories": [{"name": "其他", "items": list(range(1, len(items) + 1))}]}


async def classify(source: str, payload, period: str | None = None) -> dict:
    """Return categorised items, reusing the cache when the source is unchanged.

    The cache key is (source file timestamp, model name), so a new scrape or a
    provider switch invalidates it while repeated page loads do not re-bill the
    AI provider.
    """
    items, inline_updated_at = _unwrap_items(payload)
    saved_at = inline_updated_at or _source_saved_at(source, period)
    cache_file = _cache_path(source, period)
    model = ai_service.active_model()

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("source_saved_at") == saved_at and cached.get("provider") == model:
                return cached
        except Exception:
            pass    # unreadable cache: fall through and rebuild it

    ai_fallback = False
    ai_error = ""
    try:
        result = await ai_service.classify_trending_items(items, source=source)
    except Exception as e:
        ai_fallback = True
        ai_error = str(e)[:240]
        logger.warning("[classify] %s fell back to a single category: %s", source, ai_error)
        result = _fallback(items)

    payload_out = {
        "source": source,
        "period": period,
        "source_saved_at": saved_at,
        "provider": model,
        "ai_fallback": ai_fallback,
        "ai_error": ai_error,
        "categories": result.get("categories", []),
        "items": items,
    }
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload_out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[classify] could not write cache %s: %s", cache_file, e)
    return payload_out
