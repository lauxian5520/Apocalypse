"""Cached scraper feeds, plus AI-categorised variants."""
import json
import os
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from core.deps import require_admin
from scrapers import google_scholar, tophub
from services import classify_service, feed_service

router = APIRouter(prefix="/feeds", tags=["feeds"])

PERIOD_PATTERN = "^(daily|weekly|monthly)$"
PAPER_PERIOD_PATTERN = "^(daily|weekly|monthly|halfyear)$"
FOCUS_CATEGORIES = {"finance", "tech", "ai", "politics"}


@router.get("/github")
def github(period: str = Query("daily", pattern=PERIOD_PATTERN)):
    return feed_service.get_github(period)


@router.get("/github/classified")
async def github_classified(period: str = Query("daily", pattern=PERIOD_PATTERN)):
    return await classify_service.classify("github", feed_service.get_github(period), period)


@router.get("/huggingface")
def huggingface():
    return feed_service.get_huggingface()


@router.get("/huggingface/classified")
async def huggingface_classified():
    return await classify_service.classify("huggingface", feed_service.get_huggingface())


@router.get("/papers", summary="Get HuggingFace / arXiv papers")
async def papers(period: str = Query("daily", pattern=PAPER_PERIOD_PATTERN)):
    payload = feed_service.get_papers(period)
    data = payload.get("data") if isinstance(payload, dict) else {}
    hf = data.get("hf", []) if isinstance(data, dict) else []
    scholar = data.get("scholar", []) if isinstance(data, dict) else []

    if not scholar:
        try:
            scholar = await google_scholar.fetch(limit=20)
        except Exception:
            scholar = []

    # Last resort: turn each HF paper into a Scholar search link.
    if not scholar and hf:
        scholar = [
            {
                "title": it.get("title", ""),
                "url": f"https://scholar.google.com/scholar?q={quote_plus(it.get('title', ''))}",
                "abstract": it.get("abstract", ""),
                "citations": 0,
                "year": None,
                "authors": [],
                "source": "Scholar",
            }
            for it in hf[:20]
            if it.get("title")
        ]

    return {
        "updated_at": payload.get("updated_at") if isinstance(payload, dict) else None,
        "data": {"hf": hf, "scholar": scholar},
    }


@router.get("/papers/classified", summary="Papers grouped into research areas by AI")
async def papers_classified(period: str = Query("daily", pattern=PAPER_PERIOD_PATTERN)):
    payload = feed_service.get_papers(period)
    data = payload.get("data") if isinstance(payload, dict) else {}
    hf = data.get("hf", []) if isinstance(data, dict) else []
    return await classify_service.classify("papers", hf, period)


@router.get("/focus")
async def focus():
    """Hot-topic aggregation. Re-scrapes when the cache is empty or incomplete."""
    path = feed_service.feed_path("focus")
    data = {"savedAt": None, "categories": {}}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    categories = data.get("categories") if isinstance(data, dict) else {}
    has_required = isinstance(categories, dict) and FOCUS_CATEGORIES.issubset(categories.keys())
    has_items = isinstance(categories, dict) and any(
        any(section or [] for section in (c or {}).get("sections", {}).values())
        for c in categories.values()
    )

    if has_required and has_items:
        return data

    fresh = await tophub.fetch_categorized()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return fresh


@router.post("/refresh")
async def refresh(background_tasks: BackgroundTasks, _=Depends(require_admin)):
    """Admin-only: kick off a full re-scrape in the background."""
    background_tasks.add_task(feed_service.refresh_all)
    return {"message": "刷新任务已启动，后台执行中..."}
