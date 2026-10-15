/* focus.js — page script for focus.html.
   Uses escHtml / makeSkeleton from js/core/utils.js. */
const state = { cat: 'finance', src: null, data: null };
let focusRefreshTimer;
let focusLoading = false;

function getCurrentSections() {
    const categories = state.data?.categories || {};
    const catData = categories[state.cat] || {};
    return catData.sections || {};
}

function flattenSourceCards(sections, source) {
    const sourceSections = sections[source] || [];
    const cards = [];
    sourceSections.forEach(sec => {
        (sec.items || []).forEach(item => {
            const title = String(item.title || '').trim();
            if (!title) return;
            cards.push({
                title,
                url: String(item.url || '#').trim() || '#',
                section: sec.section || '',
                published_at: item.published_at,
                date_precision: item.date_precision,
            });
        });
    });
    return cards;
}

function renderSrcTabs() {
    const sections = getCurrentSections();
    const sources = Object.keys(sections || {}).filter(s => flattenSourceCards(sections, s).length > 0);
    if (!sources.length) {
        state.src = null;
        document.getElementById('src-tabs').innerHTML = '';
        return;
    }
    if (!state.src || !sources.includes(state.src)) state.src = sources[0];

    const el = document.getElementById('src-tabs');
    el.innerHTML = sources.map(s => `<button class="src-tab ${s === state.src ? 'active' : ''}" data-src="${escHtml(s)}">${escHtml(s)}</button>`).join('');
    el.querySelectorAll('.src-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            state.src = btn.dataset.src;
            renderSrcTabs();
            renderNews();
        });
    });
}

function renderNews() {
    const grid = document.getElementById('news-grid');
    const sections = getCurrentSections();
    const cards = state.src ? flattenSourceCards(sections, state.src) : [];

    if (!cards.length) {
        const message = state.data?.refreshing ? '正在抓取近期新闻，请稍候…'
            : state.data?.status === 'disabled' ? '自动抓取未启用，请由管理员开启数据同步。'
            : state.data?.status === 'error' ? '新闻源暂时无法更新，请稍后重试。'
            : '暂无符合时效要求的新闻，请切换分类或稍后重试。';
        grid.innerHTML = `<div class="empty-state">${escHtml(message)}</div>`;
        return;
    }

    grid.innerHTML = cards.slice(0, 60).map(item => `
        <article class="news-card">
            <div class="news-title">${escHtml(item.title)}</div>
            <div class="news-src">${escHtml(state.src)}${item.section ? ' · ' + escHtml(item.section) : ''}</div>
            ${item.published_at ? `<time class="news-src" datetime="${escHtml(item.published_at)}">发布于 ${escHtml(item.date_precision === 'day' ? new Date(item.published_at).toLocaleDateString('zh-CN', {timeZone:'UTC'}) : formatDateTime(item.published_at))}</time>` : ''}
            <a class="news-link" href="${escHtml(item.url)}" target="_blank" rel="noopener">查看详情 →</a>
        </article>
    `).join('');
}

async function loadFocus() {
    if (focusLoading) return;
    focusLoading = true;
    clearTimeout(focusRefreshTimer);
    const grid = document.getElementById('news-grid');
    document.getElementById('updated-time').textContent = '加载中...';
    grid.innerHTML = makeSkeleton(9, '120px');
    try {
        state.data = await apiFetch('/feeds/focus');
        const t = state.data?.savedAt ? formatDateTime(state.data.savedAt) : '';
        const status = state.data.refreshing ? '正在同步最新来源' : state.data.status === 'error' ? '本次同步失败'
            : state.data.stale ? '缓存待更新' : '';
        document.getElementById('updated-time').textContent = [t ? `来源同步于 ${t}` : '尚未完成同步', status].filter(Boolean).join(' · ');
        document.getElementById('news-window').textContent = `仅展示近 ${state.data.max_age_hours || 72} 小时内、可核实发布时间的新闻。`;
        renderSrcTabs();
        renderNews();
        if (state.data.refreshing) focusRefreshTimer = setTimeout(loadFocus, 5000);
    } catch (e) {
        grid.innerHTML = `<div class="empty-state error">加载失败: ${escHtml(e.message)}</div>`;
    } finally {
        focusLoading = false;
    }
}

document.querySelectorAll('.cat-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        state.cat = tab.dataset.cat;
        state.src = null;
        document.querySelectorAll('.cat-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        renderSrcTabs();
        renderNews();
    });
});

document.addEventListener('DOMContentLoaded', () => {
    loadFocus();
    document.getElementById('focus-reload').addEventListener('click', loadFocus);
    setInterval(loadFocus, 5 * 60 * 1000);
});
