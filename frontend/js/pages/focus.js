/* focus.js — page script for focus.html.
   Uses escHtml / makeSkeleton from js/core/utils.js. */
const state = { cat: 'finance', src: null, data: null };

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
        grid.innerHTML = '<div class="empty-state">该模块暂无可展示新闻</div>';
        return;
    }

    grid.innerHTML = cards.slice(0, 60).map(item => `
        <article class="news-card">
            <div class="news-title">${escHtml(item.title)}</div>
            <div class="news-src">${escHtml(state.src)}${item.section ? ' · ' + escHtml(item.section) : ''}</div>
            <a class="news-link" href="${escHtml(item.url)}" target="_blank" rel="noopener">查看详情 →</a>
        </article>
    `).join('');
}

async function loadFocus() {
    const grid = document.getElementById('news-grid');
    document.getElementById('updated-time').textContent = '加载中...';
    grid.innerHTML = makeSkeleton(9, '120px');
    try {
        state.data = await apiFetch('/feeds/focus');
        const t = state.data?.savedAt ? new Date(state.data.savedAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '';
        document.getElementById('updated-time').textContent = t ? `更新于 ${t}` : '暂无更新时间';
        renderSrcTabs();
        renderNews();
    } catch (e) {
        grid.innerHTML = `<div class="empty-state error">加载失败: ${escHtml(e.message)}</div>`;
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
    setInterval(loadFocus, 5 * 60 * 1000);
});
