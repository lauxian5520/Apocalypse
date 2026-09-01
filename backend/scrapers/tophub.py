"""
Focus news scraper — produces categorized JSON matching Asstar's realtime-focus.json format.
Uses Python built-in xml.etree.ElementTree for RSS (no lxml needed).
"""
import httpx
import xml.etree.ElementTree as ET
import re
from bs4 import BeautifulSoup
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _parse_rss(xml_text: str, limit: int = 15) -> list[dict]:
    """Parse RSS using Python built-in ElementTree (no extra deps)."""
    items = []
    try:
        # Strip XML namespaces then parse
        clean = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', '', xml_text)
        clean = re.sub(r'<(\w+):\w+', r'<\1_ns', clean)
        clean = re.sub(r'</(\w+):\w+>', r'</\1_ns>', clean)
        root = ET.fromstring(clean)
        channel = root.find('channel') or root
        for item in list(channel.iter('item'))[:limit]:
            title_el = item.find('title')
            link_el = item.find('link')
            title = (title_el.text or '').strip() if title_el is not None else ''
            link = (link_el.text or '').strip() if link_el is not None else ''
            if not link:
                guid_el = item.find('guid')
                link = (guid_el.text or '').strip() if guid_el is not None else ''
            if title and len(title) > 5:
                items.append({'title': title, 'url': link})
    except Exception:
        pass
    return items


async def _rss(url: str, limit: int = 15) -> list[dict]:
    """Generic RSS fetcher returning parsed items."""
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
        return _parse_rss(r.text, limit)
    except Exception:
        return []


# ── Source fetchers ──────────────────────────────────────────────────────────

async def _fetch_ithome() -> list[dict]:
    """IT之家 RSS"""
    return await _rss("https://www.ithome.com/rss/")


async def _fetch_huxiu() -> list[dict]:
    """虎嗅 RSS"""
    return await _rss("https://www.huxiu.com/rss/0.xml")


async def _fetch_solidot() -> list[dict]:
    """solidot RSS"""
    return await _rss("https://www.solidot.org/index.rss")


async def _fetch_oschina() -> list[dict]:
    """开源中国 RSS"""
    return await _rss("https://www.oschina.net/news/rss")


async def _fetch_eastmoney() -> list[dict]:
    """东方财富 — try multiple RSS endpoints"""
    for url in [
        "https://rss.eastmoney.com/kx.html",
        "https://36kr.com/feed",
    ]:
        items = await _rss(url)
        if items:
            return items
    return []


async def _fetch_tophub_finance_batch() -> dict:
    """Batch fetch multiple financial sources from Tophub category pages"""
    specs = [
        {'url': 'https://tophub.today/c/finance', 'targets': ['第一财经', '雪球', '华尔街见闻', '集思录']},
        {'url': 'https://tophub.today/c/finance?&p=3', 'targets': ['格隆汇', '金融界', '慧博投研资讯', '英为财情', '证券日报网', '美股市值']},
        {'url': 'https://tophub.today/c/finance?&p=4', 'targets': ['同花顺财经']}
    ]
    
    parsed = {t: [] for spec in specs for t in spec['targets']}
    
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        for spec in specs:
            try:
                r = await c.get(spec['url'])
                r.raise_for_status()
                soup = BeautifulSoup(r.text, 'html.parser')
                cards = soup.select('.cc-cd')
                for card in cards:
                    label_el = card.select_one('.cc-cd-lb')
                    label = label_el.get_text(strip=True) if label_el else ''
                    target = next((t for t in spec['targets'] if t in label), None)
                    if not target: continue
                    
                    items = []
                    for a in card.select('.cc-cd-cb a[href]'):
                        href = a.get('href', '').strip()
                        if not href.startswith('http'): continue
                        if href.startswith('https:https://'): href = href.replace('https:https://', 'https://', 1)
                        elif href.startswith('http:http://'): href = href.replace('http:http://', 'http://', 1)
                        
                        row = a.select_one('.cc-cd-cb-ll')
                        if not row: continue
                        title = row.select_one('.t')
                        title_text = title.get_text(strip=True) if title else ''
                        if title_text:
                            items.append({'title': title_text, 'url': href})
                            if len(items) >= 15: break
                    if items:
                        parsed[target] = items
            except Exception as e:
                print(f"Tophub batch fetch error on {spec['url']}: {e}")
    
    return parsed


async def _fetch_bilibili_hot() -> list[dict]:
    """B站热搜 API"""
    try:
        async with httpx.AsyncClient(timeout=12, headers=HEADERS, follow_redirects=True) as c:
            r = await c.get("https://s.search.bilibili.com/main/hotword")
            r.raise_for_status()
            data = r.json()
        return [
            {"title": it.get("keyword", ""),
             "url": f"https://search.bilibili.com/all?keyword={it.get('keyword','')}"}
            for it in data.get("list", [])[:15] if it.get("keyword")
        ]
    except Exception:
        return []


async def _fetch_zhihu_hot() -> list[dict]:
    """知乎热榜 API"""
    try:
        url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=20"
        async with httpx.AsyncClient(timeout=12, headers={**HEADERS, "Referer": "https://www.zhihu.com/"}) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
        return [
            {"title": it.get("target", {}).get("title", ""),
             "url": "https://www.zhihu.com/question/" + str(it.get("target", {}).get("id", ""))}
            for it in data.get("data", []) if it.get("target", {}).get("title")
        ]
    except Exception:
        return []


async def _fetch_xinhua() -> list[dict]:
    """新华网科技 HTML"""
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as c:
            r = await c.get("https://www.xinhuanet.com/tech/index.htm")
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        seen = set()
        for a in soup.select("a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if len(title) > 10 and "/tech/" in href and title not in seen:
                url_full = href if href.startswith("http") else f"https://www.xinhuanet.com{href}"
                items.append({"title": title, "url": url_full})
                seen.add(title)
            if len(items) >= 15:
                break
        return items
    except Exception:
        return []

async def _fetch_baidu_hot() -> list[dict]:
    """百度热搜 API"""
    try:
        url = "https://top.baidu.com/api/board?platform=pc&tab=realtime"
        async with httpx.AsyncClient(timeout=12, headers=HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
        cards = data.get("data", {}).get("cards", [])
        if not cards: return []
        items = cards[0].get("content", [])
        return [
            {"title": it.get("word", ""), "url": it.get("appUrl", "")}
            for it in items[:15] if it.get("word")
        ]
    except Exception:
        return []

async def _fetch_pengpai() -> list[dict]:
    """澎湃新闻 RSS"""
    return await _rss("https://www.thepaper.cn/rss")

async def _fetch_sina_news() -> list[dict]:
    """新浪国内要闻 RSS"""
    return await _rss("http://rss.sina.com.cn/news/china/focus15.xml")


async def _fetch_google_news(query: str, limit: int = 15) -> list[dict]:
    """Google News RSS by query, useful as resilient fallback source."""
    from urllib.parse import quote
    q = quote(query)
    return await _rss(f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", limit=limit)


async def _fetch_reuters_tech() -> list[dict]:
    return await _rss("https://feeds.reuters.com/reuters/technologyNews")


async def _fetch_reuters_business() -> list[dict]:
    return await _rss("https://feeds.reuters.com/reuters/businessNews")


async def _fetch_cnbeta() -> list[dict]:
    return await _rss("https://www.cnbeta.com/backend.php")


async def _fetch_arxiv_ai() -> list[dict]:
    return await _rss("https://export.arxiv.org/rss/cs.AI")


async def _fetch_techcrunch() -> list[dict]:
    return await _rss("https://techcrunch.com/feed/")


async def _fetch_theverge() -> list[dict]:
    return await _rss("https://www.theverge.com/rss/index.xml")


async def _fetch_venturebeat_ai() -> list[dict]:
    return await _rss("https://venturebeat.com/category/ai/feed/")


async def _fetch_reuters_world() -> list[dict]:
    return await _rss("https://feeds.reuters.com/Reuters/worldNews")


async def _fetch_bbc_world() -> list[dict]:
    return await _rss("https://feeds.bbci.co.uk/news/world/rss.xml")


def _to_section(items: list[dict], section_name: str = "焦点要闻") -> list[dict]:
    if not items:
        return []
    return [{"section": section_name, "items": items}]


def _clean_items(items: list[dict], limit: int = 15) -> list[dict]:
    out = []
    seen = set()
    for it in items or []:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        if not title:
            continue
        if not url:
            url = "#"
        k = title.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({"title": title, "url": url})
        if len(out) >= limit:
            break
    return out


def _compact_sections(sections: dict) -> dict:
    compact = {}
    for source, blocks in (sections or {}).items():
        clean_blocks = []
        for b in blocks or []:
            items = _clean_items((b or {}).get("items", []))
            if items:
                clean_blocks.append({"section": (b or {}).get("section", "焦点"), "items": items})
        if clean_blocks:
            compact[source] = clean_blocks
    return compact


# ── Public API ───────────────────────────────────────────────────────────────

async def fetch() -> list[dict]:
    """Flat list for backward compat."""
    import asyncio
    results = await asyncio.gather(
        _fetch_ithome(), _fetch_bilibili_hot(), _fetch_zhihu_hot(),
        return_exceptions=True,
    )
    flat = []
    sources = ["IT之家", "B站热搜", "知乎热榜"]
    for i, res in enumerate(results):
        if isinstance(res, list):
            for item in res:
                item["source"] = sources[i]
                flat.append(item)
    return flat


async def fetch_categorized() -> dict:
    """Categorized structure matching Asstar's realtime-focus.json."""
    import asyncio
    (ithome, eastmoney, huxiu, solidot, oschina,
     bilibili, zhihu, xinhua, reuters_biz, reuters_tech,
     cnbeta, arxiv_ai, techcrunch, theverge, venturebeat_ai,
     reuters_world, bbc_world,
     gnews_fin, gnews_tech, gnews_ai, gnews_politics, gnews_policy, gnews_world_politics,
     baidu, pengpai, sina, tophub_fin) = await asyncio.gather(
        _fetch_ithome(),
        _fetch_eastmoney(),
        _fetch_huxiu(),
        _fetch_solidot(),
        _fetch_oschina(),
        _fetch_bilibili_hot(),
        _fetch_zhihu_hot(),
        _fetch_xinhua(),
        _fetch_reuters_business(),
        _fetch_reuters_tech(),
        _fetch_cnbeta(),
        _fetch_arxiv_ai(),
        _fetch_techcrunch(),
        _fetch_theverge(),
        _fetch_venturebeat_ai(),
        _fetch_reuters_world(),
        _fetch_bbc_world(),
        _fetch_google_news("财经"),
        _fetch_google_news("科技"),
        _fetch_google_news("人工智能"),
        _fetch_google_news("时政"),
        _fetch_google_news("政策"),
        _fetch_google_news("国际政治"),
        _fetch_baidu_hot(),
        _fetch_pengpai(),
        _fetch_sina_news(),
        _fetch_tophub_finance_batch(),
        return_exceptions=True,
    )

    def s(r):
        return r if isinstance(r, list) else []
        
    def st(r, target):
        """Helper to extract a list of items from dict response for batch jobs"""
        if isinstance(r, dict):
            return r.get(target, [])
        return []

    finance_sections = _compact_sections({
        "东方财富网": _to_section(s(eastmoney), "财经要闻"),
        "同花顺财经": _to_section(st(tophub_fin, "同花顺财经"), "财经要闻"),
        "第一财经": _to_section(st(tophub_fin, "第一财经"), "市场焦点"),
        "雪球": _to_section(st(tophub_fin, "雪球"), "投资理财"),
        "英为财情": _to_section(st(tophub_fin, "英为财情"), "全球市场"),
        "金融界": _to_section(st(tophub_fin, "金融界"), "财经快讯"),
        "华尔街见闻": _to_section(st(tophub_fin, "华尔街见闻"), "全球财经"),
        "证券日报网": _to_section(st(tophub_fin, "证券日报网"), "资本市场"),
        "格隆汇": _to_section(st(tophub_fin, "格隆汇"), "港股美股"),
        "集思录": _to_section(st(tophub_fin, "集思录"), "低风险投资"),
        "慧博投研资讯": _to_section(st(tophub_fin, "慧博投研资讯"), "行业研报"),
        "美股市值": _to_section(st(tophub_fin, "美股市值"), "全球巨头"),
        "澎湃新闻": _to_section(s(pengpai), "宏观经济"),
        "36氪财经": _to_section(s(huxiu), "投融资与公司"),
        "IT之家财经": _to_section(s(ithome), "科技财经"),
        "知乎热榜": _to_section(s(zhihu), "热议话题"),
        "Google财经": _to_section(s(gnews_fin), "聚合快讯"),
        "路透财经": _to_section(s(reuters_biz), "国际财经"),
    })
    tech_sections = _compact_sections({
        "百度热搜": _to_section(s(baidu), "实时热点"),
        "IT之家": _to_section(s(ithome), "科技资讯"),
        "虎嗅": _to_section(s(huxiu), "行业动态"),
        "solidot": _to_section(s(solidot), "开源/极客"),
        "新华科技": _to_section(s(xinhua), "科技要闻"),
        "路透科技": _to_section(s(reuters_tech), "国际科技"),
        "TechCrunch": _to_section(s(techcrunch), "全球创投"),
        "The Verge": _to_section(s(theverge), "消费科技"),
        "CNBeta": _to_section(s(cnbeta), "科技门户"),
        "Google科技": _to_section(s(gnews_tech), "聚合快讯"),
    })
    ai_sections = _compact_sections({
        "开源中国": _to_section(s(oschina), "AI开源"),
        "B站热搜": _to_section(s(bilibili), "AI热词"),
        "虎嗅AI": _to_section(s(huxiu), "AI动态"),
        "arXiv AI": _to_section(s(arxiv_ai), "学术前沿"),
        "VentureBeat AI": _to_section(s(venturebeat_ai), "产业应用"),
        "IT之家AI": _to_section(s(ithome), "国内AI"),
        "Google AI": _to_section(s(gnews_ai), "聚合快讯"),
    })
    politics_sections = _compact_sections({
        "新浪要闻": _to_section(s(sina), "国内时政"),
        "澎湃新闻": _to_section(s(pengpai), "深度报道"),
        "路透时政": _to_section(s(reuters_world), "全球时政"),
        "BBC World": _to_section(s(bbc_world), "国际要闻"),
        "Google时政": _to_section(s(gnews_politics), "时政快讯"),
        "Google政策": _to_section(s(gnews_policy), "政策解读"),
        "Google国际政治": _to_section(s(gnews_world_politics), "国际政治"),
    })

    return {
        "savedAt": datetime.utcnow().isoformat() + "Z",
        "categories": {
            "finance": {
                "sections": finance_sections
            },
            "tech": {
                "sections": tech_sections
            },
            "ai": {
                "sections": ai_sections
            },
            "politics": {
                "sections": politics_sections
            },
        }
    }
