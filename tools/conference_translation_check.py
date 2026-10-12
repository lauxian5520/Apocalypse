"""Isolated acceptance for conference filtering and the DeepSeek translation tool.

Run: .venv/bin/python tools/conference_translation_check.py [--browser]
Uses synthetic proceedings and a local model; never changes the user's database.
"""
import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from check_support import ROOT, PASSWORD, login, server, test_env
from mock_provider import mock_provider

sys.path.insert(0, str(ROOT / 'backend'))
from scrapers.conference_papers import parse_programme, parse_programme_data, parse_aaai_issue
from scrapers.conference_papers import parse_vision_proceedings, parse_anthology, parse_ijcai, parse_pmlr, pmlr_volumes, fetch
from scrapers.conference_catalog import CONFERENCES, conference_catalog, held_in_year
from services import conference_service


def adapter_checks():
    cvf = '<dt class="ptitle"><a href="/content/CVPR2025/html/image.html">Visual image segmentation</a></dt><dd><input name="query_author" value="Visual Author"></dd>'
    workshop = cvf.replace('/CVPR2025/', '/CVPR2025_workshops/')
    vision = parse_vision_proceedings(cvf + cvf + workshop, 'https://openaccess.thecvf.com/', 'cvpr', 2025)
    assert len(vision) == 1 and vision[0]['authors'] == ['Visual Author'] and 'vision' in vision[0]['areas']
    old_cvf = cvf.replace('content/CVPR2025', 'content_CVPR_2020')
    assert len(parse_vision_proceedings(old_cvf, 'https://openaccess.thecvf.com/', 'cvpr', 2020)) == 1
    eccv = '<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/1_ECCV_2024_paper.php">Embodied visual navigation</a></dt><dd>Author A*, Author B</dd>'
    parsed = parse_vision_proceedings(eccv, 'https://www.ecva.net/', 'eccv', 2024)
    assert parsed[0]['authors'] == ['Author A', 'Author B']
    assert not parse_vision_proceedings(eccv, 'https://www.ecva.net/', 'eccv', 2022)
    anthology_row = '<div class="d-sm-flex align-items-stretch mb-3"><span><strong><a href="/2025.acl-long.1/"><span>E</span>com<span>S</span>criptBench</a></strong><a href="/people/example/">NLP Author</a></span></div>'
    anthology = anthology_row + anthology_row.replace('acl-long.1', 'acl-short.2')
    for excluded in ('acl-long.0', 'acl-demo.1', 'findings-acl.1', 'workshop.1'):
        anthology += anthology_row.replace('acl-long.1', excluded)
    parsed = parse_anthology(anthology, 'acl', 2025)
    assert len(parsed) == 2 and parsed[0]['title'] == 'EcomScriptBench' and parsed[0]['authors'] == ['NLP Author']
    for venue in ('emnlp', 'naacl'):
        assert len(parse_anthology(anthology_row.replace('acl-long', venue + '-main'), venue, 2025)) == 1
    ijcai_row = '<div class="section"><div class="section_title">Main Track</div><div class="paper_wrapper"><div class="title">Graph reasoning</div><div class="authors">Author A, Author B</div><div class="details"><a href="0001.pdf">PDF</a><a href="/proceedings/2025/1">Details</a></div></div></div>'
    assert len(parse_ijcai(ijcai_row + ijcai_row.replace('Main Track', 'Demo Track'), 2025)) == 1
    for venue in ('aistats', 'uai', 'colt', 'corl'):
        name = CONFERENCES[venue]
        index = f'<li><a href="v258">Volume 258</a> Proceedings of {name} 2025</li><li><a href="v100">Volume 100</a> Workshop at {name} 2025</li>'
        assert pmlr_volumes(index, venue, 2025) == ['https://proceedings.mlr.press/v258/']
        assert not pmlr_volumes(index, venue, 2024)
    mlr = '<div class="paper"><p class="title">Optimization theory</p><span class="authors">Author A, Author B</span><p class="links"><a href="https://proceedings.mlr.press/v258/test.html">abs</a><a href="test.pdf">PDF</a></p></div>'
    assert parse_pmlr(mlr, 'https://proceedings.mlr.press/v258/', 'aistats', 2025)[0]['authors'] == ['Author A', 'Author B']

    # Exercise source dispatch without public networking, including unpublished volumes.
    async def dispatch():
        requests = []
        def respond(request):
            url = str(request.url)
            requests.append(url)
            html = {
                'https://openaccess.thecvf.com/CVPR2025?day=all': cvf,
                'https://www.ecva.net/papers.php': eccv,
                'https://aclanthology.org/events/acl-2025/': anthology,
                'https://www.ijcai.org/proceedings/2025/': ijcai_row,
                'https://proceedings.mlr.press/': '<li><a href="v258">Volume 258</a> Proceedings of AISTATS 2025</li>',
                'https://proceedings.mlr.press/v258/': mlr,
            }.get(url)
            return httpx.Response(200, text=html, request=request) if html is not None else httpx.Response(404, request=request)
        original_client = httpx.AsyncClient
        with patch('scrapers.conference_papers.httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
            for venue, year in [('cvpr', 2025), ('eccv', 2024), ('acl', 2025), ('ijcai', 2025), ('aistats', 2025)]:
                assert await fetch(venue, year), venue
            assert not await fetch('cvpr', 2026)
            before = len(requests)
            assert not await fetch('eccv', 2025)
            assert len(requests) == before
    asyncio.run(dispatch())
    assert held_in_year('iccv', 2025) and not held_in_year('iccv', 2024)
    assert held_in_year('eccv', 2024) and not held_in_year('eccv', 2025)
    assert {'cvpr', 'iccv', 'eccv', 'acl', 'emnlp', 'naacl', 'ijcai', 'aistats', 'uai', 'colt', 'corl'}.issubset({entry['id'] for entry in conference_catalog()})
    print('PASS new official-source adapters, main/workshop/year boundaries, title spans, authors, dispatch and biennial calendars')


def fixtures(root):
    year = datetime.now().year - 1
    html = f'<li><a href="/virtual/{year}/poster/1">Graph neural networks</a></li>'
    assert len(parse_programme(html + html, 'https://iclr.cc', 'iclr', year)) == 1
    data = {'results': [
        {'name': 'Language model safety', 'virtualsite_url': f'/virtual/{year}/poster/2',
         'authors': [{'fullname': 'Example Author'}], 'abstract': 'A synthetic abstract.', 'decision': 'Accept (Poster)'},
        {'name': 'Rejected language model', 'virtualsite_url': f'/virtual/{year}/poster/3', 'decision': 'Reject'},
    ]}
    parsed = parse_programme_data(data, 'https://iclr.cc', 'iclr', year)
    assert len(parsed) == 1 and {'llm', 'security'}.issubset(parsed[0]['areas'])
    assert parsed[0]['authors'] == ['Example Author']
    aaai = '<div class="obj_article_summary"><h3 class="title"><a href="https://ojs.aaai.org/index.php/AAAI/article/view/1">Robot navigation</a></h3><div class="authors">Author A, Author B</div></div>'
    assert parse_aaai_issue(aaai, year)[0]['authors'] == ['Author A', 'Author B']
    feed_dir = Path(root) / 'feeds'
    feed_dir.mkdir(parents=True, exist_ok=True)
    cvf = f'<dt class="ptitle"><a href="/content/CVPR{year}/html/image.html">Visual image segmentation</a></dt><dd><input name="query_author" value="Visual Author"></dd>'
    for venue in CONFERENCES:
        papers = parsed + parse_programme(html, f'https://{venue}.cc', venue, year) if venue == 'iclr' else []
        if venue == 'cvpr':
            papers = parse_vision_proceedings(cvf, 'https://openaccess.thecvf.com/', venue, year)
        (feed_dir / f'conference_{venue}_{year}.json').write_text(json.dumps({
            'updated_at': datetime.now(timezone.utc).isoformat(), 'data': papers,
        }))
    eccv_year = year if year % 2 == 0 else year - 1
    eccv = f'<dt class="ptitle"><a href="papers/eccv_{eccv_year}/papers_ECCV/html/1_ECCV_{eccv_year}_paper.php">ECCV visual navigation</a></dt><dd>ECCV Author</dd>'
    (feed_dir / f'conference_eccv_{eccv_year}.json').write_text(json.dumps({
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'data': parse_vision_proceedings(eccv, 'https://www.ecva.net/', 'eccv', eccv_year),
    }))
    return year, parsed


def cache_failure_check(parsed):
    async def failing_fetch(*_):
        raise httpx.ConnectError('fixture outage')
    stale = {'updated_at': '2020-01-01T00:00:00+00:00', 'data': parsed}
    with patch.object(conference_service, '_load', return_value=stale), patch.object(conference_service, 'fetch', failing_fetch):
        items, updated, warning = asyncio.run(conference_service._conference('iclr', 2025))
        assert items == parsed and updated == stale['updated_at'] and '缓存' in warning
    print('PASS source parsing, deduplication, multi-area classification and stale-cache fallback')


def browser_check(base):
    from playwright.sync_api import sync_playwright, expect
    expected_total = 3 + int((datetime.now().year - 1) % 2 == 0)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        try:
            for width, height in ((1440, 1000), (390, 844)):
                context = browser.new_context(viewport={'width': width, 'height': height})
                page = context.new_page()
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/login.html')
                page.locator('[name=username]').fill('admin')
                page.locator('[name=password]').fill(PASSWORD)
                page.locator('#login-form button[type=submit]').click()
                page.wait_for_url('**/memos.html')
                page.goto(base + '/papers.html')
                page.locator('[data-source=conferences]').click()
                expect(page.locator('#conference-status')).to_contain_text(f'共 {expected_total} 篇')
                expect(page.locator('#conference-venue option')).to_have_count(len(CONFERENCES) + 1)
                page.locator('#conference-venue').select_option('cvpr')
                expect(page.locator('#conference-status')).to_contain_text('共 1 篇')
                expect(page.locator('#papers-grid')).to_contain_text('Visual image segmentation')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'conference overflow'
                output = ROOT / 'artifacts/features'
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'conference-cvpr-{width}.png'))
                page.locator('#conference-venue').select_option('all')
                expect(page.locator('#conference-status')).to_contain_text(f'共 {expected_total} 篇')
                expect(page.locator('#papers-grid')).to_contain_text('Language model safety')
                page.locator('[data-area=graph]').click()
                expect(page.locator('#conference-status')).to_contain_text('共 1 篇')
                expect(page.locator('#papers-grid')).to_contain_text('Graph neural networks')
                page.locator('#conference-query').fill('nonexistent')
                expect(page.locator('#papers-grid')).to_contain_text('暂无符合条件')
                page.locator('#conference-query').fill('')
                page.locator('[data-area=all]').click()
                page.locator('#conference-venue').select_option('eccv')
                expect(page.locator('#papers-grid')).to_contain_text('ECCV visual navigation')
                years = page.locator('#conference-year option').evaluate_all('(items) => items.map(item => Number(item.value))')
                assert years and all(year % 2 == 0 for year in years)
                page.locator('[data-source=arxiv]').click()
                expect(page.locator('#conference-controls')).to_be_hidden()
                expect(page.locator('#period-tabs')).to_be_visible()
                page.goto(base + '/tools.html')
                expect(page.locator('.tool-card').filter(has_text='实时翻译')).to_contain_text('大语言模型站内多语言翻译')
                page.locator('.tool-card').filter(has_text='实时翻译').click()
                expect(page.locator('#translation-input')).to_be_visible()
                page.locator('#translation-input').fill('Hello')
                page.locator('#translation-submit').click()
                expect(page.locator('#translation-output')).to_have_value('mock complete')
                expect(page.locator('#translation-copy')).to_be_enabled()
                page.locator('#translation-source').select_option('en')
                page.locator('#translation-swap').click()
                expect(page.locator('#translation-target')).to_have_value('en')
                expect(page.locator('#translation-input')).to_have_value('mock complete')
                page.locator('#translation-input').fill('mock-error')
                page.locator('#translation-submit').click()
                expect(page.locator('#translation-status')).to_contain_text('翻译失败')
                expect(page.locator('#translation-submit')).to_be_enabled()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal overflow'
                assert not errors, errors
                output = ROOT / 'artifacts/features'
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'translation-{width}.png'))
                context.close()
                print(f'PASS browser conference filters, translation, recovery and layout at {width}px')
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    adapter_checks()
    with tempfile.TemporaryDirectory() as tmp, mock_provider() as endpoint:
        year, parsed = fixtures(tmp)
        cache_failure_check(parsed)
        env = test_env(tmp, DEEPSEEK_API_URL=endpoint, DEEPSEEK_API_KEY='fixture-only', DEEPSEEK_MODEL='mock')
        with server(root=tmp, env=env) as (base, _, _):
            with closing(login(base, 'admin')) as client:
                response = client.get('/api/feeds/papers/conferences', params={'year': year, 'limit': 1})
                assert response.status_code == 200, response.text
                assert response.json()['total'] == 3 + int(year % 2 == 0) and len(response.json()['items']) == 1
                metadata = client.get('/api/feeds/papers/conferences/catalog').json()['conferences']
                assert {entry['id'] for entry in metadata} == set(CONFERENCES)
                assert client.get('/api/feeds/papers/conferences', params={'venue': 'cvpr', 'year': year}).json()['total'] == 1
                assert client.get('/api/feeds/papers/conferences', params={'venue': 'acl', 'year': year}).status_code == 200
                for entry in metadata:
                    assert all(held_in_year(entry['id'], y) for y in entry['years'])
                unheld = year if year % 2 else year - 1
                data = client.get('/api/feeds/papers/conferences', params={'venue': 'eccv', 'year': unheld}).json()
                assert data['total'] == 0 and '未举办' in data['warnings'][0]
                assert client.get('/api/feeds/papers/conferences', params={'year': year, 'area': 'graph'}).json()['total'] == 1
                assert client.get('/api/feeds/papers/conferences', params={'year': year, 'q': 'Example Author'}).json()['total'] == 1
                assert client.get('/api/feeds/papers/conferences?venue=bogus').status_code == 422
                assert client.get('/api/feeds/papers/conferences?year=2019').status_code == 422
                assert client.get('/api/feeds/papers/conferences?offset=-1').status_code == 422
                print('PASS conference category/author filters, pagination and input bounds')
                payload = {'text': 'Hello', 'source_language': 'en', 'target_language': 'zh'}
                response = client.post('/api/ai/translate', json=payload)
                assert response.status_code == 200 and response.json()['translation'] == 'mock complete', response.text
                # AI_PROVIDER is intentionally custom with an unreachable URL, proving translation chooses DeepSeek.
                for changes in ({'text': ' '}, {'text': 'a' * 4001}, {'target_language': 'auto'}, {'source_language': 'bogus'}):
                    assert client.post('/api/ai/translate', json=payload | changes).status_code == 422
                assert httpx.post(base + '/api/ai/translate', json=payload, trust_env=False).status_code == 401
                csrf = client.headers.pop('X-CSRF-Token')
                assert client.post('/api/ai/translate', json=payload).status_code == 403
                client.headers['X-CSRF-Token'] = csrf
                assert client.post('/api/ai/translate', json=payload | {'text': 'mock-error'}).status_code == 502
                print('PASS forced DeepSeek routing, translation validation, authentication, CSRF and upstream failure')
            if args.browser:
                browser_check(base)


if __name__ == '__main__':
    main()
