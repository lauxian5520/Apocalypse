"""Refresh the hot-topic feed and summarise what was cached.

    cd backend && python ../tools/check_feeds.py
"""
import asyncio
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.feed_service import feed_path, refresh_focus  # noqa: E402


async def check_offline() -> None:
    """Exercise freshness, cache recovery and paper invalidation without networking."""
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    import tempfile
    from unittest.mock import AsyncMock, patch
    import httpx
    from core.config import Settings
    from scrapers import hf_papers, tophub
    from services import classify_service, conference_service, feed_service as feeds

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(_env_file=None, app_env='test', feeds_dir=tmp,
                            feeds_enabled=True, feeds_initial_wait_seconds=0)
        with patch.object(feeds, 'settings', settings), patch.object(tophub, 'get_settings', return_value=settings):
            now = datetime.now(timezone.utc)
            recent = format_datetime(now - timedelta(hours=1))
            old = format_datetime(now - timedelta(hours=settings.focus_max_age_hours + 1))
            xml = f'''<rss><channel>
                <item><title>recent headline</title><link>https://example.invalid/new</link><pubDate>{recent}</pubDate></item>
                <item><title>old headline</title><link>https://example.invalid/old</link><pubDate>{old}</pubDate></item>
                <item><title>undated headline</title><link>https://example.invalid/unknown</link></item>
                </channel></rss>'''
            items = tophub._parse_rss(xml)
            assert len(items) == 1 and items[0]['title'] == 'recent headline'
            assert items[0]['published_at']
            atom = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom news</title>
                <link href="https://example.invalid/atom" rel="alternate"/>
                <published>{now.isoformat()}</published></entry></feed>'''
            assert tophub._parse_rss(atom)[0]['url'] == 'https://example.invalid/atom'
            assert not tophub._clean_items([{'title': 'unverified', 'url': 'https://example.invalid'}])
            print('PASS RSS/Atom dates, old/undated removal and publication metadata')

            feeds._write_json('focus', {'savedAt': now.isoformat(), 'categories': {
                'finance': {'sections': {'source': [{'items': [
                    *items, {'title': 'legacy old', 'url': 'https://example.invalid/old'}]}]}}
            }})
            assert len(feeds.get_focus()['categories']['finance']['sections']['source'][0]['items']) == 1
            assert not feeds.has_cached_feeds(), 'One focus file must not hide missing project/paper feeds'
            feeds._save('papers_combined_daily', {'hf': items, 'scholar': []})
            with patch.object(settings, 'feeds_enabled', False):
                assert (await feeds.current_hf_papers('daily'))['data'] == items
            Path(feeds.feed_path('broken')).write_text('{partial', encoding='utf-8')
            assert feeds._load('broken')['data'] == []

            gate = asyncio.Event()
            calls = 0

            async def fresh() -> None:
                """Hold one refresh to simulate concurrent cold page requests."""
                nonlocal calls
                calls += 1
                await gate.wait()
                feeds._save_nonempty('fixture', [{'title': 'fresh'}])

            responses = await asyncio.gather(*(feeds.ensure_cached('fixture', fresh) for _ in range(5)))
            assert all(response['refreshing'] for response in responses)
            await asyncio.sleep(0)
            assert calls == 1
            gate.set()
            await feeds._refresh_tasks['fixture']
            assert (await feeds.ensure_cached('fixture', fresh))['status'] == 'ready'

            feeds._write_json('fixture', {'updated_at': '2020-01-01T00:00:00+00:00', 'data': [{'title': 'good cache'}]})
            failing = AsyncMock(side_effect=RuntimeError('fixture outage'))
            await feeds.ensure_cached('fixture', failing)
            await feeds._refresh_tasks['fixture']
            response = await feeds.ensure_cached('fixture', failing)
            assert response['status'] == 'error' and failing.await_count == 1
            assert response['data'][0]['title'] == 'good cache'
            assert response['updated_at'].startswith('2020-01-01')
            print('PASS incomplete/corrupt/legacy caches, refresh coalescing, outage preservation and retry backoff')

            classifier = AsyncMock(return_value={'categories': []})
            with patch.object(classify_service.ai_service, 'active_model', return_value='fixture'), \
                    patch.object(classify_service.ai_service, 'classify_trending_items', classifier):
                await classify_service.classify('papers', {'updated_at': 'first', 'data': []}, 'daily')
                result = await classify_service.classify('papers', {'updated_at': 'first', 'data': items}, 'daily')
                assert result['items'] == items
                await classify_service.classify('papers', {'updated_at': 'second', 'data': items}, 'daily')
                assert classifier.await_count == 3
            print('PASS initially empty and subsequently refreshed paper classification caches')

            queries = []

            def source(request: httpx.Request) -> httpx.Response:
                """Serve official-shaped paper entries with distinct votes/comments."""
                queries.append(dict(request.url.params))
                return httpx.Response(200, json=[{'numComments': 100, 'paper': {
                    'id': 'fixture', 'title': 'Recent paper', 'summary': 'summary', 'upvotes': 3,
                    'publishedAt': now.isoformat(), 'submittedOnDailyAt': now.isoformat(),
                    'authors': [{'name': 'Fixture Author'}]}}])

            original_client = httpx.AsyncClient
            with patch.object(hf_papers.httpx, 'AsyncClient', side_effect=lambda **kwargs:
                              original_client(transport=httpx.MockTransport(source), **kwargs)):
                for period in feeds.PERIODS:
                    queries.clear()
                    result = await hf_papers.fetch(period)
                    assert result[0]['upvotes'] == 3
                    assert result[0]['published_at'] and result[0]['authors'] == ['Fixture Author']
                    assert len(queries) <= 7
                    if period == 'daily':
                        assert 'date' not in queries[0]
                    elif period == 'weekly':
                        assert 'week' in queries[0]
                    else:
                        assert 'month' in queries[0]
            print('PASS latest issue, official week/month queries, bounded half-year queries and vote counts')

            # Run the registered periodic callback without opening a paper page.
            stale = {'updated_at': '2020-01-01T00:00:00+00:00', 'data': [{'title': 'previous directory'}]}
            for venue in ('iclr', 'acl', 'icml'):
                feeds._write_json(f'conference_{venue}_2025', stale)
            feeds._write_json('conference_iclr_2025', {**stale, 'updated_at': 'invalid'})
            feeds._save('conference_cvpr_2025', [{'title': 'fresh directory'}])
            for name in ('conference_unknown_2025', 'conference_eccv_2025',
                         'conference_iclr_invalid', 'conference_iclr_9999'):
                feeds._write_json(name, stale)

            async def proceedings(venue, year):
                assert year == 2025
                if venue == 'acl':
                    raise httpx.ConnectError('fixture outage')
                return [] if venue == 'icml' else [{'title': 'updated directory'}]

            fetch = AsyncMock(side_effect=proceedings)
            with patch.object(conference_service, 'fetch', fetch), \
                    patch.object(feeds, 'refresh_github', AsyncMock()), \
                    patch.object(feeds, 'refresh_huggingface', AsyncMock()), \
                    patch.object(feeds, 'refresh_focus', AsyncMock()), \
                    patch.object(feeds, 'refresh_papers', AsyncMock()):
                scheduler = feeds.start_scheduler()
                try:
                    job = scheduler.get_job('refresh_all')
                    assert job.trigger.interval.total_seconds() == settings.feeds_cache_ttl_minutes * 60
                    assert job.func == feeds.refresh_all
                    await asyncio.gather(job.func(), job.func())
                finally:
                    scheduler.shutdown(wait=False)
                assert {call.args for call in fetch.await_args_list} == {
                    ('iclr', 2025), ('acl', 2025), ('icml', 2025)}
                assert fetch.await_count == 3, 'Concurrent refreshes must share conference work'
                assert feeds._load('conference_iclr_2025')['data'] == [{'title': 'updated directory'}]
                assert feeds._load('conference_acl_2025') == stale, 'An outage must retain the successful cache'
                assert feeds._load('conference_icml_2025')['data'] == []
                assert feeds._load('conference_icml_2025')['updated_at'] != stale['updated_at']
                fetch.reset_mock()
                await feeds.refresh_all()
                fetch.assert_awaited_once_with('acl', 2025)
            print('PASS scheduled conference refresh without page visits, invalid timestamps, shared work, fresh/empty caches and outage isolation')
            await feeds.stop_refreshes()
            feeds._failures.clear()


async def main() -> None:
    print("Refreshing focus feed…")
    await refresh_focus()

    with open(feed_path("focus"), encoding="utf-8") as f:
        data = json.load(f)

    for category, payload in (data.get("categories") or {}).items():
        sections = payload.get("sections", {})
        total = sum(
            sum(len(section.get("items", [])) for section in group)
            for group in sections.values()
        )
        print(f"  {category:10} {len(sections):2} sources  ~{total} items")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Run deterministic freshness/cache regressions')
    args = parser.parse_args()
    asyncio.run(check_offline() if args.offline else main())
