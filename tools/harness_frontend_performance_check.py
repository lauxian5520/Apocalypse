"""Browser regression for bounded Harness pages and burst streaming."""
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
KEEP = {
    "js/core/utils.js", "js/widgets/split-pane.js", "js/pages/harness-stream.js",
    "js/pages/harness-trajectory.js", "js/pages/harness.js",
}


def main() -> None:
    fixture = r'''(() => {
        window.Auth = {user: () => ({id: 1}), canUseHarness: () => true};
        const detail = {id:'probe', title:'Probe', preset:'standard', status:'idle', workspace_files:[], usage:{requests:1,prompt_tokens:0,completion_tokens:0,cached_tokens:0,cost_usd:0}};
        const events = [];
        for (let i=0; i<250; i++) {
            events.push({seq:i*2,type:'user/message',data:{content:'history-'+i}});
            events.push({seq:i*2+1,type:'assistant/chunk',data:{delta:'x'}});
        }
        window.apiFetch = async (url, options) => {
            if (url === '/harness/tools') return {tools:[],hooks:[]};
            if (url === '/harness/sessions') return options?.method === 'POST' ? detail : {items:[detail]};
            if (url.includes('/events/page')) {
                const q = new URL(url, location.href).searchParams;
                let rows = events.filter(e => !q.has('conversation') || e.type !== 'assistant/chunk');
                if (q.has('before')) rows = rows.filter(e => e.seq < Number(q.get('before')));
                if (q.has('after')) rows = rows.filter(e => e.seq > Number(q.get('after')));
                const more = rows.length > 100;
                return {items:q.has('after') ? rows.slice(0,100) : rows.slice(-100),has_more:more};
            }
            return detail;
        };
        window.maxGap = 0;
        let last = performance.now();
        setInterval(() => {const now=performance.now(); window.maxGap=Math.max(window.maxGap,now-last); last=now;}, 16);
        window.fetch = async () => {
            const frames = [];
            const add = (seq,type,data) => frames.push('data: '+JSON.stringify({seq,type,data})+'\n\n');
            add(500,'user/message',{content:'stream probe'});
            for (let i=0; i<4000; i++) add(501+i,'assistant/chunk',{delta:'abcd',reasoning:''});
            add(4501,'assistant/message',{content:'abcd'.repeat(4000),tool_calls:[]});
            add(4502,'turn/end',{});
            return new Response(frames.join('')+'data: [DONE]\n\n', {headers:{'Content-Type':'text/event-stream'}});
        };
    })();'''
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(fixture)

        def respond(route) -> None:
            path = urlparse(route.request.url).path.lstrip("/")
            file = FRONTEND / path
            if not file.is_file():
                route.fulfill(status=404, body="")
                return
            body = file.read_bytes()
            if path == "harness.html":
                body = re.sub(
                    r'<script\s+src="([^"]+)"\s*></script>',
                    lambda match: match.group(0) if match.group(1) in KEEP else "",
                    body.decode("utf-8"),
                ).encode("utf-8")
            route.fulfill(body=body, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")

        page.route("**/*", respond)
        page.goto("http://harness-test.local/harness.html")
        page.wait_for_function("!document.querySelector('#hs-new').disabled")
        page.click("#hs-new")
        page.wait_for_selector("text=history-249")
        assert page.locator("#hs-conversation .hs-msg").count() == 100
        assert page.locator("#hs-tab-body .hs-event").count() == 100
        page.locator("#hs-conversation .hs-page-controls button").first.click()
        page.wait_for_selector("text=history-149")
        assert page.locator("#hs-conversation >> text=history-249").count() == 0
        page.locator("#hs-conversation .hs-page-controls button").last.click()
        page.wait_for_selector("text=history-249")
        page.locator("#hs-tab-body .hs-page-controls button").first.click()
        page.wait_for_function("document.querySelector('#hs-tab-body .hs-event').dataset.seq === '300'")
        page.locator("#hs-tab-body .hs-page-controls button").last.click()
        page.wait_for_function("document.querySelector('#hs-tab-body .hs-event').dataset.seq === '400'")
        page.fill("#hs-input", "stream probe")
        page.evaluate("window.maxGap = 0; document.querySelector('#hs-send').click()")
        page.wait_for_function("!document.querySelector('#hs-send').disabled", timeout=30000)
        page.wait_for_timeout(100)
        assert page.locator("#hs-conversation .hs-msg-assistant pre").last.text_content() == "abcd" * 4000
        assert page.locator("#hs-tab-body .hs-event").count() <= 100
        assert page.locator("#hs-conversation .hs-msg").count() <= 100
        gap = page.evaluate("window.maxGap")
        assert gap < 2000, f"main-thread timer gap {gap:.0f}ms"
        assert not errors, errors
        print(f"PASS: paged history, bounded DOM, 4000 complete chunks; max timer gap {gap:.0f}ms")
        browser.close()


if __name__ == "__main__":
    main()
