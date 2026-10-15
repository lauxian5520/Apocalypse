"""Timestamped feed caches and coalesced, failure-tolerant refresh jobs."""
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import json
import logging
import os
import tempfile
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import get_settings
from scrapers import arxiv_papers, github_trending, hf_papers, huggingface, tophub

logger = logging.getLogger(__name__)
settings = get_settings()
PERIODS = ("daily", "weekly", "monthly", "halfyear")
_refresh_tasks: dict[str, asyncio.Task] = {}
_failures: dict[str, float] = {}


def feed_path(name: str) -> str:
    """Resolve a cache name within the configured runtime data directory."""
    return os.path.join(settings.feeds_dir, f"{name}.json")


def _write_json(name: str, payload: dict) -> None:
    """Replace complete files atomically so readers never see partial JSON."""
    os.makedirs(settings.feeds_dir, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings.feeds_dir,
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, feed_path(name))
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _save(name: str, data: list | dict) -> None:
    """Record successful synchronization rather than the time of an attempt."""
    _write_json(name, {"updated_at": datetime.now(timezone.utc).isoformat(), "data": data})


def _save_nonempty(name: str, data: list) -> None:
    """Retain good caches when scrapers return an empty failure."""
    if not data:
        raise ValueError(f"{name}: no source data returned")
    _save(name, data)


def _load(name: str) -> dict:
    """Treat missing and corrupt files as cache misses."""
    try:
        with open(feed_path(name), encoding="utf-8") as stream:
            payload = json.load(stream)
        if isinstance(payload, dict):
            return payload
    except (OSError, ValueError):
        pass
    return {"updated_at": None, "data": []}


def get_github(period: str = "daily") -> dict:
    """Read repositories for a selected period."""
    return _load(f"github_{period}")


def get_huggingface() -> dict:
    """Read the cached model list."""
    return _load("huggingface")


def get_papers(period: str = "daily") -> dict:
    """Combine independently refreshed sources, retaining legacy formats."""
    combined = _load(f"papers_combined_{period}")
    legacy = combined.get("data")
    hf = _load(f"hf_papers_{period}")
    arxiv = _load(f"arxiv_papers_{period}")
    hf_items = hf.get("data") or (legacy.get("hf") if isinstance(legacy, dict) else [])
    if not hf_items:
        hf = _load("hf_papers")
        hf_items = hf.get("data") or []
    arxiv_items = arxiv.get("data") or (legacy.get("scholar") if isinstance(legacy, dict) else legacy)
    if not arxiv_items:
        arxiv_items = _load("scholar_papers").get("data") or []
    return {
        "updated_at": hf.get("updated_at") or arxiv.get("updated_at") or combined.get("updated_at"),
        "hf_updated_at": hf.get("updated_at") or combined.get("updated_at"),
        "data": {"hf": hf_items if isinstance(hf_items, list) else [],
                 "scholar": arxiv_items if isinstance(arxiv_items, list) else []},
    }


def get_focus() -> dict:
    """Exclude expired and undated articles even from legacy cache files."""
    payload = _load("focus")
    categories = payload.get("categories") or {}
    if not isinstance(categories, dict):
        categories = {}
    return {**payload, "categories": {
        key: {"sections": tophub._compact_sections((value or {}).get("sections", {}))}
        for key, value in categories.items()
    }, "max_age_hours": settings.focus_max_age_hours}


def _has_data(name: str, payload: dict) -> bool:
    """Check news content instead of merely checking category keys."""
    if name != "focus":
        return bool(payload.get("data"))
    return any((category or {}).get("sections") for category in payload.get("categories", {}).values())


def _expired(name: str, payload: dict) -> bool:
    """Expire absent, invalid, future or stale synchronization dates."""
    try:
        stamp = datetime.fromisoformat(str(payload.get("updated_at") or payload.get("savedAt") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        ttl = settings.focus_cache_ttl_minutes if name == "focus" else settings.feeds_cache_ttl_minutes
        return age < 0 or age >= ttl * 60 or not _has_data(name, payload)
    except (TypeError, ValueError):
        return True


def _start_refresh(name: str, refresh: Callable[[], Awaitable[None]]) -> asyncio.Task:
    """Share work among startup, scheduled jobs and simultaneous page requests."""
    existing = _refresh_tasks.get(name)
    if existing is not None and not existing.done():
        return existing

    async def run() -> None:
        """Contain upstream failures and avoid destroying last known good data."""
        try:
            await refresh()
            _failures.pop(name, None)
        except Exception as exc:
            _failures[name] = time.monotonic()
            logger.warning("Feed refresh failed: %s (%s)", name, type(exc).__name__)

    task = asyncio.create_task(run())
    _refresh_tasks[name] = task
    return task


async def ensure_cached(name: str, refresh: Callable[[], Awaitable[None]]) -> dict:
    """Return cache state promptly while refreshing expired sources in the background."""
    read = get_focus if name == "focus" else lambda: _load(name)
    payload = read()
    if settings.feeds_enabled and _expired(name, payload):
        failed_at = _failures.get(name)
        if failed_at is None or time.monotonic() - failed_at >= settings.feeds_retry_seconds:
            task = _start_refresh(name, refresh)
            if not _has_data(name, payload) and settings.feeds_initial_wait_seconds:
                try:
                    await asyncio.wait_for(asyncio.shield(task), settings.feeds_initial_wait_seconds)
                except asyncio.TimeoutError:
                    pass
                payload = read()
    refreshing = name in _refresh_tasks and not _refresh_tasks[name].done()
    stale = _expired(name, payload)
    status = ("refreshing" if refreshing else "disabled" if not settings.feeds_enabled and not _has_data(name, payload)
              else "error" if name in _failures else "stale" if stale and _has_data(name, payload)
              else "empty" if not _has_data(name, payload) else "ready")
    return {**payload, "status": status, "stale": stale, "refreshing": refreshing}


async def refresh_github(period: str | None = None) -> None:
    """Refresh selected periods without replacing good data with failures."""
    for current in ((period,) if period else PERIODS[:3]):
        _save_nonempty(f"github_{current}", await github_trending.fetch(current))


async def refresh_huggingface() -> None:
    """Refresh the public model list."""
    _save_nonempty("huggingface", await huggingface.fetch())


async def refresh_hf_papers(period: str) -> None:
    """Publish HF papers without waiting for unrelated arXiv requests."""
    _save_nonempty(f"hf_papers_{period}", await hf_papers.fetch(period=period))


async def current_hf_papers(period: str) -> dict:
    """Keep legacy combined caches usable while a new source cache is populated."""
    payload = await ensure_cached(f"hf_papers_{period}", lambda: refresh_hf_papers(period))
    if not payload.get("data"):
        legacy = get_papers(period)
        payload = {**payload, "data": legacy["data"]["hf"],
                   "updated_at": legacy.get("hf_updated_at") or legacy.get("updated_at")}
    return payload


async def refresh_arxiv_papers(period: str) -> None:
    """Refresh arXiv independently."""
    _save_nonempty(f"arxiv_papers_{period}", await arxiv_papers.fetch(period=period))


async def refresh_papers() -> None:
    """Refresh each source and period independently."""
    tasks = []
    for period in PERIODS:
        tasks.append(_start_refresh(f"hf_papers_{period}", lambda p=period: refresh_hf_papers(p)))
        tasks.append(_start_refresh(f"arxiv_papers_{period}", lambda p=period: refresh_arxiv_papers(p)))
    await asyncio.gather(*tasks)


async def refresh_focus() -> None:
    """Publish a news timestamp only after receiving recent dated items."""
    data = await tophub.fetch_categorized()
    if not _has_data("focus", data):
        raise ValueError("No recent dated news returned")
    _write_json("focus", data)


async def refresh_focus_job() -> None:
    """Coalesce scheduled news refreshes with page-triggered refreshes."""
    await _start_refresh("focus", refresh_focus)


async def refresh_all() -> None:
    """Do not let one unavailable source stop other feed updates."""
    tasks = [_start_refresh(f"github_{p}", lambda p=p: refresh_github(p)) for p in PERIODS[:3]]
    tasks.extend([_start_refresh("huggingface", refresh_huggingface),
                  _start_refresh("focus", refresh_focus), asyncio.create_task(refresh_papers())])
    await asyncio.gather(*tasks)


def has_cached_feeds() -> bool:
    """Require every primary cache; a lone news file must not mask missing projects."""
    names = [*(f"github_{p}" for p in PERIODS[:3]), "huggingface",
             *(f"hf_papers_{p}" for p in PERIODS), *(f"arxiv_papers_{p}" for p in PERIODS), "focus"]
    return all(not _expired(name, get_focus() if name == "focus" else _load(name)) for name in names)


def start_scheduler() -> AsyncIOScheduler:
    """Refresh news more often than project and paper snapshots."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_all, "interval", minutes=settings.feeds_cache_ttl_minutes, id="refresh_all")
    scheduler.add_job(refresh_focus_job, "interval", minutes=settings.focus_cache_ttl_minutes, id="refresh_focus")
    scheduler.start()
    return scheduler


async def stop_refreshes() -> None:
    """Cancel scraper tasks before releasing application resources."""
    tasks = [task for task in _refresh_tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _refresh_tasks.clear()
