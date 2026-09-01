"""
HuggingFace Daily Papers — uses the official HF API endpoint.
"""
import httpx

# HF has a public JSON endpoint for daily papers
PAPERS_API = "https://huggingface.co/api/daily_papers"
PAPERS_HTML = "https://huggingface.co/papers"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
}


async def fetch(period: str = "daily", limit: int = 30) -> list[dict]:
    from datetime import datetime, timedelta
    import asyncio

    days = {"daily": 1, "weekly": 7, "monthly": 30, "halfyear": 180}.get(period, 1)
    base_date = datetime.utcnow()
    dates = [(base_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    papers_dict = {}

    async def fetch_day(client: httpx.AsyncClient, date_str: str):
        try:
            resp = await client.get(PAPERS_API, params={"date": date_str})
            resp.raise_for_status()
            for item in resp.json():
                paper = item.get("paper", item)
                arxiv_id = paper.get("id", "")
                if not arxiv_id or arxiv_id in papers_dict:
                    continue
                title = paper.get("title", "")
                url = f"https://huggingface.co/papers/{arxiv_id}"
                abstract = paper.get("summary", "") or paper.get("abstract", "")
                upvotes = item.get("numComments", 0) or paper.get("upvotes", 0)
                if title:
                    papers_dict[arxiv_id] = {
                        "title": title,
                        "url": url,
                        "abstract": abstract[:500] if abstract else "",
                        "upvotes": upvotes,
                        "arxiv_id": arxiv_id,
                    }
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        tasks = [fetch_day(client, d) for d in dates]
        await asyncio.gather(*tasks)

    # Sort by upvotes descending
    sorted_papers = sorted(papers_dict.values(), key=lambda x: x["upvotes"], reverse=True)
    if sorted_papers:
        return sorted_papers[:limit]

    # Fallback: scrape HTML
    try:
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(PAPERS_HTML)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        papers = []
        # Try multiple selectors for different HF page layouts
        for sel in ["article", "div[class*='paper']", ".paper-card"]:
            articles = soup.select(sel)
            if articles:
                for art in articles[:30]:
                    title_el = art.select_one("h3") or art.select_one("h2") or art.select_one("a")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    link_el = art.select_one("a[href*='/papers/']")
                    href = link_el["href"] if link_el else ""
                    url = f"https://huggingface.co{href}" if href.startswith("/") else href
                    abstract_el = art.select_one("p")
                    abstract = abstract_el.get_text(strip=True)[:500] if abstract_el else ""
                    if title and len(title) > 10:
                        papers.append({"title": title, "url": url, "abstract": abstract, "upvotes": 0})
                if papers:
                    break
        return papers
    except Exception:
        return []
