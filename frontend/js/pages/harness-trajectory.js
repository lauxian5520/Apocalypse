/* harness-trajectory.js — renders the inspector's three tabs.

   Pure rendering: every function takes data and returns (or fills) DOM, with
   no fetching and no state of its own. That keeps the event vocabulary in one
   readable place and lets harness.js stay about orchestration. */

(function () {
    const esc = window.escHtml;

    // Events that project into model messages, highlighted in the raw stream
    // because "what the model saw" is the question the panel exists to answer.
    const SURFACE = new Set(['user/message', 'assistant/message', 'tool/result']);

    // Forking mid-step would orphan a tool call from its results, so the button
    // is only offered where a turn is genuinely at rest.
    const FORKABLE = new Set(['turn/end', 'step/end', 'assistant/message']);

    /** One-line summary of an event, for the collapsed row. */
    function summarize(event) {
        const d = event.data || {};
        switch (event.type) {
            case 'user/message': return d.content || '';
            case 'assistant/message':
                return d.content || `${(d.tool_calls || []).length} 个工具调用`;
            case 'assistant/chunk': return d.delta || d.reasoning || '';
            case 'tool/call': return `${d.name} ${d.arguments || ''}`;
            case 'tool/result': return `${d.name} → ${d.content || ''}`;
            case 'tool/approval': return `${d.name} 待批准`;
            case 'llm/usage':
                return `in ${d.prompt_tokens} / out ${d.completion_tokens} / cached ${d.cached_tokens}`;
            case 'compaction/summary': return `压缩至 seq ${d.covers_to_seq}`;
            case 'agent/error': return d.message || '';
            case 'agent/interrupt': return `中断于 ${d.where || ''}`;
            case 'step/start': case 'step/end': return `step ${d.step ?? ''}`;
            case 'session/end-seed': return `分支自 ${(d.forked_from || '').slice(0, 8)}`;
            case 'subagent/start': return `派发 ${d.agent} → ${d.task || ''}`;
            case 'subagent/end':
                return `${d.agent} 完成，${d.steps} 步 / ${(d.usage || {}).total_tokens || 0} tokens`
                    + (d.stopped ? `（${d.stopped}）` : '');
            default: return '';
        }
    }

    /** One collapsed row. Built as a string so a full render stays one
     *  `innerHTML` write; `appendEvents` reuses it for the incremental path. */
    function eventRow(e) {
        return `
            <div class="hs-event${SURFACE.has(e.type) ? ' hs-event-surface' : ''}" data-seq="${e.seq}">
                <span class="hs-event-seq">${e.seq}</span>
                <span>
                    <span class="hs-event-type">${esc(e.type)}</span>
                    <span class="hs-event-detail">${esc(summarize(e).slice(0, 160))}</span>
                </span>
            </div>`;
    }

    /* Expanding a row is delegated to the container rather than bound per row.
       Per-row listeners forced every render to be a full rebuild — you cannot
       append to a list whose handlers were attached in the same pass — and a
       turn re-rendered the whole log on every streamed chunk. The live array
       is parked on the container so the handler can find a payload by seq
       without closing over the render that created the row. `onFork` rides
       along for the same reason: the listener is attached once, so closing
       over the first render's callback would silently ignore every later one. */
    function bindEventRows(container, onFork) {
        container.__events = container.__events || [];
        container.__onFork = onFork;
        if (container.__eventsBound) return;
        container.__eventsBound = true;
        container.addEventListener('click', (ev) => {
            const row = ev.target.closest('.hs-event');
            if (!row || !container.contains(row)) return;
            if (ev.target.closest('.hs-fork')) return;   // its own handler ran

            const existing = row.querySelector('.hs-event-json');
            if (existing) {
                existing.remove();
                row.querySelector('.hs-fork')?.remove();
                return;
            }
            const seq = Number(row.dataset.seq);
            const event = (container.__events || []).find((e) => e.seq === seq);
            if (!event) return;
            const pre = document.createElement('pre');
            pre.className = 'hs-event-json';
            pre.textContent = JSON.stringify(event.data, null, 2);
            row.appendChild(pre);

            const fork = container.__onFork;
            if (fork && FORKABLE.has(event.type)) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'hs-fork';
                btn.textContent = `从 seq ${seq} 分支`;
                btn.addEventListener('click', (e2) => { e2.stopPropagation(); fork(seq); });
                row.appendChild(btn);
            }
        });
    }

    /** Raw event stream tab. `onFork(seq)` is called from a row's fork button. */
    function renderEvents(container, events, onFork) {
        bindEventRows(container, onFork);
        container.__events = events;
        container.__rendered = events.length;
        if (!events.length) {
            container.innerHTML = '<div class="hs-empty">还没有事件</div>';
            container.__rendered = 0;
            return;
        }
        container.innerHTML = events.map(eventRow).join('');
    }

    /** Append whatever `events` has gained since the last render.
     *
     *  This is what keeps a streaming turn linear. Re-rendering the whole log
     *  per event is quadratic, and measurably so: by 2000 events a single
     *  event cost 24ms to draw and the turn had spent 30s blocking the main
     *  thread — while chunks arrive every 32 characters, far faster than that.
     *  The page stopped responding, which is what this is here to prevent. */
    function appendEvents(container, events, onFork) {
        if (container.__events !== events || container.__rendered == null) {
            renderEvents(container, events, onFork);
            return;
        }
        const from = container.__rendered;
        if (events.length <= from) return;
        if (from === 0) { renderEvents(container, events, onFork); return; }
        container.insertAdjacentHTML('beforeend',
            events.slice(from).map(eventRow).join(''));
        container.__rendered = events.length;
    }

    /** Model-visible messages tab — the projection, verbatim from the server. */
    function renderDerived(container, messages) {
        if (!messages.length) {
            container.innerHTML = '<div class="hs-empty">还没有消息</div>';
            return;
        }
        container.innerHTML = messages.map((m) => {
            const calls = (m.tool_calls || [])
                .map((c) => `${c.function?.name}(${c.function?.arguments || ''})`)
                .join('\n');
            const body = [m.content, calls].filter(Boolean).join('\n');
            return `
                <div class="hs-derived">
                    <div class="hs-derived-role">${esc(m.role)}${m.tool_call_id ? ` · ${esc(m.tool_call_id)}` : ''}</div>
                    <div class="hs-derived-body">${esc(body)}</div>
                </div>`;
        }).join('');
    }

    /** Files tab: what the agent produced, and how to get it out.
     *
     *  `onDownload` is passed in rather than using plain <a href>: the API
     *  needs the auth header, so the fetch happens in harness.js and the blob
     *  is handed to the browser. */
    function renderFiles(container, files, onDownload, onArchive) {
        if (!files) {
            container.innerHTML = '<div class="hs-empty">读取中…</div>';
            return;
        }
        if (!files.length) {
            container.innerHTML = '<div class="hs-empty">这个会话还没有产出文件</div>';
            return;
        }

        // Skill scripts are unpacked into the workspace but are not the user's
        // work product, so they sit in their own group at the bottom.
        const work = files.filter((f) => !f.is_skill_asset);
        const assets = files.filter((f) => f.is_skill_asset);

        const row = (f) => `
            <div class="hs-file" data-path="${esc(f.path)}">
                <div class="hs-file-main">
                    <span class="hs-file-name">${esc(f.path)}</span>
                    <span class="hs-file-meta">${formatBytes(f.size_bytes)} · ${formatWhen(f.modified_at)}</span>
                </div>
                <button type="button" class="btn btn-sm" data-download="${esc(f.path)}">下载</button>
            </div>`;

        container.innerHTML = `
            <div class="hs-file-actions">
                <button type="button" class="btn btn-sm btn-primary" data-archive="1">
                    打包下载全部（${files.length}）
                </button>
            </div>
            ${work.map(row).join('')}
            ${assets.length ? `<div class="section-label" style="padding:.7rem .35rem .2rem">
                技能装入的脚本</div>${assets.map(row).join('')}` : ''}`;

        container.querySelectorAll('[data-download]').forEach((b) => {
            b.addEventListener('click', () => onDownload(b.dataset.download));
        });
        container.querySelector('[data-archive]')?.addEventListener('click', onArchive);
    }

    function formatBytes(n) {
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        return `${(n / 1024 / 1024).toFixed(1)} MB`;
    }

    function formatWhen(ms) {
        const d = new Date(ms);
        return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} `
             + `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }

    /** Plugin panel tab: which tools and hooks are actually loaded. */
    function renderPlugins(container, registry) {
        if (!registry) {
            container.innerHTML = '<div class="hs-empty">读取中…</div>';
            return;
        }
        const tools = registry.tools.map((t) => `
            <div class="hs-plugin">
                <div>
                    <span class="hs-plugin-name">${esc(t.name)}</span>
                    <span class="hs-badge hs-badge-${esc(t.permission)}">${esc(t.permission)}</span>
                    <span class="hs-badge">${esc(t.module)}</span>
                </div>
                <div class="hs-plugin-desc">${esc(t.description)}</div>
            </div>`).join('');

        const hooks = registry.hooks.map((h) => `
            <div class="hs-plugin">
                <span class="hs-plugin-name">${esc(h.name)}</span>
                <span class="hs-badge">${esc(h.point)}</span>
            </div>`).join('');

        const skills = (registry.skills || []).map((s) => `
            <div class="hs-plugin">
                <div>
                    <span class="hs-plugin-name">${esc(s.name)}</span>
                    ${s.packaged ? '<span class="hs-badge">包</span>' : ''}
                    ${(s.keywords || []).map((k) => `<span class="hs-badge">${esc(k)}</span>`).join('')}
                </div>
                <div class="hs-plugin-desc">${esc(s.description)}</div>
                ${(s.files || []).length
                    ? `<div class="hs-plugin-desc">附带：${(s.files || []).map(esc).join(' · ')}</div>`
                    : ''}
            </div>`).join('') || '<div class="hs-empty">没有可用技能</div>';

        const agents = (registry.agents || []).map((a) => `
            <div class="hs-plugin">
                <div>
                    <span class="hs-plugin-name">${esc(a.name)}</span>
                    <span class="hs-badge">${esc(a.label)}</span>
                    <span class="hs-badge">${a.max_steps} 步</span>
                </div>
                <div class="hs-plugin-desc">${esc(a.description)}</div>
                <div class="hs-plugin-desc">${(a.tools || []).map(esc).join(' · ')}</div>
            </div>`).join('') || '<div class="hs-empty">子代理未启用</div>';

        container.innerHTML = `
            <div class="hs-plugin">
                <div class="hs-plugin-desc">
                    运行模式 <b>${esc(registry.preset)}</b><br>
                    模型 <b>${esc(registry.model)}</b><br>
                    shell ${registry.shell_enabled ? '已启用' : '已关闭（HARNESS_SHELL_ENABLED）'}
                </div>
            </div>
            <div class="section-label" style="padding:.6rem .35rem .2rem">TOOLS</div>${tools}
            <div class="section-label" style="padding:.6rem .35rem .2rem">SKILLS</div>${skills}
            <div class="section-label" style="padding:.6rem .35rem .2rem">AGENTS</div>${agents}
            <div class="section-label" style="padding:.6rem .35rem .2rem">HOOKS</div>${hooks}`;
    }

    /** Token and cost readout above the tabs. */
    function renderUsage(container, usage) {
        if (!usage) { container.innerHTML = ''; return; }
        // A null cost means some model in this session has no published rate.
        // Showing "—" is honest; showing 0 would not be. But a bare dash gives
        // no way to tell a missing table entry from a broken calculation, so
        // name the model — that is the whole fix, and it is a one-line edit to
        // harness/data/pricing.json.
        const unknown = usage.cost_usd === null || usage.cost_usd === undefined;
        const missing = usage.unpriced_models || [];
        const cost = unknown ? '—' : `$${usage.cost_usd.toFixed(4)}`;
        const why = unknown && missing.length
            ? ` title="${esc(missing.join('、'))} 不在 harness/data/pricing.json 的价目表里，`
                + `补上该模型的费率即可显示"`
            : '';
        container.innerHTML = `
            <span>请求 <b>${usage.requests}</b></span>
            <span>输入 <b>${usage.prompt_tokens}</b></span>
            <span>输出 <b>${usage.completion_tokens}</b></span>
            <span>命中缓存 <b>${usage.cached_tokens}</b></span>
            <span${why}>预估费用 <b>${cost}</b></span>`;
    }

    window.HarnessTrajectory = {
        renderEvents, appendEvents, renderDerived, renderPlugins, renderFiles, renderUsage,
        summarize,
    };
})();
