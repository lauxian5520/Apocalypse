"""Accepted conference papers from the conferences' public programmes/proceedings."""
import asyncio
import re
import json
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, SoupStrainer

from scrapers.conference_catalog import CONFERENCE_REGISTRY, CONFERENCES, held_in_year

# Broad topic hints rather than an asserted official track assignment.
AREA_RULES = {
    'llm': ('大语言模型与自然语言处理', r'\b(llm[s]?|language|linguistic|text|translation|reasoning|retrieval.augmented|dialog|prompt|token)\b'),
    'vision': ('计算机视觉与多模态', r'\b(vision|visual|image|video|multimodal|multi.modal|3d|object detection|segmentation)\b'),
    'rl': ('强化学习与决策', r'\b(reinforcement|bandit|policy|reward|decision|planning|multi.agent|game)\b'),
    'robotics': ('机器人与具身智能', r'\b(robot[s]?|robotic[s]?|embodied|manipulation|locomotion|navigation|autonomous)\b'),
    'generative': ('生成模型', r'\b(diffusion|generative|generation|flow matching|gan[s]?|vae[s]?)\b'),
    'graph': ('图学习与数据挖掘', r'\b(graph[s]?|recommendation|recommender|knowledge graph|data mining|time.series|forecasting)\b'),
    'security': ('可信AI与安全', r'\b(safety|safe|security|privacy|fairness|fair|robust|robustness|adversarial|alignment|unlearning|backdoor|jailbreak)\b'),
    'science': ('AI for Science', r'\b(science|scientific|protein|molecular|molecule[s]?|drug|medical|clinical|biology|biological|physics|climate|healthcare)\b'),
    'theory': ('机器学习理论与优化', r'\b(theory|theoretical|convergence|generalization|optimization|gradient|regret|bayesian|causal|statistical)\b'),
}
AREAS = {key: value[0] for key, value in AREA_RULES.items()} | {'other': '其他机器学习与人工智能'}


def classify_areas(title: str, keywords: str = '') -> list[str]:
    text = f'{title} {keywords}'.lower()
    return [key for key, (_, pattern) in AREA_RULES.items() if re.search(pattern, text)] or ['other']


def paper(title, url, venue, year, *, authors=None, abstract='', keywords=''):
    return {'title': title, 'url': url, 'conference': CONFERENCES[venue], 'year': year,
            'authors': authors or [], 'abstract': abstract, 'source': '会议官方论文集',
            'areas': classify_areas(title, keywords)}


def parse_programme(html: str, base: str, venue: str, year: int) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    # Server-rendered fallback exists even when the optional programme JSON is unavailable.
    papers = {}
    for link in soup.select(f'a[href^="/virtual/{year}/poster/"]'):
        url = urljoin(base, link['href'])
        title = link.get_text(' ', strip=True)
        if title and url not in papers:
            papers[url] = paper(title, url, venue, year)
    return list(papers.values())


def parse_aaai_issue(html: str, year: int) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    for entry in soup.select('.obj_article_summary'):
        link = entry.select_one('h3.title a')
        if not link:
            continue
        authors = entry.select_one('.authors')
        results.append(paper(link.get_text(' ', strip=True), link['href'], 'aaai', year,
                             authors=authors.get_text(' ', strip=True).split(', ') if authors else []))
    return results


def parse_programme_data(data: dict, base: str, venue: str, year: int) -> list[dict]:
    papers = {}
    for entry in data.get('results', []):
        title = entry.get('name', '').strip()
        path = entry.get('virtualsite_url') or ''
        if not title or f'/virtual/{year}/' not in path or 'poster/' not in path:
            continue
        if 'reject' in (entry.get('decision') or '').lower():
            continue
        url = urljoin(base, path)
        keywords = ' '.join(str(k) for k in entry.get('keywords') or [])
        keywords += ' ' + (entry.get('topic') or '')
        papers[url] = paper(title, url, venue, year,
            authors=[a['fullname'] for a in entry.get('authors', []) if a.get('fullname')],
            abstract=entry.get('abstract') or '', keywords=keywords)
    return list(papers.values())


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


def _text(node) -> str:
    # Preserve adjacent inline spans (e.g. ACL's case-preserving title spans).
    return ' '.join(node.get_text().split()) if node else ''


def parse_vision_proceedings(html: str, base: str, venue: str, year: int) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results = {}
    if venue == 'eccv':
        path_pattern = re.compile(rf'/papers/eccv_{year}/papers_ECCV/html/[^/]+\.php$')
    else:
        name = CONFERENCES[venue]
        path_pattern = re.compile(rf'/(?:content/{name}{year}|content_{name}_{year})/html/[^/]+\.html$')
    for entry in soup.select('dt.ptitle'):
        link = entry.find('a', href=True)
        if not link:
            continue
        url = urljoin(base, link['href'])
        if not path_pattern.fullmatch(urlsplit(url).path):
            continue  # Exclude other years and workshop proceedings.
        author_row = entry.find_next_sibling('dd')
        author_inputs = author_row.select('input[name="query_author"]') if author_row else []
        authors = [node.get('value', '').strip() for node in author_inputs]
        if not authors:
            authors = [name.strip().rstrip('*') for name in _text(author_row).split(',') if name.strip()]
        title = _text(link)
        if title:
            results[url] = paper(title, url, venue, year, authors=[name for name in authors if name])
    return list(results.values())


def parse_anthology(html: str, venue: str, year: int) -> list[dict]:
    # Event pages include workshops and Findings. Only main long/short papers qualify.
    # Straining the paper rows avoids building the much larger event-page DOM.
    soup = BeautifulSoup(html, 'html.parser', parse_only=SoupStrainer(['div', 'p'], class_=re.compile(r'\bd-sm-flex\b')))
    path_pattern = re.compile(rf'/{year}\.{venue}-(?:main|long|short)\.[1-9]\d*/$')
    results = {}
    for row in soup.select('.d-sm-flex'):
        link = next((a for a in row.select('strong a[href]') if path_pattern.fullmatch(urlsplit(a['href']).path)), None)
        if not link:
            continue
        url = urljoin('https://aclanthology.org/', link['href'])
        title = _text(link)
        authors = [_text(a) for a in row.select('a[href]') if urlsplit(a['href']).path.startswith('/people/')]
        abstract = row.select_one('.collapse .card-body')
        if title:
            results[url] = paper(title, url, venue, year, authors=authors, abstract=_text(abstract))
    return list(results.values())


def parse_ijcai(html: str, year: int) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results = {}
    base = f'https://www.ijcai.org/proceedings/{year}/'
    for entry in soup.select('.paper_wrapper'):
        section = entry.find_parent(class_='section')
        track = _text(section.select_one('.section_title')) if section else ''
        if re.search(r'doctoral|demo|journal|sister conferences', track, re.I):
            continue
        link = next((a for a in entry.select('.details a[href]') if re.fullmatch(rf'/proceedings/{year}/\d+/?', urlsplit(a['href']).path)), None)
        title = _text(entry.select_one('.title'))
        if link and title:
            url = urljoin(base, link['href'])
            authors = [name.strip() for name in _text(entry.select_one('.authors')).split(',') if name.strip()]
            results[url] = paper(title, url, 'ijcai', year, authors=authors, keywords=track)
    return list(results.values())


def pmlr_volumes(html: str, venue: str, year: int) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    pattern = re.compile(rf'\bProceedings of\s+{re.escape(CONFERENCES[venue])}\s+{year}\s*$', re.I)
    for row in soup.select('li'):
        if not pattern.search(_text(row)):
            continue
        link = row.find('a', href=re.compile(r'^/?v\d+/?$'))
        if link:
            results.append(urljoin('https://proceedings.mlr.press/', link['href']).rstrip('/') + '/')
    return list(dict.fromkeys(results))


def parse_pmlr(html: str, base: str, venue: str, year: int) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results = {}
    for entry in soup.select('.paper'):
        link = next((a for a in entry.select('.links a[href]') if _text(a).lower() == 'abs'), None)
        title = _text(entry.select_one('.title'))
        if link and title:
            url = urljoin(base, link['href'])
            authors = [name.strip() for name in _text(entry.select_one('.authors')).split(',') if name.strip()]
            results[url] = paper(title, url, venue, year, authors=authors)
    return list(results.values())


async def _proceedings_page(client: httpx.AsyncClient, url: str) -> str:
    """An unpublished year's directory is empty; other failures retain stale caches."""
    response = await client.get(url)
    if response.status_code == 404:
        return ''
    response.raise_for_status()
    return response.text


async def fetch(venue: str, year: int) -> list[dict]:
    if not held_in_year(venue, year):
        return []
    source = CONFERENCE_REGISTRY[venue]['source']
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        if source == 'cvf':
            base = 'https://openaccess.thecvf.com/'
            html = await _proceedings_page(client, f'{base}{CONFERENCES[venue]}{year}?day=all')
            return await asyncio.to_thread(parse_vision_proceedings, html, base, venue, year)
        if source == 'ecva':
            base = 'https://www.ecva.net/'
            html = await _get(client, f'{base}papers.php')
            return await asyncio.to_thread(parse_vision_proceedings, html, base, venue, year)
        if source == 'anthology':
            html = await _proceedings_page(client, f'https://aclanthology.org/events/{venue}-{year}/')
            return await asyncio.to_thread(parse_anthology, html, venue, year)
        if source == 'ijcai':
            html = await _proceedings_page(client, f'https://www.ijcai.org/proceedings/{year}/')
            return await asyncio.to_thread(parse_ijcai, html, year)
        if source == 'pmlr':
            index = await _get(client, 'https://proceedings.mlr.press/')
            volumes = await asyncio.to_thread(pmlr_volumes, index, venue, year)
            pages = []
            for url in volumes:
                html = await _get(client, url)
                pages.extend(await asyncio.to_thread(parse_pmlr, html, url, venue, year))
            return list({p['url']: p for p in pages}.values())
        if source == 'virtual':
            base = f'https://{venue}.cc'
            try:
                raw = await _get(client, f'{base}/static/virtual/data/{venue}-{year}-orals-posters.json')
                papers = parse_programme_data(json.loads(raw), base, venue, year)
                if papers:
                    return papers
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                pass
            html = await _get(client, f'{base}/virtual/{year}/papers.html')
            return parse_programme(html, base, venue, year)

        if source != 'aaai':
            raise ValueError(f'Unsupported proceedings source: {source}')
        archive = 'https://ojs.aaai.org/index.php/AAAI/issue/archive'
        issues = {}
        # OJS paginates volumes: follow links until the requested year is passed.
        for page_no in range(1, 21):
            html = await _get(client, archive if page_no == 1 else f'{archive}/{page_no}')
            soup = BeautifulSoup(html, 'html.parser')
            years = []
            for link in soup.select('.obj_issue_summary a.title'):
                label = link.get_text(' ', strip=True)
                match = re.search(r'AAAI-(\d{2})\b', label)
                if not match:
                    continue
                issue_year = 2000 + int(match[1])
                years.append(issue_year)
                if issue_year == year and 'Technical Track' in label:
                    issues[link['href']] = label
            if not years or min(years) < year:
                break
        semaphore = asyncio.Semaphore(4)

        async def load_issue(url):
            async with semaphore:
                return parse_aaai_issue(await _get(client, url), year)

        # Do not cache a silently incomplete volume if any issue failed.
        pages = await asyncio.gather(*(load_issue(url) for url in issues))
        return list({p['url']: p for page in pages for p in page}.values())
