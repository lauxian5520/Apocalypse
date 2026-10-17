"""Acceptance for memo keyword search on isolated data and real HTTP endpoints.

Run: .venv/bin/python tools/memo_search_check.py [--browser]
Browser checks require Playwright and /usr/bin/chromium.
"""
import argparse
from contextlib import ExitStack
from urllib.parse import parse_qs, urlparse

import httpx

from check_support import PASSWORD, ROOT, login, server


def api_checks(base):
    with ExitStack() as stack:
        clients = {name: login(base, name) for name in ('owner', 'other', 'admin')}
        for client in clients.values():
            stack.callback(client.close)
        clients['anonymous'] = stack.enter_context(httpx.Client(base_url=base, trust_env=False))

        def create(content, visibility='public', author='owner', anonymous=False):
            response = clients[author].post('/api/memos', data={
                'content': content, 'visibility': visibility, 'is_anonymous': str(anonymous).lower(),
            })
            assert response.status_code == 201, response.text
            return response.json()

        matches = [create(f'分布式系统 Keyword batch {i}') for i in range(12)]
        assert clients['owner'].patch(f"/api/memos/{matches[0]['id']}", json={'pinned': True}).status_code == 200
        chinese = create('中文学习笔记 😀')
        literal = create('100%_done /? 与空格')
        anon = create('匿名公开 Keyword', anonymous=True)
        create('<script>window.searchInjected=true</script> Keyword')
        owner_private = create('机密 Keyword owner', 'private')
        other_private = create('机密 Keyword other', 'private', 'other')
        guest = clients['anonymous']

        def query(client=guest, **params):
            response = client.get('/api/memos', params=params)
            assert response.status_code == 200, response.text
            return response.json()

        total = query()['total']
        assert total == 16
        assert query(q='   ')['total'] == total
        assert query(q=' 中文 ')['items'][0]['id'] == chinese['id']
        for keyword in ['%', '_', '100%_done /?', '/?']:
            found = query(q=keyword)
            assert found['total'] == 1 and found['items'][0]['id'] == literal['id'], (keyword, found)
        assert query(q='missing')['total'] == query(q="' OR 1=1 --")['total'] == 0
        first = query(q='keyword', page=1, page_size=10)
        second = query(q='keyword', page=2, page_size=10)
        assert first['total'] == second['total'] == 14
        assert first['items'][0]['id'] == matches[0]['id']
        ids = [memo['id'] for memo in first['items'] + second['items']]
        assert len(ids) == len(set(ids)) == 14
        assert query(q='keyword', page=3, page_size=10)['items'] == []
        assert query(q='匿名公开')['items'][0]['user_id'] is None
        assert query(q='admin')['total'] == 0  # Only memo body participates in search.
        assert guest.get('/api/memos', params={'q': 'x' * 201}).status_code == 422
        print('PASS real API Chinese/emoji, case-insensitive keywords, whitespace, literal SQL wildcards, pagination and anonymity')

        for name, client in clients.items():
            response = client.get('/api/memos', params={'visibility': 'private', 'q': '机密'})
            if name == 'anonymous':
                assert response.status_code == 403
            else:
                data = response.json()
                expected = {'owner': [owner_private['id']], 'other': [other_private['id']], 'admin': [other_private['id'], owner_private['id']]}[name]
                assert response.status_code == 200 and [memo['id'] for memo in data['items']] == expected
            assert query(client, q='机密')['total'] == 0
        print('PASS private search ACL for guest/owner/other/admin and filtered totals')


def browser_checks(base):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        try:
            for width, height in ((1440, 1000), (390, 844)):
                context = browser.new_context(viewport={'width': width, 'height': height})
                page = context.new_page()
                errors, queries = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
                page.on('request', lambda request: queries.append(parse_qs(urlparse(request.url).query)) if urlparse(request.url).path == '/api/memos' else None)
                page.goto(base + '/memos.html')
                cards = page.locator('.memo-card')
                search = page.locator('#memo-search')
                status = page.locator('#memo-search-status')
                expect(cards).to_have_count(10)

                before = len(queries)
                search.fill('K')
                search.fill('Key')
                search.fill('Keyword')
                expect(status).to_have_text('共找到 14 条匹配笔记')
                assert len(queries) == before + 1, 'debounce sent duplicate requests'
                expect(cards).to_have_count(10)
                def fail_page(route):
                    if parse_qs(urlparse(route.request.url).query).get('page') == ['2']:
                        route.fulfill(status=503, json={'detail': 'Fixture outage'})
                    else:
                        route.continue_()
                page.route('**/api/memos?**', fail_page)
                page.locator('#load-more-btn').click()
                expect(status).to_contain_text('加载失败')
                expect(cards).to_have_count(10)
                expect(page.locator('#load-more-btn')).to_be_enabled()
                page.unroute('**/api/memos?**', fail_page)
                page.locator('#load-more-btn').click()
                expect(cards).to_have_count(14)
                expect(page.locator('#load-more-btn')).to_be_hidden()
                assert queries[-1]['q'] == ['Keyword'] and queries[-1]['page'] == ['2']
                assert not page.evaluate('Boolean(window.searchInjected)')

                search.fill('中文')
                search.press('Enter')
                expect(status).to_have_text('共找到 1 条匹配笔记')
                expect(cards).to_have_count(1)
                expect(cards).to_contain_text('中文学习笔记 😀')
                assert queries[-1]['page'] == ['1']
                count = len(queries)
                page.wait_for_timeout(400)
                assert len(queries) == count, 'Enter left a pending debounce request'
                for keyword, expected_count in [('100%_done /?', 1), ('不存在的笔记', 0), ('   ', 16)]:
                    search.fill(keyword)
                    expect(status).to_have_text(f'共找到 {expected_count} 条匹配笔记' if keyword.strip() else '共 16 条笔记')
                page.locator('#memo-search-clear').click()
                expect(search).to_have_value('')
                expect(status).to_have_text('共 16 条笔记')
                expect(cards).to_have_count(10)

                # An old response must not overwrite the newest query, even when it arrives last.
                held = []
                def hold_response(route):
                    if parse_qs(urlparse(route.request.url).query).get('q') == ['Keyword']:
                        held.append((route, route.fetch()))
                    else:
                        route.continue_()
                page.route('**/api/memos?**', hold_response)
                search.fill('Keyword')
                search.press('Enter')
                page.wait_for_timeout(100)
                assert held
                search.fill('中文')
                search.press('Enter')
                expect(status).to_have_text('共找到 1 条匹配笔记')
                held[0][0].fulfill(response=held[0][1])
                page.wait_for_timeout(100)
                expect(cards).to_have_count(1)
                expect(cards).to_contain_text('中文学习笔记 😀')
                page.unroute('**/api/memos?**', hold_response)

                # Do not search partially composed Chinese input.
                page.locator('#memo-search-clear').click()
                expect(status).to_have_text('共 16 条笔记')
                count = len(queries)
                search.evaluate("el => { el.dispatchEvent(new CompositionEvent('compositionstart')); el.value='中'; el.dispatchEvent(new InputEvent('input', {bubbles:true,isComposing:true})); }")
                page.wait_for_timeout(400)
                assert len(queries) == count
                search.evaluate("el => { el.value='中文'; el.dispatchEvent(new CompositionEvent('compositionend')); }")
                expect(status).to_have_text('共找到 1 条匹配笔记')

                page.locator('.feed-tab[data-visibility="private"]').click()
                expect(status).to_contain_text('加载失败')
                expect(cards).to_have_count(0)
                response = context.request.post(base + '/api/auth/login', data={'username': 'owner', 'password': PASSWORD})
                assert response.status == 200
                page.reload()
                expect(cards).to_have_count(10)
                search.fill('机密')
                page.locator('.feed-tab[data-visibility="private"]').click()
                expect(status).to_have_text('共找到 1 条匹配笔记')
                expect(cards).to_contain_text('机密 Keyword owner')
                assert '机密 Keyword other' not in cards.inner_text()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal overflow'
                output = ROOT / 'artifacts' / 'features'
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'memo-search-{width}.png'))
                cards.locator('.memo-menu summary').click()
                page.once('dialog', lambda dialog: dialog.accept())
                cards.locator('.memo-del-btn').click()
                expect(status).to_have_text('共找到 0 条匹配笔记')
                expect(cards).to_have_count(0)
                # Restore this isolated fixture for the next viewport.
                owner = login(base, 'owner')
                try:
                    restored = owner.post('/api/memos', data={'content': '机密 Keyword owner', 'visibility': 'private'})
                    assert restored.status_code == 201
                finally:
                    owner.close()
                assert not errors, errors
                context.close()
                print(f'PASS {width}px real browser search, debounce/Enter/IME, clear, pagination, stale response, private filtering and deletion totals')
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    with server() as (base, _, _):
        api_checks(base)
        if args.browser:
            browser_checks(base)
