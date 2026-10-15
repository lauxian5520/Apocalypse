"""Cached scraper feeds, plus AI-categorised variants."""
import asyncio
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from core.deps import require_admin
from scrapers.conference_catalog import ConferenceVenue, conference_catalog
from services import classify_service, conference_service, feed_service

router = APIRouter(prefix="/feeds", tags=["feeds"])

PERIOD_PATTERN = "^(daily|weekly|monthly)$"
PAPER_PERIOD_PATTERN = "^(daily|weekly|monthly|halfyear)$"


@router.get("/github")
async def github(period: str = Query("daily", pattern=PERIOD_PATTERN)):
    return await feed_service.ensure_cached(f"github_{period}", lambda: feed_service.refresh_github(period))


@router.get("/github/classified")
async def github_classified(period: str = Query("daily", pattern=PERIOD_PATTERN)):
    payload = await github(period)
    return await classify_service.classify("github", payload, period)


@router.get("/huggingface")
async def huggingface():
    return await feed_service.ensure_cached("huggingface", feed_service.refresh_huggingface)


@router.get("/huggingface/classified")
async def huggingface_classified():
    return await classify_service.classify("huggingface", await huggingface())


@router.get("/papers", summary="Get HuggingFace / arXiv papers")
async def papers(period: str = Query("daily", pattern=PAPER_PERIOD_PATTERN)):
    hf, arxiv = await asyncio.gather(
        feed_service.ensure_cached(f"hf_papers_{period}", lambda: feed_service.refresh_hf_papers(period)),
        feed_service.ensure_cached(f"arxiv_papers_{period}", lambda: feed_service.refresh_arxiv_papers(period)),
    )
    payload = feed_service.get_papers(period)
    return {**payload, "refreshing": hf["refreshing"] or arxiv["refreshing"],
            "stale": hf["stale"] or arxiv["stale"], "status": arxiv["status"]}


@router.get("/papers/classified", summary="Papers grouped into research areas by AI")
async def papers_classified(period: str = Query("daily", pattern=PAPER_PERIOD_PATTERN)):
    payload = await feed_service.current_hf_papers(period)
    return await classify_service.classify("papers", payload, period)


@router.get('/papers/conferences/catalog', summary='Supported conferences and their calendar years')
def conference_metadata():
    return {'conferences': conference_catalog()}


@router.get('/papers/conferences', summary='Accepted top conference papers by research area')
async def conference_papers(
    venue: ConferenceVenue = ConferenceVenue.all,
    year: int = Query(datetime.now().year - 1, ge=2020, le=datetime.now().year),
    area: Literal['all', 'llm', 'vision', 'rl', 'robotics', 'generative', 'graph', 'security', 'science', 'theory', 'other'] = 'all',
    q: str = Query('', max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=120),
):
    return await conference_service.get_papers(venue.value, year, area, q, offset, limit)


@router.get("/focus")
async def focus():
    """Return recent news, refreshing expired caches and reporting refresh state."""
    return await feed_service.ensure_cached("focus", feed_service.refresh_focus)


@router.post("/refresh")
async def refresh(background_tasks: BackgroundTasks, _=Depends(require_admin)):
    """Admin-only: kick off a full re-scrape in the background."""
    background_tasks.add_task(feed_service.refresh_all)
    return {"message": "刷新任务已启动，后台执行中..."}
