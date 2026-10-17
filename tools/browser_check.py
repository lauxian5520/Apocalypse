"""Desktop and narrow-screen browser acceptance against the local mock provider."""
import io
import base64
import json
import wave
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from PIL import Image
from playwright.sync_api import Page, Route, sync_playwright, expect
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


def check_music_playback_ui(page: Page, base: str, width: int) -> None:
    """Play real WAV audio through pointer, touch, autoplay recovery and dragging."""
    fixture = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/css/base.css">
<link rel="stylesheet" href="/css/components/floating-panel.css">
<link rel="stylesheet" href="/css/components/music-player.css"></head><body>
<button id="outside">Outside player</button>
<script>window.Auth = {init: async () => null, isAdmin: () => false};</script>
<script src="/js/widgets/floating-panel.js"></script>
<script src="/js/widgets/music-player.js"></script></body></html>'''
    audio_file = io.BytesIO()
    with wave.open(audio_file, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\0\0' * 8000 * 30)
    playlist = [
        {'id': index, 'title': f'Fixture {index}', 'url': f'{base}/music-fixture-{index}.wav'}
        for index in (1, 2)
    ]
    for initially_paused in (True, False):
        context = page.context.browser.new_context(
            viewport={'width': width, 'height': 900}, has_touch=width < 768,
        )
        music = context.new_page()
        failures: list[str] = []
        music.on('pageerror', lambda error: failures.append(str(error)))
        unavailable = {'value': False}

        def stream_fixture(route: Route) -> None:
            """Support byte ranges so Chromium can restore a nonzero position."""
            if unavailable['value'] and route.request.url.endswith('-2.wav'):
                route.fulfill(status=404)
                return
            content = audio_file.getvalue()
            headers = {'Accept-Ranges': 'bytes'}
            status_code = 200
            requested = route.request.headers.get('range')
            if requested:
                first, last = requested.removeprefix('bytes=').split('-', 1)
                start, end = int(first), int(last) if last else len(content) - 1
                headers['Content-Range'] = f'bytes {start}-{end}/{len(content)}'
                content = content[start:end + 1]
                status_code = 206
            route.fulfill(status=status_code, content_type='audio/wav', headers=headers, body=content)

        try:
            context.add_init_script('''(() => {
                const NativeAudio = window.Audio;
                window.musicPlayAttempts = 0;
                window.Audio = class extends NativeAudio {
                    constructor(...args) {
                        super(...args);
                        window.musicAudio = this;
                        const play = this.play.bind(this);
                        this.play = () => {
                            ++window.musicPlayAttempts;
                            if (!navigator.userActivation.hasBeenActive) {
                                return Promise.reject(new DOMException('Gesture required', 'NotAllowedError'));
                            }
                            return play();
                        };
                    }
                };
                if (!localStorage.getItem('mw_music_state_v1')) {
                    localStorage.setItem('mw_music_state_v1', JSON.stringify({
                        trackId: 1, currentTime: 1.5, volume: 0.7, paused: INITIAL_PAUSED
                    }));
                }
            })();'''.replace('INITIAL_PAUSED', json.dumps(initially_paused)))
            music.route('**/music-check.html*', lambda route: route.fulfill(content_type='text/html', body=fixture))
            music.route('**/api/music', lambda route: route.fulfill(json=playlist))
            music.route('**/music-fixture-*.wav', stream_fixture)
            music.goto(base+'/music-check.html')
            music.wait_for_function('window.musicAudio?.readyState >= 2 && musicAudio.currentTime >= 1.5')
            if not initially_paused:
                expect(music.locator('#mp-status')).to_have_text('点击播放音乐')
            attempts = music.evaluate('musicPlayAttempts')

            def activate(selector: str) -> None:
                """Use actual touchscreen gestures on the narrow viewport."""
                control = music.locator(selector)
                if width < 768:
                    control.tap()
                else:
                    control.click()

            def expect_playing() -> None:
                """Check decoded audio advances instead of checking only an icon."""
                position = music.evaluate('musicAudio.currentTime')
                music.wait_for_function('start => !musicAudio.paused && musicAudio.currentTime > start + 0.08', arg=position)
                expect(music.locator('#mp-play')).to_have_text('⏸')

            activate('#mp-play')
            expect_playing()
            assert music.evaluate('musicPlayAttempts') == attempts + 1
            activate('#mp-play')
            activate('#outside')
            assert music.evaluate('musicAudio.paused')
            expect(music.locator('#mp-play')).to_have_text('▶')
            activate('#mp-next')
            expect_playing()
            assert music.evaluate('musicAudio.src.endsWith("-2.wav")')
            activate('#mp-prev')
            expect_playing()
            assert music.evaluate('musicAudio.src.endsWith("-1.wav")')
            music.locator('#mp-volume').focus()
            music.keyboard.press('Home')
            music.keyboard.press('ArrowRight')
            assert music.evaluate('musicAudio.volume') == 0.01
            activate('#mp-mute')
            assert music.evaluate('musicAudio.volume') == 0
            activate('#mp-mute')
            assert music.evaluate('musicAudio.volume') == 0.01

            # Moving from a button must drag without also clicking that button.
            panel = music.locator('#music-player').bounding_box()
            button = music.locator('#mp-play').bounding_box()
            music.mouse.move(button['x']+button['width']/2, button['y']+button['height']/2)
            music.mouse.down()
            music.mouse.move(button['x']+button['width']/2-60, button['y']+button['height']/2+65, steps=6)
            music.mouse.up()
            moved = music.locator('#music-player').bounding_box()
            assert moved['y'] > panel['y'] + 40
            expect_playing()

            # Page navigation restores progress/volume; a single click unlocks playback.
            music.reload()
            expect(music.locator('#mp-status')).to_have_text('点击播放音乐')
            music.wait_for_function('musicAudio.readyState >= 2 && musicAudio.currentTime > 0')
            assert music.evaluate('musicAudio.volume') == 0.01
            activate('#mp-play')
            expect_playing()
            unavailable['value'] = True
            activate('#mp-next')
            expect(music.locator('#mp-status')).to_contain_text('音乐播放失败')
            attempts = music.evaluate('musicPlayAttempts')
            activate('#outside')
            assert music.evaluate('musicPlayAttempts') == attempts
            activate('#mp-next')
            expect_playing()
            assert not failures, failures
        except Exception:
            print('Music failure:', json.dumps(music.evaluate('''({
                source: window.musicAudio?.src, ready: window.musicAudio?.readyState,
                time: window.musicAudio?.currentTime, error: window.musicAudio?.error?.code,
                status: document.getElementById('mp-status')?.textContent,
                saved: localStorage.getItem('mw_music_state_v1')
            })'''), ensure_ascii=True))
            print('Music page errors:', json.dumps(failures, ensure_ascii=True))
            raise
        finally:
            context.close()
    print(f'PASS music playback {width}px: real decoding/time, click/touch, pause, tracks, volume, drag, navigation and media failure')


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
                        expect(page.locator('.feed-tab[data-visibility="private"]')).to_have_attribute('aria-pressed', 'true')
                        card=page.locator('.memo-card').filter(has_text=marker)
                        expect(card.locator('.memo-content img')).to_have_count(0)
                        expect(card.locator('.memo-img')).to_be_visible()
                        card.locator('.memo-image-button').click()
                        expect(page.locator('.memo-lightbox')).to_be_visible()
                        page.keyboard.press('Escape')
                        expect(page.locator('.memo-lightbox')).to_have_count(0)
                        card.locator('.toggle-comments-btn').click()
                        card.locator('.comment-input').fill(marker+' reply')
                        card.locator('.comment-form button[type=submit]').click()
                        expect(card.locator('.memo-comment-count')).to_have_text('1')
                        expect(card.locator('.comment-content')).to_have_text(marker+' reply')
                        page.once('dialog',lambda dialog: dialog.accept())
                        card.locator('.comment-del-btn').click()
                        expect(card.locator('.memo-comment-count')).to_have_text('0')
                        expect(card.locator('.no-comments')).to_be_visible()
                        card.locator('.memo-like-btn').click()
                        expect(card.locator('.memo-like-btn')).to_have_attribute('aria-pressed', 'true')
                        expect(card.locator('.memo-likes')).to_contain_text('admin')
                        card.locator('.memo-like-btn').click()
                        expect(card.locator('.memo-like-count')).to_have_text('0')
                        card.locator('.memo-menu summary').click()
                        card.locator('.memo-edit-btn').click()
                        expect(card.locator('.memo-edit-content')).to_be_visible()
                        card.locator('.memo-edit-content').fill(marker+' edited')
                        card.locator('[data-remove-existing]').click()
                        card.locator('.memo-edit-file').set_input_files({'name':'replacement.png','mimeType':'image/png','buffer':data.getvalue()})
                        card.locator('.memo-edit-save').click()
                        expect(page.locator('.memo-content').filter(has_text=marker+' edited')).to_be_visible()
                        expect(card.locator('.memo-images img')).to_have_attribute('alt', 'replacement.png')
                        card.locator('.memo-menu summary').click()
                        page.once('dialog',lambda dialog: dialog.accept())
                        page.locator('.memo-card').filter(has_text=marker).locator('.memo-visibility-btn').click()
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_have_count(0)
                        page.locator('.feed-tab[data-visibility="public"]').click()
                        expect(page.locator('.memo-card').filter(has_text=marker)).to_be_visible()
                        card.locator('.memo-menu summary').click()
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
                        check_music_playback_ui(page, base, width)
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
