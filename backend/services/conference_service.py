"""Per-conference disk caching, filtering and pagination for accepted papers."""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from scrapers.conference_papers import AREAS, CONFERENCES, fetch
from scrapers.conference_catalog import conference_catalog, held_in_year
from services import feed_service
from services.feed_service import _load, _save

logger = logging.getLogger(__name__)
_locks: dict[tuple[str, int], asyncio.Lock] = {}
TTL_SECONDS = 6 * 3600
_fetch_slots = asyncio.Semaphore(4)


async def _conference(venue: str, year: int) -> tuple[list, str | None, str | None]:
    if not held_in_year(venue, year):
        return [], None, f'{CONFERENCES[venue]} {year} 未举办，请切换年份'
    async with _locks.setdefault((venue, year), asyncio.Lock()):
        name = f'conference_{venue}_{year}'
        cached = _load(name)
        updated = cached.get('updated_at')
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds()
        except (TypeError, ValueError):
            age = TTL_SECONDS
        if 0 <= age < TTL_SECONDS:
            return cached['data'], updated, None
        try:
            async with _fetch_slots:
                data = await fetch(venue, year)
            # Empty programmes may indicate an unpublished year; retry after the TTL.
            _save(name, data)
            return data, _load(name)['updated_at'], None
        except Exception as exc:
            logger.warning('Conference feed failed: %s %s (%s)', venue, year, type(exc).__name__)
            warning = f'{CONFERENCES[venue]} {year} 暂时无法获取'
            if cached.get('data'):
                warning += '，正在显示上次缓存'
            return cached.get('data') or [], updated, warning


async def refresh_cached_papers() -> None:
    """Keep previously requested conference directories fresh without page visits."""
    directories = []
    current_year = datetime.now(timezone.utc).year
    for path in Path(feed_service.settings.feeds_dir).glob('conference_*_*.json'):
        venue, _, year_text = path.stem.removeprefix('conference_').rpartition('_')
        if venue in CONFERENCES and year_text.isdigit():
            year = int(year_text)
            if 2020 <= year <= current_year and held_in_year(venue, year):
                directories.append(_conference(venue, year))
    await asyncio.gather(*directories)


async def get_papers(venue: str, year: int, area: str, query: str, offset: int, limit: int) -> dict:
    venues = [key for key in CONFERENCES if held_in_year(key, year)] if venue == 'all' else [venue]
    results = await asyncio.gather(*(_conference(v, year) for v in venues))
    items = [item for data, _, _ in results for item in data]
    if query.strip():
        needle = query.strip().casefold()
        items = [p for p in items if needle in (p['title'] + ' ' + ' '.join(p['authors'])).casefold()]
    counts = {key: sum(key in p['areas'] for p in items) for key in AREAS}
    if area != 'all':
        items = [p for p in items if area in p['areas']]
    items.sort(key=lambda p: (p['title'].casefold(), p['conference']))
    return {
        'items': items[offset:offset + limit], 'total': len(items),
        'offset': offset, 'limit': limit,
        'categories': [{'id': key, 'name': name, 'count': counts[key]} for key, name in AREAS.items()],
        'conferences': conference_catalog(),
        'warnings': [warning for _, _, warning in results if warning],
        'updated_at': min((date for _, date, _ in results if date), default=None),
        'classification': '根据论文标题、官方主题与关键词归类，跨领域论文可归入多个分类',
    }
