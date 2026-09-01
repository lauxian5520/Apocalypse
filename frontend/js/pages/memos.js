/* ═══════════════════════════════════════════════════════════════
   memos.js — Memo timeline: composing, comments, AI explain.
   Uses escHtml / formatTime / makeSkeleton from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */

let page = 1, totalPages = 1, isLoading = false;
let selectedImages = [];
let locatingInProgress = false;
let locationEnabled = false;

function setLocationUI(text, value = '') {
    const hidden = document.getElementById('memo-location');
    const label = document.getElementById('memo-location-text');
    const btn = document.getElementById('memo-locate-btn');
    if (hidden) hidden.value = value;
    if (label) label.textContent = text;
    if (btn) {
        btn.textContent = locatingInProgress ? '📍 定位中…' : '📍 重新定位';
        btn.disabled = !locationEnabled || locatingInProgress;
    }
}

function setLocationEnabled(enabled) {
    locationEnabled = enabled;
    locatingInProgress = false;
    if (!enabled) {
        setLocationUI('定位未开启', '');
        return;
    }
    setLocationUI('位置功能已开启，点击重新定位获取位置', '');
    locateUser(true);
}

async function reverseGeocode(lat, lon) {
    const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}&accept-language=zh-CN`;
    const res = await fetch(url, {
        headers: { 'Accept': 'application/json' },
    });
    if (!res.ok) throw new Error('reverse geocode failed');
    const data = await res.json();
    const addr = data.address || {};
    const city = addr.city || addr.town || addr.village || addr.county || addr.state || '';
    const suburb = addr.suburb || addr.city_district || addr.neighbourhood || '';
    const display = [city, suburb].filter(Boolean).join(' · ') || data.display_name || '';
    return display;
}

async function ipLocateCity() {
    const sources = [
        'https://ipapi.co/json/',
        'https://ipwho.is/',
    ];

    for (const url of sources) {
        try {
            const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
            if (!res.ok) continue;
            const data = await res.json();

            // Normalize fields from different providers
            const city = data.city || data.region || data.region_name || '';
            const country = data.country_name || data.country || '';
            const value = [country, city].filter(Boolean).join(' · ') || '';
            if (value) return value;
        } catch (_) {
            // Try next provider.
        }
    }
    throw new Error('ip locate failed');
}

async function locateUser(auto = false) {
    const hidden = document.getElementById('memo-location');
    if (!hidden || locatingInProgress || !locationEnabled) return;
    if (!navigator.geolocation) {
        setLocationUI('当前浏览器不支持定位，可直接发帖');
        return;
    }

    locatingInProgress = true;
    setLocationUI(auto ? '正在自动定位…' : '正在重新定位…');

    navigator.geolocation.getCurrentPosition(async (pos) => {
        try {
            const { latitude, longitude } = pos.coords;
            let place = '';
            try {
                place = await reverseGeocode(latitude, longitude);
            } catch (_) {
                place = `坐标 ${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
            }
            locatingInProgress = false;
            setLocationUI(`已定位: ${place}`, place);
        } catch (_) {
            try {
                const ipPlace = await ipLocateCity();
                locatingInProgress = false;
                setLocationUI(`定位回退(IP): ${ipPlace}`, ipPlace);
            } catch (_) {
                locatingInProgress = false;
                setLocationUI('定位失败，可点击重新定位');
            }
        }
    }, async () => {
        try {
            const ipPlace = await ipLocateCity();
            locatingInProgress = false;
            setLocationUI(`定位回退(IP): ${ipPlace}`, ipPlace);
        } catch (_) {
            locatingInProgress = false;
            setLocationUI('定位权限被拒绝，且 IP 回退失败');
        }
    }, {
        enableHighAccuracy: false,
        timeout: 10000,
        maximumAge: 300000,
    });
}

// ── Render a single memo card ────────────────────────────────────
function renderMemoCard(memo) {
        const currentUser = Auth.user();
        const isOwn = currentUser?.id === memo.user_id;
        const isAdmin = Auth.isAdmin();
        const canDelete = isOwn || isAdmin;

        const memoAuthorIdText = memo.user_id
            ? `<a href="profile.html?uid=${memo.user_id}" class="js-user-link" data-uid="${memo.user_id}">ID: ${memo.user_id}</a>`
            : 'ID: 匿名';
        const memoAuthorName = memo?.author?.username || '匿名用户';
        const memoAuthorAvatar = memo?.author?.avatar_url || 'assets/images/default-avatar.svg';
        const memoAuthorProfileHref = memo.user_id ? `profile.html?uid=${memo.user_id}` : '';

    const images = (memo.attachments || []).map(a =>
        `<div class="memo-image-item"><img src="${escHtml(a.url)}" alt="${escHtml(a.original_name)}" class="memo-img" data-url="${escHtml(a.url)}" loading="lazy"><a class="btn btn-sm" href="${escHtml(a.url)}" download>保存</a></div>`
    ).join('');

    const comments = (memo.comments || []).map(c => `
    <div class="comment-item">
            ${c.user_id ? `<a href="profile.html?uid=${c.user_id}" class="js-user-link" data-uid="${c.user_id}" title="查看资料"><img src="${escHtml(c.author.avatar_url || 'assets/images/default-avatar.svg')}" class="avatar" width="28" height="28"></a>` : `<img src="${escHtml(c.author.avatar_url || 'assets/images/default-avatar.svg')}" class="avatar" width="28" height="28">`}
      <div class="comment-body">
                <span class="comment-author">${c.user_id ? `<a href="profile.html?uid=${c.user_id}" class="js-user-link" data-uid="${c.user_id}">${escHtml(c.author.username)}</a>` : escHtml(c.author.username)}${c.user_id ? `（<a href="profile.html?uid=${c.user_id}" class="js-user-link" data-uid="${c.user_id}">ID: ${c.user_id}</a>）` : ''}</span>
        <span class="comment-content">${escHtml(c.content)}</span>
        ${c.image_url ? `<div style="margin-top:.45rem"><img src="${escHtml(c.image_url)}" class="memo-img" data-url="${escHtml(c.image_url)}" style="width:120px;height:120px"><div style="margin-top:.35rem"><a class="btn btn-sm" href="${escHtml(c.image_url)}" download>保存图片</a></div></div>` : ''}
        ${Auth.user()?.id === c.user_id ?
            `<button class="comment-del-btn" data-cid="${c.id}" data-mid="${memo.id}">删除</button>` : ''}
      </div>
    </div>
  `).join('');

    return `
    <article class="card memo-card" data-id="${memo.id}">
      <div class="memo-header">
                ${memoAuthorProfileHref ? `<a href="${memoAuthorProfileHref}" class="js-user-link" data-uid="${memo.user_id}" title="查看资料"><img src="${escHtml(memoAuthorAvatar)}" class="avatar" width="40" height="40"></a>` : `<img src="${escHtml(memoAuthorAvatar)}" class="avatar" width="40" height="40">`}
        <div class="memo-author-info">
                    <div class="memo-author-name">${memoAuthorProfileHref ? `<a href="${memoAuthorProfileHref}" class="js-user-link" data-uid="${memo.user_id}">${escHtml(memoAuthorName)}</a>` : escHtml(memoAuthorName)}</div>
                    <div class="memo-time">${memoAuthorIdText}${memo.is_anonymous ? ' · 匿名帖' : ''}</div>
          <div class="memo-time">${formatTime(memo.created_at)}</div>
                    ${memo.location ? `<div class="memo-location">📍 ${escHtml(memo.location)}</div>` : ''}
        </div>
        ${memo.pinned ? '<span class="memo-pin" title="置顶">📌</span>' : ''}
        <div class="memo-actions">
          <button class="btn btn-sm ai-explain-btn" data-mid="${memo.id}" title="AI 解释">🤖 AI</button>
                    ${canDelete ? `<button class="btn btn-sm btn-danger memo-del-btn" data-mid="${memo.id}">删除</button>` : ''}
        </div>
      </div>
      <div class="memo-content">${escHtml(memo.content)}</div>
      ${images ? `<div class="memo-images">${images}</div>` : ''}
      <div class="ai-explanation" id="ai-exp-${memo.id}" style="display:none"></div>
      <div class="memo-footer">
        <span class="memo-comment-count">💬 ${memo.comment_count} 评论</span>
        <button class="btn btn-sm toggle-comments-btn" data-mid="${memo.id}">查看评论</button>
      </div>
      <div class="comments-section" id="comments-${memo.id}" style="display:none">
        <div class="comments-list">
          ${comments || '<div class="no-comments">暂无评论</div>'}
        </div>
        <form class="comment-form" data-mid="${memo.id}">
          <input type="text" placeholder="写下你的评论…" name="content" class="input comment-input" maxlength="500">
                    <label class="btn btn-sm" for="comment-image-${memo.id}">图片</label>
                    <input type="file" id="comment-image-${memo.id}" name="image" accept="image/*" style="display:none">
          <button type="submit" class="btn btn-primary btn-sm">发送</button>
        </form>
      </div>
    </article>
  `;
}


// ── Load memos ────────────────────────────────────────────────────
async function loadMemos(reset = false) {
    if (isLoading) return;
    isLoading = true;
    if (reset) { page = 1; document.getElementById('memos-list').innerHTML = makeSkeleton(4, '160px'); }

    try {
        const data = await apiFetch(`/memos?page=${page}&page_size=10&visibility=public`);
        const list = document.getElementById('memos-list');
        if (reset) list.innerHTML = '';
        if (data.items.length === 0 && reset) {
            list.innerHTML = '<div class="empty-state">暂无帖子，来发第一条吧！</div>';
            return;
        }
        data.items.forEach(m => list.insertAdjacentHTML('beforeend', renderMemoCard(m)));
        totalPages = Math.ceil(data.total / 10);
        document.getElementById('load-more-btn').style.display = page < totalPages ? 'block' : 'none';
        bindMemoEvents();
    } catch (e) {
        toast('加载失败: ' + e.message, 'error');
    } finally {
        isLoading = false;
    }
}

// ── Post new memo ─────────────────────────────────────────────────
async function submitMemo(e) {
    e.preventDefault();
    if (!Auth.token()) { toast('请先登录', 'error'); return; }

    const form = e.target;
    const content = form.querySelector('#memo-content').value.trim();
    if (!content) { toast('帖子内容不能为空', 'error'); return; }

    const fd = new FormData();
    fd.append('content', content);
    fd.append('location', (form.querySelector('#memo-location')?.value || '').trim());
    fd.append('visibility', form.querySelector('#memo-visibility').value);
    fd.append('is_anonymous', form.querySelector('#memo-anonymous')?.checked ? 'true' : 'false');
    const fileInput = form.querySelector('#memo-images');
    if (fileInput.files.length > 9) { toast('最多上传 9 张图片', 'error'); return; }
    for (const f of fileInput.files) fd.append('images', f);

    const btn = form.querySelector('.post-btn');
    btn.disabled = true; btn.textContent = '发布中…';
    try {
        const memo = await apiFetch('/memos', {
            method: 'POST',
            body: fd,
        });

        toast('发布成功！', 'success');
        form.reset();
        selectedImages = [];
        document.getElementById('image-preview').innerHTML = '';
        setLocationEnabled(false);
        document.getElementById('memos-list').insertAdjacentHTML('afterbegin', renderMemoCard(memo));
        bindMemoEvents();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        btn.disabled = false; btn.textContent = '发布';
    }
}

// ── Image preview ────────────────────────────────────────────────
function updateImagePreview(input) {
    const preview = document.getElementById('image-preview');
    if (!preview) return;

    selectedImages = Array.from(input.files);
    preview.innerHTML = '';
    selectedImages.forEach((f, idx) => {
        const url = URL.createObjectURL(f);
        preview.insertAdjacentHTML('beforeend', `<div class="img-preview-item"><img src="${url}"><span class="img-remove" data-idx="${idx}" title="移除">✕</span></div>`);
    });
}

function removeSelectedImage(index) {
    if (index < 0 || index >= selectedImages.length) return;
    selectedImages.splice(index, 1);

    const input = document.getElementById('memo-images');
    if (!input) return;

    const dt = new DataTransfer();
    selectedImages.forEach(f => dt.items.add(f));
    input.files = dt.files;
    updateImagePreview(input);
}

async function submitCommentForm(form) {
    if (!Auth.token()) { toast('请先登录', 'error'); return; }
    const mid = form.dataset.mid;
    const input = form.querySelector('input[name="content"]');
    const imgInput = form.querySelector('input[name="image"]');
    const content = (input?.value || '').trim();
    const hasImage = Boolean(imgInput?.files && imgInput.files.length > 0);
    if (!content && !hasImage) return;

    const sendBtn = form.querySelector('button[type="submit"]');
    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.textContent = '发送中…';
    }

    try {
        const fd = new FormData();
        fd.append('content', content);
        if (hasImage) fd.append('image', imgInput.files[0]);
        const c = await apiFetch(`/memos/${mid}/comments`, {
            method: 'POST',
            body: fd,
        });
        const list = form.closest('.comments-section').querySelector('.comments-list');
        list.querySelector('.no-comments')?.remove();
        const avatar = escHtml(c?.author?.avatar_url || 'assets/images/default-avatar.svg');
        const authorName = c?.author?.username || Auth.user()?.username || '用户';
        const uid = c?.user_id || Auth.user()?.id;
        list.insertAdjacentHTML('beforeend', `
          <div class="comment-item" data-cid="${c.id}">
            ${uid ? `<a href="profile.html?uid=${uid}" class="js-user-link" data-uid="${uid}" title="查看资料"><img src="${avatar}" class="avatar" width="28" height="28"></a>` : `<img src="${avatar}" class="avatar" width="28" height="28">`}
            <div class="comment-body">
              <span class="comment-author">${uid ? `<a href="profile.html?uid=${uid}" class="js-user-link" data-uid="${uid}">${escHtml(authorName)}</a>` : escHtml(authorName)}${uid ? `（<a href="profile.html?uid=${uid}" class="js-user-link" data-uid="${uid}">ID: ${uid}</a>）` : ''}</span>
              <span class="comment-content">${escHtml(c.content || content)}</span>
                            ${c?.image_url ? `<div style="margin-top:.45rem"><img src="${escHtml(c.image_url)}" class="memo-img" data-url="${escHtml(c.image_url)}" style="width:120px;height:120px"><div style="margin-top:.35rem"><a class="btn btn-sm" href="${escHtml(c.image_url)}" download>保存图片</a></div></div>` : ''}
              ${uid === Auth.user()?.id ? `<button class="comment-del-btn" data-cid="${c.id}" data-mid="${mid}">删除</button>` : ''}
            </div>
          </div>
        `);
        if (input) input.value = '';
                if (imgInput) imgInput.value = '';
        toast('评论成功', 'success');
        bindMemoEvents();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.textContent = '发送';
        }
    }
}

// ── Event delegation ──────────────────────────────────────────────
function bindMemoEvents() {
    // Toggle comments
    document.querySelectorAll('.toggle-comments-btn').forEach(btn => {
        btn.onclick = function () {
            const sec = document.getElementById('comments-' + this.dataset.mid);
            const show = sec.style.display === 'none';
            sec.style.display = show ? 'block' : 'none';
            this.textContent = show ? '收起评论' : '查看评论';
        };
    });

    // Delete comment
    document.querySelectorAll('.comment-del-btn').forEach(btn => {
        btn.onclick = async function () {
            if (!confirm('删除此评论？')) return;
            try {
                await apiFetch(`/memos/${this.dataset.mid}/comments/${this.dataset.cid}`, { method: 'DELETE' });
                this.closest('.comment-item').remove();
            } catch (err) { toast(err.message, 'error'); }
        };
    });

    // Delete memo
    document.querySelectorAll('.memo-del-btn').forEach(btn => {
        btn.onclick = async function () {
            if (!confirm('删除此帖子？')) return;
            try {
                await apiFetch('/memos/' + this.dataset.mid, { method: 'DELETE' });
                this.closest('.memo-card').remove();
                toast('已删除', 'success');
            } catch (err) { toast(err.message, 'error'); }
        };
    });

    // AI explain
    document.querySelectorAll('.ai-explain-btn').forEach(btn => {
        btn.onclick = async function () {
            const mid = this.dataset.mid;
            const card = this.closest('.memo-card');
            const expEl = document.getElementById('ai-exp-' + mid);
            if (expEl.style.display !== 'none') { expEl.style.display = 'none'; return; }
            if (!Auth.token()) { toast('请先登录可使用 AI 功能', 'error'); return; }

            const content = card.querySelector('.memo-content').textContent;
            const imgUrls = [...card.querySelectorAll('.memo-img')].map(i => i.dataset.url);
            expEl.style.display = 'block';
            expEl.innerHTML = '<div class="skeleton" style="height:60px"></div>';
            try {
                const r = await apiFetch('/ai/explain', {
                    method: 'POST', body: JSON.stringify({ content, image_urls: imgUrls }),
                });
                expEl.innerHTML = `<div class="ai-result">🤖 <b>AI 解析：</b><br>${escHtml(r.explanation)}</div>`;
            } catch (err) { expEl.innerHTML = `<div class="ai-result error">AI 解析失败: ${err.message}</div>`; }
        };
    });

    // Image lightbox
    document.querySelectorAll('.memo-img').forEach(img => {
        img.onclick = function () {
            const ov = document.createElement('div');
            ov.className = 'modal-overlay active';
            ov.innerHTML = `<div style="max-width:90vw;max-height:90vh"><img src="${this.src}" style="max-width:100%;max-height:90vh;border-radius:12px"></div>`;
            ov.addEventListener('click', () => ov.remove());
            document.body.appendChild(ov);
        };
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    await Auth.init();
    loadMemos(true);
    document.getElementById('load-more-btn')?.addEventListener('click', () => { page++; loadMemos(); });

    // Compose form is injected by memos.html after this file runs, so use delegation.
    document.addEventListener('submit', (e) => {
        if (e.target && e.target.id === 'memo-form') {
            submitMemo(e);
        }
    });

    document.addEventListener('change', (e) => {
        if (e.target && e.target.id === 'memo-images') {
            updateImagePreview(e.target);
        }
    });

    document.addEventListener('click', (e) => {
        if (e.target && e.target.classList.contains('img-remove')) {
            const idx = Number(e.target.dataset.idx || '-1');
            removeSelectedImage(idx);
        }
        if (e.target && e.target.id === 'memo-locate-btn') {
            locateUser(false);
        }
    });

    document.addEventListener('change', (e) => {
        if (e.target && e.target.id === 'memo-location-enabled') {
            setLocationEnabled(Boolean(e.target.checked));
        }
    });

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!form.classList.contains('comment-form')) return;
        e.preventDefault();
        submitCommentForm(form);
    });

    document.addEventListener('click', (e) => {
        const link = e.target.closest('.js-user-link');
        if (!link) return;
        const uid = link.dataset.uid;
        if (!uid) return;
        e.preventDefault();
        window.location.href = `profile.html?uid=${encodeURIComponent(uid)}`;
    });

    setLocationEnabled(false);
});

/* ── Compose box: rendered only for logged-in visitors ── */
// Render compose box only when logged in
document.addEventListener('DOMContentLoaded', async () => {
    const area = document.getElementById('compose-area');
    await Auth.init();
    if (Auth.token()) {
        area.innerHTML = `
  <div class="glass-panel compose-box">
    <form id="memo-form">
      <div class="field">
        <textarea class="textarea" id="memo-content" name="content" placeholder="记录此刻的想法…支持 Markdown" rows="4"></textarea>
      </div>
      <div id="image-preview"></div>
      <div class="compose-bottom">
        <label class="img-upload-btn" for="memo-images">🖼 添加图片</label>
        <input type="file" id="memo-images" accept="image/*" multiple>
                        <label class="location-toggle" title="仅在你开启后才会定位">
                            <input type="checkbox" id="memo-location-enabled">
                              发布时附带位置
                        </label>
                        <input type="hidden" id="memo-location" name="location" value="">
                        <button type="button" class="locate-btn" id="memo-locate-btn" disabled>📍 重新定位</button>
                        <span id="memo-location-text">定位未开启</span>
                        <label class="location-toggle" title="匿名后仅你和管理员可见你的ID">
                            <input type="checkbox" id="memo-anonymous">
                            匿名发布
                        </label>
        <select id="memo-visibility" name="visibility">
          <option value="public">🌐 公开</option>
          <option value="private">🔒 私密</option>
        </select>
        <button type="submit" class="btn btn-primary post-btn">发布</button>
      </div>
    </form>
  </div>`;
    } else {
        area.innerHTML = `
  <div class="login-prompt">
    <a href="login.html">登录</a> 或 <a href="register.html">注册</a> 后即可发帖、评论
  </div>`;
    }
});
