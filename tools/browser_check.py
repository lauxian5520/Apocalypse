"""Desktop and narrow-screen browser acceptance against the local mock provider."""
import io
from pathlib import Path
import tempfile
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from check_support import server, test_env, PASSWORD, ROOT
from mock_provider import mock_provider


def main():
    output=ROOT/'artifacts/browser';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, mock_provider() as endpoint:
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
