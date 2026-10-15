"""Desktop and narrow-screen browser acceptance against the local mock provider."""
import io
import base64
import json
import wave
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from PIL import Image
from playwright.sync_api import Page, sync_playwright, expect
from check_support import server, test_env, PASSWORD, ROOT
from mock_provider import mock_provider


def seed_feed_fixtures(root: Path) -> None:
    """Give offline browser checks realistic cache shapes without public requests."""
    directory = root / 'feeds'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    payloads = {
        'github_daily': [{'name': 'fixture/project', 'url': 'https://example.invalid/project', 'stars': '42'}],
        'huggingface': [{'id': 'fixture/model', 'name': 'Fixture model', 'url': 'https://example.invalid/model'}],
        'hf_papers_daily': [{'title': 'Fixture recent paper', 'url': 'https://example.invalid/paper', 'published_at': stamp, 'upvotes': 3}],
    }
    for name, items in payloads.items():
        (directory/f'{name}.json').write_text(json.dumps({'updated_at': stamp, 'data': items}), encoding='utf-8')
    news = {'title': 'Fixture recent news', 'url': 'https://example.invalid/news', 'published_at': stamp}
    (directory/'focus.json').write_text(json.dumps({'savedAt': stamp, 'categories': {
        key: {'sections': {'Fixture publisher': [{'section': 'News', 'items': [news]}]}}
        for key in ('finance', 'tech', 'ai', 'politics')
    }}), encoding='utf-8')


def check_feed_widgets_ui(page: Page, base: str, width: int) -> None:
    """Verify populated feeds, news dates and visible/recoverable music controls."""
    page.goto(base+'/trending.html')
    expect(page.locator('#github-grid .feed-card').first).to_be_visible(timeout=20000)
    expect(page.locator('#hf-grid .feed-card').first).to_be_visible(timeout=20000)
    page.goto(base+'/papers.html')
    expect(page.locator('#papers-grid .feed-card').first).to_be_visible(timeout=20000)
    page.goto(base+'/focus.html')
    expect(page.locator('.news-card').first).to_be_visible(timeout=20000)
    expect(page.locator('.news-card time').first).to_be_visible()
    expect(page.locator('#news-window')).to_contain_text('72')
    assert page.locator('html').evaluate('(element) => element.scrollWidth <= innerWidth')
    expect(page.locator('#music-player')).to_be_visible()
    expect(page.locator('#mp-status')).to_have_text('暂无音乐')
    expect(page.locator('#mp-play')).to_be_disabled()
    expect(page.locator('#mp-library')).to_be_visible()
    page.screenshot(path=str(ROOT/f'artifacts/browser/feeds-{width}.png'), full_page=True)

    page.route('**/api/music', lambda route: route.fulfill(status=503, json={'detail': 'fixture outage'}))
    page.locator('#mp-retry').click()
    expect(page.locator('#mp-status')).to_have_text('音乐加载失败')
    expect(page.locator('#music-player')).to_be_visible()
    expect(page.locator('#mp-retry')).to_be_enabled()
    page.unroute('**/api/music')
    page.locator('#mp-retry').click()
    expect(page.locator('#mp-status')).to_have_text('暂无音乐')
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\0\0'*8000)
    url = 'data:audio/wav;base64,' + base64.b64encode(audio.getvalue()).decode()
    page.route('**/api/music', lambda route: route.fulfill(json=[{'id': 1, 'title': 'Fixture audio', 'url': url}]))
    page.locator('#mp-retry').click()
    expect(page.locator('#mp-play')).to_be_enabled()
    expect(page.locator('#mp-status')).not_to_be_visible()
    page.unroute('**/api/music')
    page.locator('#mp-retry').click()
    expect(page.locator('#mp-status')).to_have_text('暂无音乐')
    print(f'PASS feed/music UI {width}px: projects, models, HF papers, dated news, empty/error/recovered player')


def check_user_management_ui(page: Page, base: str, width: int) -> None:
    """Verify delegated Harness access and self-service names in separate sessions."""
    page.goto(base+'/admin.html')
    toggle = page.locator('.js-toggle-harness[data-uid="1"]')
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    context = page.context.browser.new_context(viewport=page.viewport_size)
    friend = context.new_page()
    failures: list[str] = []
    friend.on('pageerror', lambda error: failures.append(str(error)))
    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
    try:
        friend.goto(base+'/login.html')
        friend.locator('#login-form [name=username]').fill('owner')
        friend.locator('#login-form [name=password]').fill(PASSWORD)
        friend.locator('#login-form button[type=submit]').click()
        friend.wait_for_url('**/memos.html')
        expect(friend.locator('#nav-user-area a[href="admin.html"]')).to_have_count(0)
        friend.goto(base+'/harness.html')
        friend.locator('#hs-new').click()
        expect(friend.locator('#hs-subtitle')).to_contain_text('idle')
        friend.locator('#hs-input').fill('delegated access')
        friend.locator('#hs-send').click()
        expect(friend.locator('#hs-conversation')).to_contain_text('mock complete', timeout=15000)

        friend.goto(base+'/profile.html')
        username = f'同伴{width}<b>昵称</b>'
        friend.locator('#profile-username').fill(username)
        friend.locator('#username-form button[type=submit]').click()
        expect(friend.locator('.profile-name')).to_have_text(username)
        expect(friend.locator('#nav-avatar-btn')).to_have_attribute('title', username)
        friend.reload()
        expect(friend.locator('#profile-username')).to_have_value(username)
        assert friend.locator('html').evaluate('(element) => element.scrollWidth <= window.innerWidth')
        page.locator('#user-refresh-btn').click()
        row = page.locator('#users-tbody tr').filter(has=toggle)
        expect(row.locator('td').nth(2)).to_have_text(username)
        expect(row.locator('td').nth(2).locator('b')).to_have_count(0)
        toggle.click()
        expect(toggle).to_have_attribute('aria-pressed', 'false')
        friend.goto(base+'/harness.html')
        expect(friend.locator('#hs-title')).to_have_text('Harness 使用权限未开通')
        expect(friend.locator('#hs-new')).to_be_disabled()
        friend.goto(base+'/admin.html')
        expect(friend.locator('#no-perm')).to_be_visible()
        expect(friend.locator('#admin-main')).not_to_be_visible()
        friend.goto(base+'/profile.html?uid=2')
        expect(friend.locator('.profile-name')).to_have_text('other')
        expect(friend.locator('#username-form')).to_have_count(0)
        friend.goto(base+'/profile.html')
        friend.locator('#profile-username').fill('owner')
        friend.locator('#username-form button[type=submit]').click()
        expect(friend.locator('.profile-name')).to_have_text('owner')
        friend.screenshot(path=str(ROOT/f'artifacts/browser/profile-{width}.png'))
        page.screenshot(path=str(ROOT/f'artifacts/browser/admin-{width}.png'), full_page=True)
        assert not failures, failures
    except Exception:
        friend.screenshot(path=str(ROOT/f'artifacts/browser/user-management-failed-{width}.png'), full_page=True)
        raise
    finally:
        context.close()
    print(f'PASS user management UI {width}px: independent permission, admin denial, rename, escaping and profile ownership')


def main():
    output=ROOT/'artifacts/browser';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, mock_provider() as endpoint:
        seed_feed_fixtures(Path(tmp))
        env=test_env(tmp,CUSTOM_API_URL=endpoint)
        with server(root=tmp,env=env) as (base,_,_), sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            try:
                for width,height in ((1440,1000),(390,844)):
                    context=browser.new_context(viewport={'width':width,'height':height})
                    page=context.new_page()
                    # Core workflows must function without optional CDN decorations.
                    page.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
                    failures=[]
                    page.on('pageerror',lambda error: failures.append(str(error)))
                    try:
                        page.goto(base+'/login.html')
                        page.locator('#login-form [name=username]').fill('admin')
                        page.locator('#login-form [name=password]').fill(PASSWORD)
                        page.locator('#login-form button[type=submit]').click()
                        page.wait_for_url('**/memos.html')
                        expect(page.locator('#memo-content')).to_be_visible()
                        marker=f'browser-fixture-{width}'
                        page.locator('#memo-content').fill(marker+' <img src=x onerror=alert(1)>')
                        page.locator('#memo-visibility').select_option('private')
                        data=io.BytesIO();Image.new('RGB',(2,2)).save(data,format='PNG')
                        page.locator('#memo-images').set_input_files({'name':'fixture.png','mimeType':'image/png','buffer':data.getvalue()})
                        page.locator('.post-btn').click()
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_be_visible()
                        expect(page.locator('#memo-filter')).to_have_value('private')
                        card=page.locator('.memo-card').filter(has_text=marker)
                        expect(card.locator('.memo-content img')).to_have_count(0)
                        expect(card.locator('.memo-img')).to_be_visible()
                        page.once('dialog',lambda dialog: dialog.accept(marker+' edited'))
                        card.locator('.memo-edit-btn').click()
                        expect(page.locator('.memo-content').filter(has_text=marker+' edited')).to_be_visible()
                        page.once('dialog',lambda dialog: dialog.accept())
                        page.locator('.memo-card').filter(has_text=marker).locator('.memo-visibility-btn').click()
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_have_count(0)
                        page.locator('#memo-filter').select_option('public')
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_be_visible()
                        page.once('dialog',lambda dialog: dialog.accept())
                        page.locator('.memo-card').filter(has_text=marker).locator('.memo-del-btn').click()
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_have_count(0)
                        page.goto(base+'/messages.html?uid=1')
                        page.locator('#dm-input').fill(marker+' DM')
                        page.locator('#dm-file').set_input_files({'name':'fixture.txt','mimeType':'text/plain','buffer':b'browser attachment'})
                        page.locator('#dm-form button[type=submit]').click()
                        expect(page.locator('#dm-messages')).to_contain_text(marker+' DM')
                        with page.expect_download() as download:
                            page.locator('#dm-messages a[download]').last.click()
                        assert Path(download.value.path()).read_bytes()==b'browser attachment'
                        page.goto(base+'/harness.html')
                        page.locator('#hs-new').click()
                        expect(page.locator('#hs-subtitle')).to_contain_text('idle')
                        page.locator('#hs-input').fill('browser hello')
                        page.locator('#hs-send').click()
                        expect(page.locator('#hs-conversation')).to_contain_text('mock complete')
                        expect(page.locator('#hs-send')).to_be_enabled()
                        page.locator('#hs-input').fill('mock-slow')
                        page.locator('#hs-send').click()
                        expect(page.locator('#hs-conversation')).to_contain_text('tick')
                        page.reload()
                        page.locator('.hs-session').first.click()
                        expect(page.locator('#hs-interrupt')).to_be_visible()
                        page.locator('#hs-interrupt').click()
                        expect(page.locator('#hs-send')).to_be_enabled(timeout=15000)
                        page.locator('#hs-input').fill('browser recovery')
                        page.locator('#hs-send').click()
                        expect(page.locator('#hs-conversation')).to_contain_text('browser recovery')
                        expect(page.locator('#hs-send')).to_be_enabled(timeout=15000)
                        check_feed_widgets_ui(page, base, width)
                        check_user_management_ui(page, base, width)
                        page.screenshot(path=str(output/f'passed-{width}.png'))
                        assert not failures,failures
                        print(f'PASS browser {width}x{height}: login, CRUD/privacy, image, DM download, Agent/reload/interrupt')
                    except Exception:
                        page.screenshot(path=str(output/f'failed-{width}.png'),full_page=True)
                        (output/f'failed-{width}.html').write_text(page.content())
                        raise
                    finally: context.close()
            finally: browser.close()


if __name__=='__main__': main()
