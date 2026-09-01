"""Scraper scheduling and the JSON feed cache.

Owns *when* external data is fetched and *where* it is cached; the scrapers
themselves only know how to fetch. Cache files live under var/feeds/.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import get_settings
from scrapers import arxiv_papers, github_trending, hf_papers, huggingface, tophub

logger = logging.getLogger(__name__)
settings = get_settings()

REFRESH_INTERVAL_HOURS = 6


def feed_path(name: str) -> str:
    """Absolute path of a cache file inside the feed directory."""
    return os.path.join(settings.feeds_dir, f"{name}.json")


def _ensure_dir():
    os.makedirs(settings.feeds_dir, exist_ok=True)


def _save(name: str, data):
    _ensure_dir()
    path = feed_path(name)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "data": data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"[scraper] Saved {len(data)} items to {name}.json")


def _load(name: str) -> dict:
    path = feed_path(name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"updated_at": None, "data": []}


async def refresh_github():
    for period in ["daily", "weekly", "monthly"]:
        data = await github_trending.fetch(period)
        _save(f"github_{period}", data)


async def refresh_huggingface():
    data = await huggingface.fetch()
    _save("huggingface", data)


async def refresh_papers():
    for period in ["daily", "weekly", "monthly", "halfyear"]:
        hf_data = await hf_papers.fetch(period=period)
        arxiv_data = await arxiv_papers.fetch(period=period)
        _save(f"hf_papers_{period}", hf_data)
        _save(f"papers_combined_{period}", {"hf": hf_data, "scholar": arxiv_data}) # Keeping key 'scholar' for frontend backward compat, though it's arxiv data now


async def refresh_focus():
    # focus.json keeps the full categorised dict rather than the {updated_at,data}
    # envelope the other feeds use, so it is written directly.
    data = await tophub.fetch_categorized()
    _ensure_dir()
    with open(feed_path("focus"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[scraper] Saved focus (categorized) to focus.json")


async def refresh_all():
    logger.info("[scraper] Starting full refresh...")
    await asyncio.gather(
        refresh_github(),
        refresh_huggingface(),
        refresh_papers(),
        refresh_focus(),
    )
    logger.info("[scraper] Full refresh complete.")


# ── Public API ──────────────────────────────────────────────────────────────

def get_github(period: str = "daily") -> dict:
    return _load(f"github_{period}")


def get_huggingface() -> dict:
    return _load("huggingface")


def get_papers(period: str = "daily") -> dict:
    combined = _load(f"papers_combined_{period}")
    combined_data = combined.get("data")
    if isinstance(combined_data, dict):
        return combined

    # Backward compatibility for old data files. combined_data may be a list
    # (legacy format) or [] (cache file missing) — guard before calling .get(),
    # which used to raise AttributeError and return HTTP 500.
    hf_periodic = _load(f"hf_papers_{period}")
    hf = hf_periodic.get("data") or []
    hf_legacy = None
    if not hf:  # Try without period fallback
        hf_legacy = _load("hf_papers")
        hf = hf_legacy.get("data") or []
    if not isinstance(hf, list):
        hf = []

    scholar = []
    if isinstance(combined_data, list):
        scholar = combined_data
    if not scholar:
        scholar = _load("scholar_papers").get("data") or []
    if not isinstance(scholar, list):
        scholar = []

    updated_at = combined.get("updated_at") or hf_periodic.get("updated_at")
    if not updated_at:
        updated_at = (hf_legacy or _load("hf_papers")).get("updated_at")
    return {
        "updated_at": updated_at,
        "data": {"hf": hf, "scholar": scholar},
    }


def get_focus() -> dict:
    return _load("focus")


def has_cached_feeds() -> bool:
    """True when at least one feed file already exists."""
    if not os.path.isdir(settings.feeds_dir):
        return False
    return any(f.endswith(".json") for f in os.listdir(settings.feeds_dir))


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_all, "interval", hours=REFRESH_INTERVAL_HOURS, id="refresh_all")
    scheduler.start()
    logger.info("[scheduler] Started. Next refresh in %sh.", REFRESH_INTERVAL_HOURS)
    return scheduler
