/* harness.js — page script for harness.html.

   Orchestration only: fetching, state, and wiring the three panes together.
   Event rendering lives in harness-trajectory.js, the SSE reader in
   harness-stream.js. */

(function () {
    const { streamEvents } = window.HarnessStream;
    const T = window.HarnessTrajectory;
    const esc = window.escHtml;

    const el = (id) => document.getElementById(id);
    const dom = {};

    const state = {
        sessionId: '',
        events: [],
        registry: null,
        tab: 'events',
        running: false,
        search: '',
    };

    // ── session list ─────────────────────────────────────────

    async function loadSessions() {
        const query = state.search ? `?q=${encodeURIComponent(state.search)}` : '';
        const page = await apiFetch(`/harness/sessions${query}`);
        renderSessions(page.items || []);
    }

    function renderSessions(sessions) {
        if (!sessions.length) {
            dom.sessionList.innerHTML = `<div class="hs-empty">${state.search ? '没有匹配的会话' : '还没有会话'}</div>`;
            return;
        }
        dom.sessionList.innerHTML = sessions.map((s) => `
            <div class="hs-session${s.id === state.sessionId ? ' hs-session-active' : ''}" data-id="${s.id}">
                <div class="hs-session-title">${esc(s.title || '未命名会话')}</div>
                <div class="hs-session-meta">
                    <span class="hs-dot hs-dot-${esc(s.status)}"></span>
                    <span>${esc(s.preset)}</span>
                    <span>${formatTime(s.updated_at)}</span>
                </div>
            </div>`).join('');

        dom.sessionList.querySelectorAll('.hs-session').forEach((node) => {
            node.addEventListener('click', () => openSession(node.dataset.id));
        });
    }

    async function newSession() {
        const session = await apiFetch('/harness/sessions', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        await loadSessions();
        await openSession(session.id);
    }

    async function openSession(sessionId) {
        state.sessionId = sessionId;
        state.events = await apiFetch(`/harness/sessions/${sessionId}/events`);
        const detail = await apiFetch(`/harness/sessions/${sessionId}`);

        dom.title.textContent = detail.title || '未命名会话';
        dom.subtitle.textContent =
            `${detail.preset} · ${detail.status}` +
            (detail.agent ? ` · 子代理 ${detail.agent}` : '') +
            (detail.forked_from ? ` · 分支自 ${detail.forked_from.slice(0, 8)} @ seq ${detail.forked_at_seq}` : '') +
            (detail.workspace_files.length ? ` · ${detail.workspace_files.length} 个文件` : '');
        renderParentLink(detail);

        T.renderUsage(dom.usage, detail.usage);
        renderConversation();
        renderTab();
        await loadSessions();
    }

    /** A child session has no sidebar row, so it needs its own way back up. */
    function renderParentLink(detail) {
        const existing = document.getElementById('hs-parent-link');
        if (existing) existing.remove();
        if (!detail.parent_id) return;

        const link = document.createElement('button');
        link.id = 'hs-parent-link';
        link.type = 'button';
        link.className = 'btn btn-sm';
        link.textContent = '← 返回上级会话';
        link.addEventListener('click', () => openSession(detail.parent_id));
        dom.subtitle.parentNode.appendChild(link);
    }

    // ── conversation ─────────────────────────────────────────

    /** Rebuild the centre pane from the log. The log is the only source of
     *  truth, so a reload and a live stream produce the same view. */
    function renderConversation() {
        if (!state.sessionId) {
            dom.conversation.innerHTML = '<div class="hs-empty">选择左侧会话，或新建一个开始</div>';
            return;
        }
        dom.conversation.innerHTML = '';
        state.events.forEach(appendEvent);
        scrollDown();
    }

    function appendEvent(event) {
        const node = buildNode(event);
        if (node) dom.conversation.appendChild(node);
    }

    function buildNode(event) {
        const d = event.data || {};
        switch (event.type) {
            case 'user/message':
                return bubble('hs-msg hs-msg-user', d.content);
            case 'assistant/message':
                return d.content ? bubble('hs-msg hs-msg-assistant', d.content) : null;
            case 'tool/result':
                return toolCard(event);
            case 'tool/approval':
                return approvalCard(event);
            case 'agent/error':
                return bubble('hs-msg hs-msg-error', `⚠ ${d.message || ''}`);
            case 'agent/interrupt':
                return bubble('hs-msg hs-msg-error', '⏹ 已中断');
            case 'subagent/end':
                return subagentCard(event);
            default:
                return null;   // infrastructure events belong in the inspector
        }
    }

    function bubble(className, text) {
        const node = document.createElement('div');
        node.className = className;
        const pre = document.createElement('pre');
        pre.textContent = text || '';
        node.appendChild(pre);
        return node;
    }

    function toolCard(event) {
        const d = event.data;
        const call = findCall(d.tool_call_id);
        const node = document.createElement('div');
        node.className = `hs-tool${d.is_error ? ' hs-tool-error' : ''}`;
        // todo_write output is the progress display itself, so show it open.
        const isTodo = d.name === 'todo_write' && !d.is_error;

        node.innerHTML = `
            <div class="hs-tool-head">
                <span class="hs-tool-name">${esc(d.name)}</span>
                <span class="hs-tool-summary">${esc(oneLine(d.content))}</span>
                <span class="hs-caret">${isTodo ? '▾' : '▸'}</span>
            </div>
            <div class="hs-tool-body${isTodo ? '' : ' hs-hidden'}">
                ${call ? `<pre>参数：${esc(call.function?.arguments || '')}</pre>` : ''}
                <pre class="${isTodo ? 'hs-todo' : ''}">${esc(d.content || '')}</pre>
            </div>`;

        const head = node.querySelector('.hs-tool-head');
        head.addEventListener('click', () => {
            const body = node.querySelector('.hs-tool-body');
            body.classList.toggle('hs-hidden');
            node.querySelector('.hs-caret').textContent = body.classList.contains('hs-hidden') ? '▸' : '▾';
        });
        return node;
    }

    /** A delegated run. The conversation shows only that it happened and what
     *  it cost — the steps themselves live in the child's own trajectory, which
     *  this card links to. */
    function subagentCard(event) {
        const d = event.data;
        const usage = d.usage || {};
        const node = document.createElement('div');
        node.className = `hs-tool${d.stopped ? ' hs-tool-error' : ''}`;
        node.innerHTML = `
            <div class="hs-tool-head">
                <span class="hs-tool-name">子代理 ${esc(d.agent)}</span>
                <span class="hs-tool-summary">${d.steps} 步 · ${usage.total_tokens || 0} tokens${
                    d.stopped ? ` · ${esc(d.stopped)}` : ''}</span>
            </div>
            <div class="hs-approval-actions">
                <button type="button" class="btn btn-sm" data-open-child="${esc(d.child_session_id)}">
                    查看它的完整轨迹
                </button>
            </div>`;
        node.querySelector('[data-open-child]').addEventListener('click', (e) => {
            // Child sessions are deliberately absent from the sidebar, so this
            // link is the only way in. openSession works from an id alone.
            openSession(e.currentTarget.dataset.openChild);
        });
        return node;
    }

    function approvalCard(event) {
        const d = event.data;
        const node = document.createElement('div');
        node.className = 'hs-tool hs-tool-approval';
        // A resolved approval is history: its buttons would be dead, so the
        // card renders read-only once a result for the same call exists.
        const settled = state.events.some(
            (e) => e.type === 'tool/result' && e.data?.tool_call_id === d.tool_call_id);

        node.innerHTML = `
            <div class="hs-tool-head">
                <span class="hs-tool-name">${esc(d.name)}</span>
                <span class="hs-tool-summary">需要人工批准</span>
            </div>
            <div class="hs-approval-reason">${esc(d.reason || '')}</div>
            <div class="hs-tool-body"><pre>${esc(d.arguments || '')}</pre></div>
            ${settled ? '' : `
            <div class="hs-approval-actions">
                <button type="button" class="btn btn-sm btn-primary" data-approve="1">批准执行</button>
                <button type="button" class="btn btn-sm btn-danger" data-approve="0">拒绝</button>
            </div>`}`;

        node.querySelectorAll('[data-approve]').forEach((btn) => {
            btn.addEventListener('click', () => {
                node.querySelector('.hs-approval-actions')?.remove();
                resolveApproval(d.tool_call_id, btn.dataset.approve === '1');
            });
        });
        return node;
    }

    function findCall(callId) {
        for (let i = state.events.length - 1; i >= 0; i -= 1) {
            const event = state.events[i];
            if (event.type !== 'assistant/message') continue;
            const call = (event.data.tool_calls || []).find((c) => c.id === callId);
            if (call) return call;
        }
        return null;
    }

    const oneLine = (text) => String(text || '').replace(/\s+/g, ' ').slice(0, 90);
    const scrollDown = () => { dom.conversation.scrollTop = dom.conversation.scrollHeight; };

    // ── running a turn ───────────────────────────────────────

    async function send() {
        const text = dom.input.value.trim();
        if (!text || state.running) return;
        if (!state.sessionId) await newSession();

        dom.input.value = '';
        await runStream(`/harness/sessions/${state.sessionId}/messages`, { text });
    }

    async function resolveApproval(callId, approved) {
        await runStream(
            `/harness/sessions/${state.sessionId}/approvals/${callId}`, { approved });
    }

    /** Consume one SSE turn, appending events live to both panes. */
    async function runStream(path, body) {
        setRunning(true);
        const typing = document.createElement('div');
        typing.className = 'hs-typing';
        typing.textContent = '思考中…';
        dom.conversation.appendChild(typing);

        // Assistant text arrives as chunks; stream it into one growing bubble
        // so the user sees tokens rather than a wait then a wall of text.
        let streamingBubble = null;
        let streamed = '';

        try {
            await streamEvents(path, body, {
                onEvent: (event) => {
                    state.events.push(event);

                    if (event.type === 'assistant/chunk') {
                        if (!streamingBubble) {
                            streamingBubble = bubble('hs-msg hs-msg-assistant', '');
                            dom.conversation.insertBefore(streamingBubble, typing);
                        }
                        streamed += event.data.delta || '';
                        streamingBubble.querySelector('pre').textContent = streamed;
                    } else {
                        if (event.type === 'assistant/message' && streamingBubble) {
                            // The final message supersedes the chunks it was
                            // assembled from; drop the placeholder.
                            streamingBubble.remove();
                            streamingBubble = null;
                            streamed = '';
                        }
                        const node = buildNode(event);
                        if (node) dom.conversation.insertBefore(node, typing);
                    }

                    renderTab();
                    scrollDown();
                },
                onError: (message) => {
                    dom.conversation.insertBefore(
                        bubble('hs-msg hs-msg-error', `⚠ ${message}`), typing);
                },
            });
        } catch (e) {
            toast(e.message, 'error');
            dom.conversation.insertBefore(bubble('hs-msg hs-msg-error', `⚠ ${e.message}`), typing);
        } finally {
            typing.remove();
            setRunning(false);
            // Titles and usage are settled server-side after the turn.
            if (state.sessionId) {
                const detail = await apiFetch(`/harness/sessions/${state.sessionId}`);
                dom.title.textContent = detail.title || '未命名会话';
                dom.subtitle.textContent = `${detail.preset} · ${detail.status}` +
                    (detail.workspace_files.length ? ` · ${detail.workspace_files.length} 个文件` : '');
                T.renderUsage(dom.usage, detail.usage);
                await loadSessions();
            }
        }
    }

    function setRunning(running) {
        state.running = running;
        dom.send.disabled = running;
        dom.interrupt.classList.toggle('hs-hidden', !running);
    }

    async function interruptTurn() {
        const res = await apiFetch(`/harness/sessions/${state.sessionId}/interrupt`, {
            method: 'POST',
        });
        toast(res.interrupted ? '已请求中断' : '当前没有运行中的任务', res.interrupted ? 'info' : 'error');
    }

    // ── inspector ────────────────────────────────────────────

    function renderTab() {
        if (state.tab === 'events') {
            T.renderEvents(dom.tabBody, state.events, forkSession);
        } else if (state.tab === 'messages') {
            renderDerivedTab();
        } else if (state.tab === 'files') {
            renderFilesTab();
        } else {
            T.renderPlugins(dom.tabBody, state.registry);
        }
    }

    /** Files the agent produced. Refetched on open rather than cached: a turn
     *  that just finished has almost certainly changed this list. */
    async function renderFilesTab() {
        if (!state.sessionId) {
            dom.tabBody.innerHTML = '<div class="hs-empty">还没有会话</div>';
            return;
        }
        T.renderFiles(dom.tabBody, null);
        try {
            const files = await apiFetch(`/harness/sessions/${state.sessionId}/files`);
            T.renderFiles(dom.tabBody, files, downloadFile, downloadArchive);
        } catch (e) {
            dom.tabBody.innerHTML = `<div class="hs-empty">读取文件列表失败：${esc(e.message)}</div>`;
        }
    }

    /** Download through fetch, not a bare <a href>.
     *
     *  The API is behind auth and apiFetch only returns JSON, so the bytes are
     *  pulled here and handed to the browser as a blob. */
    async function saveAs(url, fallbackName) {
        const res = await fetch(`/api${url}`, {
            credentials: 'include',
            headers: { 'X-CSRF-Token': window.Auth?.csrfToken?.() || '' },
        });
        if (!res.ok) {
            alert(`下载失败：HTTP ${res.status}`);
            return;
        }
        // Prefer the server's filename; it carries the RFC 5987 encoding that
        // keeps a CJK name intact.
        const disposition = res.headers.get('content-disposition') || '';
        const starred = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
        const plain = /filename="([^"]+)"/i.exec(disposition);
        const name = starred ? decodeURIComponent(starred[1]) : (plain ? plain[1] : fallbackName);

        const blob = await res.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(href);
    }

    function downloadFile(path) {
        const encoded = path.split('/').map(encodeURIComponent).join('/');
        saveAs(`/harness/sessions/${state.sessionId}/files/${encoded}`, path.split('/').pop());
    }

    function downloadArchive() {
        saveAs(`/harness/sessions/${state.sessionId}/archive`,
               `harness-${state.sessionId.slice(0, 8)}.zip`);
    }

    async function renderDerivedTab() {
        if (!state.sessionId) {
            dom.tabBody.innerHTML = '<div class="hs-empty">还没有会话</div>';
            return;
        }
        const res = await apiFetch(`/harness/sessions/${state.sessionId}/messages/derived`);
        T.renderDerived(dom.tabBody, res.messages || []);
    }

    async function forkSession(seq) {
        const forked = await apiFetch(
            `/harness/sessions/${state.sessionId}/fork?seq=${seq}`, { method: 'POST' });
        toast('已创建分支会话');
        await openSession(forked.id);
    }

    async function removeSession() {
        if (!state.sessionId) return;
        if (!confirm('删除该会话及其工作区？此操作不可撤销。')) return;
        await apiFetch(`/harness/sessions/${state.sessionId}`, { method: 'DELETE' });
        state.sessionId = '';
        state.events = [];
        dom.title.textContent = 'Harness 工作台';
        dom.subtitle.textContent = '选择左侧会话，或新建一个开始';
        T.renderUsage(dom.usage, null);
        renderConversation();
        renderTab();
        await loadSessions();
    }

    async function uploadAttachment(file) {
        if (!state.sessionId) await newSession();
        const form = new FormData();
        form.append('file', file);
        const res = await apiFetch(`/harness/sessions/${state.sessionId}/attachments`, {
            method: 'POST',
            body: form,
        });
        toast(`已上传 ${res.filename}，可以让 Agent 直接读取`);
        await openSession(state.sessionId);
    }

    // ── boot ─────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', async () => {
        Object.assign(dom, {
            sessionList: el('hs-session-list'), search: el('hs-search'), newBtn: el('hs-new'),
            title: el('hs-title'), subtitle: el('hs-subtitle'), conversation: el('hs-conversation'),
            input: el('hs-input'), send: el('hs-send'), file: el('hs-file'),
            interrupt: el('hs-interrupt'), delete: el('hs-delete'),
            usage: el('hs-usage'), tabBody: el('hs-tab-body'),
        });

        // Make the three columns draggable. The sizes here are the CSS
        // defaults, so double-clicking a divider returns to the stylesheet's
        // layout; the middle track stays `1fr` so the conversation absorbs
        // every window resize rather than overflowing.
        window.SplitPane?.make({
            id: 'harness',
            grid: '.hs-layout',
            tracks: [
                { size: 260, min: 180 },     // session list
                { flexible: true, min: 320 },  // conversation
                { size: 380, min: 260 },     // inspector
            ],
        });

        // And vertically, so the conversation can take room back from the
        // composer. On a short viewport (a landscape phone) the composer and
        // the head between them left the conversation showing one line.
        window.SplitPane?.make({
            id: 'harness-main',
            grid: '.hs-main',
            axis: 'row',
            tracks: [
                { auto: true },                // head — sized by its own content
                { flexible: true, min: 120 },  // conversation
                { size: 116, min: 64 },        // composer
            ],
        });

        // Auth.init() resolves asynchronously; reading the user directly races
        // with it and tells a logged-in user they are not.
        const user = window.Auth?.user?.() || await window.Auth?.init?.();
        if (!user) { window.Auth?.requireLogin?.(); return; }

        dom.newBtn.addEventListener('click', () => newSession().catch((e) => toast(e.message, 'error')));
        dom.send.addEventListener('click', () => send().catch((e) => toast(e.message, 'error')));
        dom.delete.addEventListener('click', () => removeSession().catch((e) => toast(e.message, 'error')));
        dom.interrupt.addEventListener('click', () => interruptTurn().catch((e) => toast(e.message, 'error')));

        dom.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send().catch((err) => toast(err.message, 'error')); }
        });

        let searchTimer;
        dom.search.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.search = dom.search.value.trim();
                loadSessions().catch((e) => toast(e.message, 'error'));
            }, 250);
        });

        dom.file.addEventListener('change', () => {
            const file = dom.file.files?.[0];
            if (!file) return;
            uploadAttachment(file).catch((e) => toast(e.message, 'error'));
            dom.file.value = '';
        });

        document.querySelectorAll('.hs-tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.hs-tab').forEach((t) => t.classList.remove('hs-tab-active'));
                tab.classList.add('hs-tab-active');
                state.tab = tab.dataset.tab;
                renderTab();
            });
        });

        try {
            state.registry = await apiFetch('/harness/tools');
        } catch (e) {
            toast(e.message, 'error');
        }
        renderConversation();
        renderTab();
        await loadSessions().catch((e) => toast(e.message, 'error'));
    });
})();
