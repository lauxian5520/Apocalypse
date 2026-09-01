"""
Google Scholar scraper (best-effort, public HTML parsing).
"""
import re
import httpx
from bs4 import BeautifulSoup

BASE = "https://scholar.google.com/scholar"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


def _parse_year(meta: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", meta or "")
    return int(m.group(0)) if m else None


def _parse_citations(card: BeautifulSoup) -> int:
    for a in card.select("a"):
        t = a.get_text(" ", strip=True)
        m = re.search(r"Cited by\s*(\d+)", t, re.I)
        if m:
            return int(m.group(1))
    return 0


def _parse_authors(meta: str) -> list[str]:
    if not meta:
        return []
    left = meta.split("-")[0].strip()
    out = []
    for p in left.split(","):
        p = p.strip()
        if p:
            out.append(p)
    return out[:4]


async def fetch(limit: int = 20) -> list[dict]:
    queries = [
        "artificial intelligence",
        "large language model",
        "machine learning",
    ]

    items: list[dict] = []
    seen = set()

    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        for q in queries:
            if len(items) >= limit:
                break
            try:
                r = await c.get(BASE, params={"hl": "en", "q": q})
                r.raise_for_status()
            except Exception:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for card in soup.select("div.gs_r.gs_or.gs_scl"):
                if len(items) >= limit:
                    break

                title_el = card.select_one("h3.gs_rt")
                if not title_el:
                    continue

                link = title_el.select_one("a")
                title = title_el.get_text(" ", strip=True)
                url = link.get("href", "") if link else ""
                if not title or title in seen:
                    continue

                abstract = ""
                abs_el = card.select_one("div.gs_rs")
                if abs_el:
                    abstract = abs_el.get_text(" ", strip=True)

                meta = ""
                meta_el = card.select_one("div.gs_a")
                if meta_el:
                    meta = meta_el.get_text(" ", strip=True)

                items.append(
                    {
                        "title": title,
                        "url": url,
                        "abstract": abstract[:500],
                        "citations": _parse_citations(card),
                        "year": _parse_year(meta),
                        "authors": _parse_authors(meta),
                        "source": "Scholar",
                    }
                )
                seen.add(title)

    return items
