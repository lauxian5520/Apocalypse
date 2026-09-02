"""Network tool handlers.

Search scrapes DuckDuckGo's HTML endpoint with the same httpx + BeautifulSoup
pairing `backend/scrapers/` already uses, so no new dependency is needed.
"""
import logging

import httpx
from bs4 import BeautifulSoup

from core.config import get_settings
from core.errors import UpstreamError, ValidationError
from harness.tools.base import ToolContext

settings = get_settings()
logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
MAX_PAGE_CHARS = 20000
MAX_SEARCH_RESULTS = 20
DEFAULT_SEARCH_RESULTS = 8
DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"

# Some sites return a challenge page to a default httpx agent.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def web_fetch(ctx: ToolContext, url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValidationError(f"只支持 http/https 地址：{url}")

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=BROWSER_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        raise UpstreamError(f"抓取失败（{url}）：{_reason(e)}")

    text = _to_text(html)
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n[页面过长，已截断]"
    return text or "[页面没有可读正文]"


async def web_search(ctx: ToolContext, query: str, limit: int = 0) -> str:
    limit = min(max(1, limit or DEFAULT_SEARCH_RESULTS), MAX_SEARCH_RESULTS)
    endpoint = settings.harness_search_url or DUCKDUCKGO_HTML

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=BROWSER_HEADERS
        ) as client:
            resp = await client.post(endpoint, data={"q": query})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        raise UpstreamError(f"搜索失败：{_reason(e)}")

    results = _parse_results(html, limit)
    if not results:
        return f"没有找到与「{query}」相关的结果"
    return "\n\n".join(
        f"{n}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
        for n, r in enumerate(results, start=1)
    )


def _parse_results(html: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for node in soup.select(".result")[: limit * 2]:
        link = node.select_one(".result__a")
        if not link:
            continue
        snippet = node.select_one(".result__snippet")
        out.append({
            "title": link.get_text(strip=True),
            "url": link.get("href", ""),
            "snippet": snippet.get_text(strip=True) if snippet else "",
        })
        if len(out) >= limit:
            break
    return out


def _to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _reason(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        return f"HTTP {resp.status_code}"
    return str(exc) or exc.__class__.__name__


HANDLERS = {"web_fetch": web_fetch, "web_search": web_search}
