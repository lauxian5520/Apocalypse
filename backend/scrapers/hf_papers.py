"""Hugging Face daily/weekly/monthly papers through the official public API."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging

import httpx

PAPERS_API = "https://huggingface.co/api/daily_papers"
HEADERS = {"User-Agent": "ApocalypseBot/1.0", "Accept": "application/json"}
logger = logging.getLogger(__name__)


def _period_queries(period: str, now: datetime) -> list[dict]:
    """Use server-side period aggregation instead of hundreds of daily requests."""
    if period == "weekly":
        year, week, _ = now.isocalendar()
        return [{"week": f"{year}-W{week:02d}"}]
    if period == "monthly":
        return [{"month": now.strftime("%Y-%m")}]
    if period == "halfyear":
        first = (now - timedelta(days=180)).replace(day=1)
        queries = []
        while first <= now:
            queries.append({"month": first.strftime("%Y-%m")})
            first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        return queries
    # Omit date so weekends and the interval before today's issue is published
    # receive the latest available issue rather than an empty calendar date.
    return [{}]


async def fetch(period: str = "daily", limit: int = 30) -> list[dict]:
    """Fetch period rankings with bounded concurrency and real source dates."""
    now = datetime.now(timezone.utc)
    days = {"daily": 7, "weekly": 7, "monthly": 31, "halfyear": 180}.get(period, 7)
    cutoff = now - timedelta(days=days)
    slots = asyncio.Semaphore(4)

    async def request(client: httpx.AsyncClient, query: dict) -> list:
        """Contain individual monthly failures while retaining successful results."""
        try:
            async with slots:
                response = await client.get(PAPERS_API, params={**query, "sort": "trending", "limit": limit})
                response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else []
        except (httpx.HTTPError, ValueError):
            logger.warning("Hugging Face paper query failed for %s", query)
            return []

    async with httpx.AsyncClient(timeout=25, headers=HEADERS, follow_redirects=True) as client:
        results = await asyncio.gather(*(request(client, query) for query in _period_queries(period, now)))
    papers: dict[str, dict] = {}
    for items in results:
        for item in items:
            paper = item.get("paper", item)
            identifier = paper.get("id")
            title = paper.get("title")
            featured = paper.get("submittedOnDailyAt") or item.get("publishedAt") or paper.get("publishedAt")
            try:
                stamp = datetime.fromisoformat(str(featured).replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            except (TypeError, ValueError):
                continue
            if not identifier or not title or not cutoff <= stamp <= now + timedelta(minutes=5):
                continue
            papers[identifier] = {
                "title": title, "url": f"https://huggingface.co/papers/{identifier}",
                "abstract": (paper.get("summary") or paper.get("abstract") or "")[:500],
                "upvotes": paper.get("upvotes", item.get("upvotes", 0)) or 0,
                "arxiv_id": identifier, "published_at": paper.get("publishedAt") or stamp.isoformat(),
                "featured_at": stamp.isoformat(),
                "authors": [author["name"] for author in paper.get("authors", []) if author.get("name")],
            }
    return sorted(papers.values(), key=lambda paper: paper["upvotes"], reverse=True)[:limit]
