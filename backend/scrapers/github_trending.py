"""
GitHub Trending scraper — uses official GitHub API (no auth needed for public data)
with HTML fallback.
"""
import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://github.com/trending"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch(period: str = "daily") -> list[dict]:
    period = period if period in ("daily", "weekly", "monthly") else "daily"
    url = f"{BASE_URL}?since={period}"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        # GitHub uses <article class="Box-row"> for each repo card
        articles = soup.select("article.Box-row")

        if not articles:
            # Fallback: try any article tag
            articles = soup.select("article")

        repos = []
        for article in articles[:25]:
            # Repo name: h2 contains "owner / repo" as text
            h2 = article.select_one("h2")
            if not h2:
                continue
            link = h2.select_one("a")
            if not link:
                continue
            href = link.get("href", "").strip("/")
            parts = href.split("/")
            full_name = "/".join(parts[-2:]) if len(parts) >= 2 else href

            # Description
            desc_el = article.select_one("p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            # Language
            lang_el = article.select_one('[itemprop="programmingLanguage"]')
            language = lang_el.get_text(strip=True) if lang_el else ""

            # Stars / Forks — look for svg octicons links
            star_links = article.select("a.Link--muted")
            stars = star_links[0].get_text(strip=True).replace(",", "").strip() if star_links else "0"
            forks = star_links[1].get_text(strip=True).replace(",", "").strip() if len(star_links) > 1 else "0"

            # Stars today
            today_el = article.select_one("span.d-inline-block.float-sm-right") or \
                       article.select_one("[class*='float-sm-right']")
            today_stars = today_el.get_text(strip=True) if today_el else ""

            repos.append({
                "name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": desc,
                "language": language,
                "stars": stars,
                "forks": forks,
                "stars_today": today_stars,
            })
        # NOTE: must be awaited — returning the bare coroutine used to make the
        # scheduled refresh blow up in json.dump().
        return repos if repos else await _fallback_github_api(period)
    except Exception:
        return await _fallback_github_api(period)


async def _fallback_github_api(period: str) -> list[dict]:
    """Search GitHub API for popular repos as a fallback."""
    import datetime
    try:
        date_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = date_map.get(period, 1)
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        url = f"https://api.github.com/search/repositories?q=created:>{since}&sort=stars&order=desc&per_page=25"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ApocalypseBot/1.0",
        }
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        return [
            {
                "name": r["full_name"],
                "url": r["html_url"],
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "stars": str(r.get("stargazers_count", 0)),
                "forks": str(r.get("forks_count", 0)),
                "stars_today": "",
            }
            for r in data.get("items", [])
        ]
    except Exception:
        return []


