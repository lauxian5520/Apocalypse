/* papers.js — page script for papers.html.
   Uses escHtml / makeSkeleton from js/core/utils.js. */
const state = { source: 'hf', period: 'daily', hfData: null, arxivData: [] };

function renderList() {
    const grid = document.getElementById('papers-grid');
    grid.className = 'grid-3'; 

    if (state.source === 'arxiv') {
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
                        <span style="color:var(--accent)">#</span> ${name}
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

    // Bind AI summary buttons
    grid.querySelectorAll('.ai-sum-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            await window.aiSummarize(btn.dataset.text, btn.dataset.ctx || '论文', btn);
        });
    });
}

async function loadData() {
    const grid = document.getElementById('papers-grid');
    grid.innerHTML = makeSkeleton(9, '160px');

    try {
        if (state.source === 'hf') {
            const res = await apiFetch(`/feeds/papers/classified?period=${state.period}`);
            state.hfData = res;
            if (!state.arxivData.length) {
                const sRes = await apiFetch(`/feeds/papers?period=daily`);
                state.arxivData = sRes.data?.scholar || []; 
            }
        } else {
            const res = await apiFetch(`/feeds/papers?period=${state.period}`);
            state.arxivData = res.data?.scholar || []; 
        }
        renderList();
    } catch (e) {
        grid.innerHTML = '<div class="empty-state error">加载失败: ' + escHtml(e.message) + '</div>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
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
