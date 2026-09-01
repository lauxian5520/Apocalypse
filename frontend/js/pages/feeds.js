/* ═══════════════════════════════════════════════════════════════
   feeds.js — Card renderers + AI summarise, shared by the
   trending / papers / focus pages.
   Uses escHtml / makeSkeleton from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */

// ── GitHub Trending card ─────────────────────────────────────────
function renderGithubCard(item) {
    return `
    <a href="${escHtml(item.url)}" target="_blank" rel="noopener" class="card feed-card">
      <div class="feed-card-header">
        <span class="feed-icon">⚡</span>
        <h3 class="feed-title">${escHtml(item.name || '—')}</h3>
      </div>
      <p class="feed-desc">${escHtml(item.description || '暂无描述')}</p>
      <div class="feed-meta">
        ${item.ai_category ? `<span class="tag tag-active">${escHtml(item.ai_category)}</span>` : ''}
        ${item.language ? `<span class="tag">${escHtml(item.language)}</span>` : ''}
        <span>⭐ ${escHtml(item.stars || '0')}</span>
        <span>🍴 ${escHtml(item.forks || '0')}</span>
        ${item.stars_today ? `<span class="tag tag-active">+${escHtml(item.stars_today)}</span>` : ''}
        ${Auth.token() ? `<button class="btn btn-sm ai-sum-btn" style="margin-left:auto" onclick="event.preventDefault();event.stopPropagation();" data-text="${encodeURIComponent((item.name || '') + ' ' + (item.description || ''))}" data-ctx="GitHub项目">🤖 AI 总结</button>` : ''}
      </div>
    </a>
  `;
}

// ── HuggingFace model card ────────────────────────────────────────
function renderHFModelCard(item) {
    return `
    <a href="${escHtml(item.url)}" target="_blank" rel="noopener" class="card feed-card">
      <div class="feed-card-header">
        <span class="feed-icon">🤗</span>
        <h3 class="feed-title">${escHtml(item.name || item.id || '—')}</h3>
      </div>
      <p class="feed-desc author">by ${escHtml(item.author || 'unknown')}</p>
      ${item.pipeline_tag ? `<span class="tag" style="margin-bottom:.5rem">${escHtml(item.pipeline_tag)}</span>` : ''}
      <div class="feed-meta">
        ${item.ai_category ? `<span class="tag tag-active">${escHtml(item.ai_category)}</span>` : ''}
        <span>❤️ ${item.likes || 0}</span>
        <span>⬇️ ${(item.downloads || 0).toLocaleString()}</span>
        ${Auth.token() ? `<button class="btn btn-sm ai-sum-btn" style="margin-left:auto" onclick="event.preventDefault();event.stopPropagation();" data-text="${encodeURIComponent(item.name || item.id)}" data-ctx="AI模型">🤖 AI 总结</button>` : ''}
      </div>
    </a>
  `;
}

function renderGroupedCards(payload, renderFn) {
    const items = payload.items || [];
    const categories = payload.categories || [];
    if (!items.length) return '<div class="empty-state">暂无数据</div>';

    const byIdx = new Map(items.map((it, i) => [i + 1, it]));
    const sections = [];

    categories.forEach(c => {
        const name = c.name || '其他';
        const idxs = Array.isArray(c.items) ? c.items : [];
        const cards = idxs
            .map(i => byIdx.get(i))
            .filter(Boolean)
            .map(it => renderFn({ ...it, ai_category: name }))
            .join('');
        if (cards) {
            sections.push(`
        <div style="grid-column:1 / -1;margin:.4rem 0 .2rem;font-weight:600;color:var(--accent-2)"># ${escHtml(name)}</div>
        ${cards}
      `);
        }
    });

    return sections.join('') || items.map(renderFn).join('');
}

async function loadClassifiedFeed(endpoint, gridId, renderFn) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = makeSkeleton(9, '150px');
    try {
        const payload = await apiFetch('/' + endpoint);
    const warn = payload.ai_fallback
      ? `<div class="empty-state" style="grid-column:1/-1;border:1px solid var(--border);padding:.7rem 1rem;margin-bottom:.6rem">AI分类暂不可用，已回退默认分类。原因: ${escHtml(payload.ai_error || 'unknown')}</div>`
      : '';
    grid.innerHTML = warn + renderGroupedCards(payload, renderFn);
    } catch (e) {
        grid.innerHTML = `<div class="empty-state error">AI分类加载失败: ${escHtml(e.message)}</div>`;
    }
}

// ── Paper card ───────────────────────────────────────────────────
function renderPaperCard(item, catName = '') {
    const aiBtn = Auth.token()
        ? `<button class="btn btn-sm ai-sum-btn" style="margin-left:auto" onclick="event.preventDefault();event.stopPropagation();" data-text="${encodeURIComponent((item.title || '') + '. ' + (item.abstract || ''))}" data-ctx="论文">🤖 AI 总结</button>`
        : '';
    const authors = Array.isArray(item.authors) ? item.authors.join(', ') : '';
    
    return `
    <a href="${escHtml(item.url)}" target="_blank" rel="noopener" class="card feed-card" style="display:flex;flex-direction:column">
      <div class="feed-card-header">
        <span class="feed-icon">${item.upvotes !== undefined ? '📄' : '🎓'}</span>
        <h3 class="feed-title">${escHtml(item.title || '—')}</h3>
      </div>
      <p class="feed-desc" style="display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden">${escHtml(item.abstract || '暂无摘要')}</p>
      <div class="feed-meta" style="margin-top:auto">
        ${catName ? `<span class="tag tag-active">${escHtml(catName)}</span>` : ''}
        ${item.year ? `<span class="tag">${escHtml(item.year)}</span>` : ''}
        ${authors ? `<span class="tag" style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(authors)}">👤 ${escHtml(authors)}</span>` : ''}
        <span>${item.upvotes !== undefined ? '👍 ' + item.upvotes : (item.citations !== undefined && item.citations !== 0 ? '引用 ' + item.citations : '')}</span>
        ${aiBtn}
      </div>
    </a>
  `;
}

// ── Focus / news card ─────────────────────────────────────────────
function renderFocusCard(item) {
    return `
    <a href="${escHtml(item.url)}" target="_blank" rel="noopener" class="card feed-card">
      <div class="feed-card-header">
        <span class="tag">${escHtml(item.source || 'news')}</span>
      </div>
      <p class="feed-title feed-news-title">${escHtml(item.title)}</p>
      <div class="feed-meta" style="margin-top:0.8rem">
        ${Auth.token() ? `<button class="btn btn-sm ai-sum-btn" onclick="event.preventDefault();event.stopPropagation();" data-text="${encodeURIComponent(item.title)}" data-ctx="新闻">🤖 AI 总结</button>` : ''}
      </div>
    </a>
  `;
}

// ── AI summarize ─────────────────────────────────────────────────
async function aiSummarize(text, context, btn) {
    if (!Auth.token()) { toast('请先登录', 'error'); return; }
    let resultEl = btn.closest('.feed-card').querySelector('.ai-sum-result');
    if (!resultEl) {
        resultEl = document.createElement('div');
        resultEl.className = 'ai-sum-result';
        btn.closest('.feed-meta').after(resultEl);
    }

    btn.disabled = true;
    resultEl.innerHTML = '<div class="skeleton" style="height:40px"></div>';
    try {
        const r = await apiFetch('/ai/summarize', {
            method: 'POST',
            body: JSON.stringify({ text: decodeURIComponent(text), context }),
        });
        resultEl.innerHTML = `<div class="ai-result">🤖 ${escHtml(r.summary)}</div>`;
        btn.textContent = '✅ 已总结';
    } catch (e) {
        resultEl.innerHTML = `<div class="ai-result error">总结失败: ${escHtml(e.message)}</div>`;
        btn.disabled = false;
    }
}

// ── Load feed ─────────────────────────────────────────────────────
async function loadFeed(endpoint, gridId, renderFn, params = '') {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = makeSkeleton(9, '150px');
    try {
        const data = await apiFetch('/' + endpoint + params);
        const items = Array.isArray(data) ? data : (data.data || []);
        if (!items.length) { grid.innerHTML = '<div class="empty-state">暂无数据</div>'; return; }
        grid.innerHTML = items.map(renderFn).join('');
        // Bind AI summarize buttons
        grid.querySelectorAll('.ai-sum-btn').forEach(btn => {
            btn.addEventListener('click', e => {
                e.preventDefault();
                aiSummarize(btn.dataset.text, btn.dataset.ctx, btn);
            });
        });
    } catch (e) {
        grid.innerHTML = `<div class="empty-state error">加载失败: ${escHtml(e.message)}</div>`;
    }
}

// ── Init per page ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await Auth.init();
    const page = document.body.dataset.page;

    if (page === 'trending') {
        let period = 'daily';
      loadClassifiedFeed('feeds/github/classified?period=daily', 'github-grid', renderGithubCard);
      loadClassifiedFeed('feeds/huggingface/classified', 'hf-grid', renderHFModelCard);
        document.querySelectorAll('.period-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.period-tab').forEach(t => t.classList.remove('tag-active'));
                tab.classList.add('tag-active');
                period = tab.dataset.period;
          loadClassifiedFeed(`feeds/github/classified?period=${period}`, 'github-grid', renderGithubCard);
            });
        });
    }

    if (page === 'papers') {
        loadFeed('feeds/papers', 'papers-grid', renderPaperCard);
    }

    if (page === 'focus') {
        loadFeed('feeds/focus', 'focus-grid', renderFocusCard);
        // Auto refresh every 5 min
        setInterval(() => loadFeed('feeds/focus', 'focus-grid', renderFocusCard), 5 * 60 * 1000);
    }
});
