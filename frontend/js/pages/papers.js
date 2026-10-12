/* papers.js — page script for papers.html.
   Uses escHtml / makeSkeleton from js/core/utils.js. */
const state = { source: 'hf', period: 'daily', hfData: null, arxivData: [],
    venue: 'all', year: new Date().getFullYear() - 1, area: 'all', query: '', conferenceData: null };
let loadVersion = 0;
let conferenceCatalog = [];
let conferenceSearchTimer;

function updateConferenceYears() {
    const select = document.getElementById('conference-year');
    const entry = conferenceCatalog.find(venue => venue.id === state.venue);
    const allYears = Array.from({ length: new Date().getFullYear() - 2019 }, (_, index) => new Date().getFullYear() - index);
    const years = entry?.years?.length ? entry.years : allYears;
    if (!years.includes(state.year)) state.year = years.find(year => year <= state.year) ?? years[0];
    select.replaceChildren(...years.map(year => new Option(String(year), String(year))));
    select.value = String(state.year);
}

function updateConferenceCatalog(venues) {
    if (!Array.isArray(venues) || !venues.length) return;
    conferenceCatalog = venues;
    const select = document.getElementById('conference-venue');
    select.replaceChildren(new Option('全部会议', 'all'), ...venues.map(venue =>
        new Option(venue.id === 'neurips' ? 'NeurIPS（NIPS）' : venue.name, venue.id)));
    if (state.venue !== 'all' && !venues.some(venue => venue.id === state.venue)) state.venue = 'all';
    select.value = state.venue;
    updateConferenceYears();
}

async function loadConferenceCatalog() {
    try {
        const data = await apiFetch('/feeds/papers/conferences/catalog');
        updateConferenceCatalog(data.conferences);
    } catch (_) {
        // Paper responses also carry the registry, so a successful load can recover.
        if (!conferenceCatalog.length && state.source === 'conferences') document.getElementById('conference-status').textContent = '会议目录暂时无法加载，请刷新重试。';
    }
}

function renderList() {
    const grid = document.getElementById('papers-grid');
    grid.className = 'grid-3'; 

    if (state.source === 'conferences') {
        renderConferences();
        return;
    } else if (state.source === 'arxiv') {
        document.getElementById('period-tabs').style.display = 'flex'; // Arxiv also uses period filters
        if (!state.arxivData.length) {
            grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">暂无 ArXiv 论文数据</div>';
            return;
        }
        grid.innerHTML = state.arxivData.map(p => renderPaperCard(p)).join('');
    } else { // HuggingFace
        document.getElementById('period-tabs').style.display = 'flex'; // HF also uses period filters
        if (!state.hfData || !state.hfData.items || !state.hfData.items.length) {
            grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">暂无 HuggingFace 论文数据</div>';
            return;
        }

        const items = state.hfData.items;
        const categories = state.hfData.categories || [];
        const byIdx = new Map(items.map((it, i) => [i + 1, it]));
        let html = '';

        if (state.hfData.ai_fallback) {
            html += `<div class="empty-state" style="border:1px solid var(--border);padding:.7rem 1rem;margin-bottom:.6rem;text-align:left;grid-column:1/-1">AI分类暂不可用，已回退默认分类。</div>`;
        }

        if (categories.length > 0 && categories.some(c => Array.isArray(c.items) && c.items.length > 0)) {
            grid.className = ''; // Remove grid to use sections
            categories.forEach(c => {
                const name = c.name || '其他';
                const idxs = Array.isArray(c.items) ? c.items : [];
                const cards = idxs
                    .map(i => byIdx.get(i))
                    .filter(Boolean)
                    .map(it => renderPaperCard(it, name))
                    .join('');
                if (cards) {
                    html += `
                    <div style="margin:2rem 0 1rem;font-weight:600;font-size:1.2rem;color:var(--text-1);font-family:var(--font-display);display:flex;align-items:center;gap:0.5rem;">
                        <span style="color:var(--accent)">#</span> ${escHtml(name)}
                    </div>
                    <div class="grid-3">${cards}</div>
                    `;
                }
            });
        } else {
            html += items.map(p => renderPaperCard(p)).join('');
        }
        grid.innerHTML = html;
    }

    // AI summary buttons need no binding here: feeds.js delegates the click
    // once, for every card on the page whichever renderer produced it. Two
    // mechanisms for one job is what left the trending page with buttons
    // nobody had bound.
}

async function loadData() {
    clearTimeout(conferenceSearchTimer);
    const version = ++loadVersion;
    const grid = document.getElementById('papers-grid');
    document.getElementById('conference-controls').hidden = state.source !== 'conferences';
    document.getElementById('period-tabs').style.display = state.source === 'conferences' ? 'none' : 'flex';
    document.getElementById('conference-more').hidden = true;
    grid.className = 'grid-3';
    grid.innerHTML = makeSkeleton(9, '160px');

    try {
        if (state.source === 'conferences') {
            document.getElementById('conference-status').textContent = '正在加载官方论文目录，首次抓取可能需要一些时间…';
            const res = await fetchConferences(0);
            if (version !== loadVersion) return;
            state.conferenceData = res;
        } else if (state.source === 'hf') {
            const res = await apiFetch(`/feeds/papers/classified?period=${state.period}`);
            if (version !== loadVersion) return;
            state.hfData = res;
        } else {
            const res = await apiFetch(`/feeds/papers?period=${state.period}`);
            if (version !== loadVersion) return;
            state.arxivData = res.data?.scholar || []; 
        }
        renderList();
    } catch (e) {
        if (version !== loadVersion) return;
        grid.innerHTML = '<div class="empty-state error">加载失败: ' + escHtml(e.message) + '</div>';
        if (state.source === 'conferences') document.getElementById('conference-status').textContent = '加载失败，请切换筛选条件重试。';
    }
}

function fetchConferences(offset) {
    const params = new URLSearchParams({ venue: state.venue, year: state.year, area: state.area,
        q: state.query, offset, limit: 60 });
    return apiFetch(`/feeds/papers/conferences?${params}`);
}

function renderConferences() {
    const data = state.conferenceData;
    if (!data) return;
    updateConferenceCatalog(data.conferences);
    const grid = document.getElementById('papers-grid');
    const categories = data.categories || [];
    document.getElementById('conference-areas').innerHTML = [
        { id: 'all', name: '全部领域' }, ...categories,
    ].map(c => `<button type="button" class="tag ${state.area === c.id ? 'tag-active' : ''}" data-area="${escHtml(c.id)}" aria-pressed="${state.area === c.id}">${escHtml(c.name)}${c.count !== undefined ? ` (${c.count})` : ''}</button>`).join('');
    document.getElementById('conference-status').textContent =
        `共 ${data.total} 篇，已加载 ${data.items.length} 篇。` +
        (data.updated_at ? ` 更新于 ${new Date(data.updated_at).toLocaleString()}。` : '') +
        (data.warnings || []).join('；');
    if (!data.items.length) {
        grid.className = 'grid-3';
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">暂无符合条件的论文；当年目录可能尚未发布，请切换年份或筛选条件。</div>';
    } else {
        grid.className = '';
        grid.innerHTML = categories.filter(c => state.area === 'all' || c.id === state.area).map(c => {
            const items = data.items.filter(p => p.areas.includes(c.id));
            if (!items.length) return '';
            return `<section><h2 class="conference-heading">${escHtml(c.name)}</h2><div class="grid-3">${items.map(p => renderPaperCard(p, c.name)).join('')}</div></section>`;
        }).join('');
    }
    document.getElementById('conference-more').hidden = data.items.length >= data.total;
}

document.addEventListener('DOMContentLoaded', () => {
    const yearSelect = document.getElementById('conference-year');
    updateConferenceYears();
    loadConferenceCatalog();
    document.getElementById('conference-venue').addEventListener('change', e => { state.venue = e.target.value; updateConferenceYears(); loadData(); });
    yearSelect.addEventListener('change', e => { state.year = Number(e.target.value); loadData(); });
    document.getElementById('conference-areas').addEventListener('click', e => {
        const button = e.target.closest('[data-area]');
        if (button) { state.area = button.dataset.area; loadData(); }
    });
    document.getElementById('conference-query').addEventListener('input', e => {
        state.query = e.target.value;
        clearTimeout(conferenceSearchTimer);
        ++loadVersion;
        conferenceSearchTimer = setTimeout(loadData, 350);
    });
    document.getElementById('conference-more').addEventListener('click', async e => {
        const button = e.target;
        const version = loadVersion;
        button.disabled = true;
        try {
            const data = await fetchConferences(state.conferenceData.items.length);
            if (version !== loadVersion) return;
            state.conferenceData = { ...data, items: [...state.conferenceData.items, ...data.items] };
            renderConferences();
        } catch (err) {
            if (version === loadVersion) document.getElementById('conference-status').textContent = `加载失败：${err.message}`;
        } finally { button.disabled = false; }
    });
    // Source Tabs (HF vs Arxiv)
    document.querySelectorAll('.source-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.source-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.source = tab.dataset.source;
            loadData();
        });
    });

    // Period Tabs (Daily, Weekly, Monthly, Halfyear)
    document.querySelectorAll('.period-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.period-tab').forEach(t => t.classList.remove('tag-active'));
            tab.classList.add('tag-active');
            state.period = tab.dataset.period;
            loadData();
        });
    });

    loadData();
});
