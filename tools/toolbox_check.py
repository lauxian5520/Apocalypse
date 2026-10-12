"""Browser acceptance for local tools, using an isolated static server and mocked APIs.

Run: .venv/bin/python tools/toolbox_check.py
Requires Playwright and Chromium; no application dependencies or user data change.
"""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import re

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def check(playwright, base, width, height):
    browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': width, 'height': height}, timezone_id='America/Phoenix',
                                  permissions=['clipboard-read', 'clipboard-write'], accept_downloads=True)
    page = context.new_page()
    errors, translations, utility_requests = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        url = route.request.url
        if '/api/ai/translate' in url:
            translations.append(route.request.post_data_json)
            route.fulfill(json={'translation': '你好 🌍'})
        elif '/api/' in url:
            route.fulfill(status=401, json={'detail': 'Not authenticated'}) if '/auth/me' in url else route.fulfill(json=[])
        elif url.startswith(base):
            route.continue_()
        else:
            route.abort()

    page.route('**/*', route_request)
    page.add_init_script("""(() => {
      window.liveBlobURLs = new Set();
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = blob => { const url = create(blob); liveBlobURLs.add(url); return url; };
      URL.revokeObjectURL = url => { liveBlobURLs.delete(url); revoke(url); };
      window.activeIntervals = new Set();
      const start = window.setInterval.bind(window), clear = window.clearInterval.bind(window);
      window.setInterval = (...args) => { const id = start(...args); activeIntervals.add(id); return id; };
      window.clearInterval = id => { activeIntervals.delete(id); clear(id); };
    })();""")
    page.goto(base + '/tools.html')
    expect(page.locator('.tool-card')).to_have_count(12)
    page.wait_for_timeout(100)
    page.on('request', lambda request: utility_requests.append(request.url) if '/api/' in request.url else None)

    def open_tool(name):
        page.locator('#tools-search').fill(name)
        expect(page.locator('.tool-card')).to_have_count(1)
        page.locator('.tool-card').click()
        expect(page.locator('#tool-modal')).to_have_class('modal-overlay active')

    def close_tool():
        page.locator('#modal-close').click()
        expect(page.locator('#modal-content')).to_be_empty()
        assert page.evaluate('liveBlobURLs.size') == 0

    def layout():
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow'
        assert page.locator('.modal').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'), 'modal overflow'
        columns = page.locator('.utility-panels').first.evaluate('(el) => getComputedStyle(el).gridTemplateColumns.split(" ").length')
        assert columns == (1 if width <= 640 else 2)
        page.locator('.modal').evaluate('(el) => el.scrollTop = el.scrollHeight')
        box = page.locator('#modal-close').bounding_box()
        assert box and 0 <= box['y'] and box['y'] + box['height'] <= height, 'close control offscreen'

    output = ROOT / 'artifacts' / 'features'
    output.mkdir(parents=True, exist_ok=True)
    open_tool('文本处理与统计')
    page.locator('#text-input').fill('中文 🌍 hello world\n\n  重复  \n重复\n')
    expect(page.locator('#text-stats dd')).to_have_text(['28', '17', '6', '2', '5'])
    page.locator('#text-unique').check()
    page.locator('#text-run').click()
    expect(page.locator('#text-output')).to_have_value('中文 🌍 hello world\n重复')
    page.locator('#text-copy').click()
    assert page.evaluate('navigator.clipboard.readText()') == '中文 🌍 hello world\n重复'
    layout()
    page.screenshot(path=str(output / f'toolbox-text-{width}.png'))
    close_tool()
    open_tool('文本处理与统计')
    expect(page.locator('#text-input')).to_have_value('')
    expect(page.locator('#text-stats dd')).to_have_text(['0'] * 5)
    page.locator('#text-input').fill('<script>window.injected=true</script>')
    page.locator('#text-run').click()
    assert not page.evaluate('Boolean(window.injected)')
    close_tool()
    print(f'PASS {width}px text statistics, cleanup, clipboard, pure-text rendering and responsive layout')

    open_tool('时间戳转换')
    page.locator('#timestamp-input').fill('1700000000')
    page.locator('#timestamp-form button[type=submit]').click()
    expect(page.locator('#timestamp-output')).to_have_value(re.compile(re.escape('2023-11-14T22:13:20.000Z')))
    expect(page.locator('#timestamp-output')).to_have_value(re.compile(re.escape('2023-11-14 15:13:20.000 (America/Phoenix)')))
    page.locator('#timestamp-unit').select_option('milliseconds')
    page.locator('#timestamp-input').fill('1700000000123')
    page.locator('#timestamp-form button[type=submit]').click()
    expect(page.locator('#timestamp-output')).to_have_value(re.compile(re.escape('2023-11-14T22:13:20.123Z')))
    page.locator('#datetime-form button').click()
    expect(page.locator('#timestamp-input')).to_have_value('1700000000123')
    for value, expected in [('0', '1970-01-01T00:00:00.000Z'), ('-1', '1969-12-31T23:59:59.999Z')]:
        page.locator('#timestamp-input').fill(value)
        page.locator('#timestamp-form button[type=submit]').click()
        expect(page.locator('#timestamp-output')).to_have_value(re.compile(re.escape(expected)))
    for value in ['1.5', 'abc', '9007199254740992']:
        page.locator('#timestamp-input').fill(value)
        page.locator('#timestamp-form button[type=submit]').click()
        expect(page.locator('.utility-status')).to_have_class('utility-status is-error')
        expect(page.locator('#timestamp-copy')).to_be_disabled()
    page.locator('#timestamp-now').click()
    assert abs(int(page.locator('#timestamp-input').input_value()) - page.evaluate('Date.now()')) < 3000
    close_tool()
    print(f'PASS {width}px seconds/milliseconds, local/UTC, date round-trip, epoch, negative and invalid timestamps')

    open_tool('编码转换')
    original = '\ufeff中文 😀 🌍\nhello /?&=%'
    page.locator('#encoding-input').fill(original)
    page.locator('#encoding-run').click()
    encoded = page.locator('#encoding-output').input_value()
    assert base64.b64decode(encoded).decode('utf-8') == original
    page.locator('#encoding-type').select_option('base64-decode')
    expect(page.locator('#encoding-output')).to_have_value('')
    page.locator('#encoding-input').fill(encoded)
    page.locator('#encoding-run').click()
    expect(page.locator('#encoding-output')).to_have_value(original)
    for value in ['abc!', 'A', 'YQ=', 'Y===', 'Zh==', '/w==']:
        page.locator('#encoding-input').fill(value)
        page.locator('#encoding-run').click()
        expect(page.locator('.utility-status')).to_have_class('utility-status is-error')
    page.locator('#encoding-type').select_option('url-encode')
    page.locator('#encoding-input').fill(original)
    page.locator('#encoding-run').click()
    url_encoded = page.locator('#encoding-output').input_value()
    page.locator('#encoding-type').select_option('url-decode')
    page.locator('#encoding-input').fill(url_encoded)
    page.locator('#encoding-run').click()
    expect(page.locator('#encoding-output')).to_have_value(original)
    page.locator('#encoding-input').fill('%E4%ZZ')
    page.locator('#encoding-run').click()
    expect(page.locator('.utility-status')).to_contain_text('URL 编解码失败')
    page.locator('#encoding-input').fill('')
    page.locator('#encoding-run').click()
    expect(page.locator('#encoding-output')).to_have_value('')
    page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: () => Promise.reject(new Error('denied'))}})")
    page.locator('#encoding-copy').click()
    expect(page.locator('.utility-status')).to_contain_text('手动复制')
    page.evaluate("delete navigator.clipboard")
    close_tool()
    print(f'PASS {width}px Chinese/emoji UTF-8, Base64/URL round-trips, invalid inputs and clipboard fallback')

    open_tool('文本差异对比')
    page.locator('#diff-before').fill('keep\ndelete\nrepeat\nrepeat')
    page.locator('#diff-after').fill('keep\ninsert\nrepeat')
    page.locator('#diff-run').click()
    expect(page.locator('#diff-count')).to_have_text('差异 3 行 · 新增 1 · 删除 2 · 未修改 2')
    page.locator('#diff-before').fill('<img src=x onerror="window.injected=true">\n')
    page.locator('#diff-after').fill('')
    page.locator('#diff-run').click()
    expect(page.locator('.diff-removed')).to_have_count(2)
    assert not page.evaluate('Boolean(window.injected)')
    page.locator('#diff-before').fill('')
    page.locator('#diff-run').click()
    expect(page.locator('#diff-count')).to_contain_text('差异 0 行')
    for count in [500, 501]:
        page.locator('#diff-before').fill('\n'.join(str(i) for i in range(count)))
        page.locator('#diff-after').fill('\n'.join(str(i) for i in reversed(range(count))))
        page.locator('#diff-run').click()
        if count == 500:
            expect(page.locator('.diff-same')).to_have_count(1)
            layout()
            page.screenshot(path=str(output / f'toolbox-diff-{width}.png'))
        else:
            expect(page.locator('.utility-status')).to_contain_text('最多支持 500 行')
            expect(page.locator('#diff-output')).to_be_empty()
    close_tool()
    print(f'PASS {width}px LCS diff, repeated/empty lines, escaped content and 500-line bounds')

    # Generate real transparent PNG fixtures using the browser's canvas.
    def image_fixture(w, h):
        encoded = page.evaluate("""([w,h]) => { const c=document.createElement('canvas'); c.width=w; c.height=h;
          c.getContext('2d').fillRect(w/2,h/2,w/2,h/2); return c.toDataURL('image/png').split(',')[1]; }""", [w, h])
        return {'name': '透明.png', 'mimeType': 'image/png', 'buffer': base64.b64decode(encoded)}

    def result_pixels():
        return page.evaluate("""async () => { const image = document.querySelector('#image-result'); await image.decode();
          const c = document.createElement('canvas'); c.width=image.naturalWidth; c.height=image.naturalHeight;
          c.getContext('2d').drawImage(image,0,0); return {width:c.width, height:c.height, pixel:Array.from(c.getContext('2d').getImageData(0,0,1,1).data)}; }""")

    open_tool('图片压缩与格式转换')
    page.locator('#image-file').set_input_files(image_fixture(2400, 1200))
    expect(page.locator('#image-download')).to_be_visible()
    pixels = result_pixels()
    assert (pixels['width'], pixels['height'], pixels['pixel'][3]) == (1920, 960, 0)
    with page.expect_download() as info:
        page.locator('#image-download').click()
    download = info.value
    assert download.suggested_filename.endswith('.webp')
    download_path = output / f'toolbox-download-{width}.webp'
    download.save_as(download_path)
    assert download_path.stat().st_size > 0
    page.locator('#image-format').select_option('image/jpeg')
    page.locator('#image-run').click()
    expect(page.locator('#image-result-info')).to_contain_text('JPEG')
    assert result_pixels()['pixel'] == [255, 255, 255, 255]
    page.locator('#image-format').select_option('image/png')
    expect(page.locator('#image-quality')).to_be_disabled()
    page.locator('#image-run').click()
    expect(page.locator('#image-result-info')).to_contain_text('PNG')
    assert result_pixels()['pixel'][3] == 0
    layout()
    page.screenshot(path=str(output / f'toolbox-image-{width}.png'))
    page.locator('#image-file').set_input_files(image_fixture(32, 16))
    expect(page.locator('#image-result-info')).to_contain_text('32 × 16')
    assert page.evaluate('liveBlobURLs.size') == 2
    page.locator('#image-max').fill('0')
    page.locator('#image-run').click()
    expect(page.locator('.utility-status')).to_contain_text('最长边请输入')
    page.locator('#image-file').set_input_files({'name': 'broken.png', 'mimeType': 'image/png', 'buffer': b'broken'})
    expect(page.locator('.utility-status')).to_contain_text('无法读取图片')
    assert page.evaluate('liveBlobURLs.size') == 0
    page.locator('#image-file').set_input_files({'name': 'large.png', 'mimeType': 'image/png', 'buffer': b'0' * (20 * 1024 * 1024 + 1)})
    expect(page.locator('.utility-status')).to_contain_text('超过 20 MB')
    page.locator('#image-file').set_input_files({'name': 'file.txt', 'mimeType': 'text/plain', 'buffer': b'text'})
    expect(page.locator('.utility-status')).to_contain_text('请选择 JPEG')
    # Drop upload, and closing while async conversion is pending.
    page.locator('#image-max').fill('1920')
    fixture = image_fixture(32, 16)
    page.evaluate("""data => { const dt=new DataTransfer(); dt.items.add(new File([Uint8Array.from(atob(data), c=>c.charCodeAt(0))], 'drop.png', {type:'image/png'}));
       document.querySelector('#image-drop').dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:dt})); }""", base64.b64encode(fixture['buffer']).decode())
    expect(page.locator('#image-download')).to_be_visible()
    page.evaluate("""() => { window.originalToBlob = HTMLCanvasElement.prototype.toBlob;
      HTMLCanvasElement.prototype.toBlob = function(cb, ...args) { originalToBlob.call(this, blob => setTimeout(() => cb(blob), 200), ...args); }; }""")
    page.locator('#image-run').click()
    close_tool()
    page.wait_for_timeout(300)
    assert page.evaluate('liveBlobURLs.size') == 0
    page.evaluate('() => { HTMLCanvasElement.prototype.toBlob = window.originalToBlob; }')
    print(f'PASS {width}px image scaling/no upscale, transparency/JPEG white, download, drop, file limits and URL cleanup')
    assert not utility_requests, utility_requests

    for _ in range(3):
        open_tool('实时翻译')
        expect(page.locator('#translation-target option')).to_have_count(8)
        page.locator('#translation-input').fill('Hello')
        page.locator('#translation-submit').click()
        expect(page.locator('#translation-status')).to_contain_text('请先登录')
        page.evaluate("Auth.set('', {id: 1, username: 'test'})")
        page.locator('#translation-input').press('Control+Enter')
        expect(page.locator('#translation-output')).to_have_value('你好 🌍')
        close_tool()
        page.evaluate('Auth.clear()')
    assert len(translations) == 3, translations
    open_tool('专注计时器')
    before = page.evaluate('activeIntervals.size')
    page.locator('#timer-start').click()
    assert page.evaluate('activeIntervals.size') == before + 1
    close_tool()
    assert page.evaluate('activeIntervals.size') == before
    for name, selector in [('Markdown 编辑器', '#md-in'), ('颜色工具', '#color-picker'), ('密码生成器', '#pwd-result'), ('JSON 格式化', '#json-in')]:
        open_tool(name)
        expect(page.locator(selector)).to_be_visible()
        close_tool()
    open_tool('编码转换')
    page.keyboard.press('Escape')
    expect(page.locator('#modal-content')).to_be_empty()
    assert not errors, errors
    print(f'PASS {width}px translation login/keyboard/reopening, existing tools, timer disposal, Escape and no browser errors')
    context.close()
    browser.close()


def main():
    handler = partial(QuietHandler, directory=str(ROOT / 'frontend'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            for dimensions in [(1440, 1000), (390, 844)]:
                check(playwright, f'http://127.0.0.1:{server.server_port}', *dimensions)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
