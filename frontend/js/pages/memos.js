/* ═══════════════════════════════════════════════════════════════
   memos.js — Memo timeline: composing, comments, AI explain.
   Uses escHtml / formatTime / makeSkeleton from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */

let page = 0, totalPages = 0, isLoading = false;
let selectedImages = [];
let previewImageUrls = [];
let currentVisibility = "public";
let memoLoadGeneration = 0;
let currentKeyword = '';
let memoSearchTimer = null;
let memoSearchComposing = false;
let locatingInProgress = false;
let locationEnabled = false;
const memoCache = new Map();
const memoEditors = new Map();
let nextEditImageId = 0;

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
function memoIcon(name) {
    return `<svg class="memo-icon" aria-hidden="true"><use href="#icon-${name}"/></svg>`;
}

function renderMemoLikes(likes = []) {
    return `${memoIcon('heart')}<p>${likes.map(user => `<a href="profile.html?uid=${user.id}" class="js-user-link" data-uid="${user.id}">${escHtml(user.username)}</a>`).join('<span class="like-name-separator">、</span>')}<span class="likes-caption">觉得很赞</span></p>`;
}

function renderComment(comment, memoId) {
    const name = comment.author?.username || '匿名用户';
    const avatar = escHtml(comment.author?.avatar_url || 'assets/images/default-avatar.svg');
    const profile = comment.user_id ? `profile.html?uid=${comment.user_id}` : '';
    return `<div class="comment-item" data-cid="${comment.id}">
        ${profile ? `<a href="${profile}" class="js-user-link" data-uid="${comment.user_id}" title="查看资料">` : ''}
        <img src="${avatar}" class="avatar" width="28" height="28" alt="${escHtml(name)}的头像">
        ${profile ? '</a>' : ''}
        <div class="comment-body">
            <span class="comment-author">${profile ? `<a href="${profile}" class="js-user-link" data-uid="${comment.user_id}">${escHtml(name)}</a>` : escHtml(name)}</span>
            <span class="comment-content">${escHtml(comment.content || '')}</span>
            ${comment.image_url ? `<div class="comment-image"><button class="memo-image-button" type="button" aria-label="查看评论图片"><img src="${escHtml(comment.image_url)}" class="memo-img" data-url="${escHtml(comment.image_url)}" alt="评论图片" loading="lazy"></button><a class="profile-link" href="${escHtml(comment.image_url)}" download>保存图片 ${memoIcon('download')}</a></div>` : ''}
            ${Auth.user()?.id === comment.user_id ? `<button type="button" class="comment-del-btn" data-cid="${comment.id}" data-mid="${memoId}">删除</button>` : ''}
        </div>
    </div>`;
}

function renderMemoCard(memo) {
    memoCache.set(String(memo.id), memo);
    const editor = memoEditors.get(String(memo.id));
    const canManage = Auth.user()?.id === memo.user_id || Auth.isAdmin();
    const name = memo.author?.username || '匿名用户';
    const avatar = memo.author?.avatar_url || 'assets/images/default-avatar.svg';
    const profile = memo.user_id ? `profile.html?uid=${memo.user_id}` : '';
    const isPrivate = memo.visibility === 'private';
    const images = (memo.attachments || []).map(attachment => `
        <div class="memo-image-item">
            <button class="memo-image-button" type="button" aria-label="查看图片：${escHtml(attachment.original_name || '笔记配图')}"><img src="${escHtml(attachment.url)}" alt="${escHtml(attachment.original_name || '笔记配图')}" class="memo-img" data-url="${escHtml(attachment.url)}" loading="lazy"></button>
            <a class="memo-image-save" href="${escHtml(attachment.url)}" download aria-label="保存图片" title="保存图片">${memoIcon('download')}</a>
        </div>`).join('');

    return `<article class="card memo-card${editor ? ' is-editing' : ''}" data-id="${memo.id}">
        <div class="memo-header">
            ${profile ? `<a href="${profile}" class="js-user-link" data-uid="${memo.user_id}" title="查看资料">` : ''}
            <img src="${escHtml(avatar)}" class="avatar" width="40" height="40" alt="${escHtml(name)}的头像">
            ${profile ? '</a>' : ''}
            <div class="memo-author-info">
                <div class="memo-author-line">
                    <span class="memo-author-name">${profile ? `<a href="${profile}" class="js-user-link" data-uid="${memo.user_id}">${escHtml(name)}</a>` : escHtml(name)}</span>
                    <span class="memo-author-id">${memo.is_anonymous ? '匿名发布' : (memo.user_id ? `ID: ${memo.user_id}` : '匿名用户')}</span>
                </div>
                <div class="memo-time"><time datetime="${escHtml(memo.created_at)}">${formatTime(memo.created_at)}</time><span>·</span><span class="${isPrivate ? 'memo-private-label' : ''}" ${isPrivate ? 'title="作者和管理员可见"' : ''}>${memoIcon(isPrivate ? 'lock' : 'globe')} ${isPrivate ? '私密 · 作者和管理员可见' : '公开'}</span></div>
            </div>
            ${memo.pinned ? `<span class="memo-pin">${memoIcon('pin')}<span>置顶</span></span>` : ''}
            ${canManage ? `<details class="memo-menu"><summary aria-label="更多笔记操作" title="更多笔记操作">${memoIcon('more')}</summary><div class="memo-menu-items">
                <button type="button" class="memo-edit-btn" data-mid="${memo.id}">编辑笔记</button>
                <button type="button" class="memo-visibility-btn" data-mid="${memo.id}" data-next="${isPrivate ? 'public' : 'private'}">${isPrivate ? '设为公开' : '设为私密'}</button>
                <button type="button" class="memo-del-btn" data-mid="${memo.id}">删除笔记</button>
            </div></details>` : ''}
        </div>
        ${editor ? renderMemoEditor(String(memo.id)) : ''}
        <div class="memo-content">${escHtml(memo.content)}</div>
        ${images ? `<div class="memo-images" data-count="${memo.attachments.length}">${images}</div>` : ''}
        ${memo.location ? `<div class="memo-location">${memoIcon('location')}${escHtml(memo.location)}</div>` : ''}
        <div class="ai-explanation" id="ai-exp-${memo.id}" style="display:none"></div>
        <div class="memo-footer">
            <button type="button" class="memo-footer-btn memo-like-btn${memo.liked_by_me ? ' is-liked' : ''}" data-mid="${memo.id}" aria-pressed="${Boolean(memo.liked_by_me)}" aria-label="${memo.liked_by_me ? '取消点赞' : '点赞'}">${memoIcon('heart')}<span>${memo.liked_by_me ? '已赞' : '赞'}</span><span class="memo-like-count">${memo.like_count || 0}</span></button>
            <button type="button" class="memo-footer-btn toggle-comments-btn" data-mid="${memo.id}" aria-expanded="false" aria-controls="comments-${memo.id}">${memoIcon('comment')}<span class="comment-toggle-label">评论</span><span class="memo-comment-count">${memo.comment_count || 0}</span></button>
            <button type="button" class="memo-footer-btn ai-explain-btn" data-mid="${memo.id}" aria-expanded="false" aria-controls="ai-exp-${memo.id}">${memoIcon('spark')}AI 解读</button>
        </div>
        <div class="memo-likes" aria-label="点赞的用户" aria-live="polite" ${memo.like_count ? '' : 'hidden'}>${renderMemoLikes(memo.likes)}</div>
        <div class="comments-section" id="comments-${memo.id}" style="display:none">
            <div class="comments-list">${(memo.comments || []).map(comment => renderComment(comment, memo.id)).join('') || '<div class="no-comments">还没有评论，聊聊你的想法吧。</div>'}</div>
            <form class="comment-form" data-mid="${memo.id}">
                <input type="text" placeholder="写下你的回应…" name="content" class="input comment-input" maxlength="500" aria-label="评论内容">
                <label class="btn btn-sm" for="comment-image-${memo.id}" title="添加评论图片">${memoIcon('image')}<span class="visually-hidden">添加图片</span></label>
                <input type="file" id="comment-image-${memo.id}" name="image" accept="image/*" hidden>
                <button type="submit" class="btn btn-primary btn-sm">发送</button>
                <span class="comment-image-status" role="status"></span>
            </form>
        </div>
    </article>`;
}

function syncVisibilityUI() {
    document.querySelectorAll('[data-visibility]').forEach(button => {
        const active = button.dataset.visibility === currentVisibility;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
    });
    document.getElementById('feed-title').innerHTML = `${currentVisibility === 'private' ? '私密笔记' : '笔记广场'}<span class="heading-period">.</span>`;
    document.getElementById('memo-feed-context').textContent = currentVisibility === 'private' ? '仅作者和管理员可见' : '公开分享的每一个瞬间';
}

function renderMemoEmptyState(kind) {
    const states = {
        search: ['search', '还没有找到这条灵感', '换个关键词试试，或回到全部笔记。', '清空搜索', 'data-clear-search'],
        empty: ['pen', '这里，等你的第一条笔记', '生活里的小事，脑海里的新想法，都值得记录。', '写一条笔记', 'data-compose-focus'],
        private: ['lock', '给思绪留一点私人空间', '发布私密笔记后，会在这里显示，仅作者和管理员可见。', '写一条笔记', 'data-compose-focus'],
        error: ['grid', '笔记暂时没有加载出来', '稍后再试一次，你的灵感还在这里。', '重新加载', 'data-retry-memos'],
        login: ['lock', '登录后，打开你的私密空间', '私密笔记仅作者和管理员可见。', '去登录', 'data-compose-focus'],
    };
    const [icon, title, description, action, attribute] = states[kind];
    return `<div class="empty-state"><span class="empty-state-icon">${memoIcon(icon)}</span><h2>${title}</h2><p>${description}</p><button type="button" class="btn btn-sm" ${attribute}>${action}</button></div>`;
}


// ── Load memos ────────────────────────────────────────────────────
async function loadMemos(reset = false) {
    if (isLoading && !reset) return;
    if (reset) clearTimeout(memoSearchTimer);
    const generation = ++memoLoadGeneration;
    const requestedPage = reset ? 1 : page + 1;
    const list = document.getElementById('memos-list');
    const loadMore = document.getElementById('load-more-btn');
    const status = document.getElementById('memo-search-status');
    isLoading = true;
    syncVisibilityUI();
    list.setAttribute('aria-busy', 'true');
    loadMore.disabled = true;
    status.classList.remove('is-error');
    status.textContent = currentKeyword ? '正在搜索笔记…' : '正在加载笔记…';
    if (reset) { page = 0; list.innerHTML = makeSkeleton(4, '160px'); loadMore.style.display = 'none'; }

    try {
        const params = new URLSearchParams({ page: requestedPage, page_size: 10, visibility: currentVisibility });
        if (currentKeyword) params.set('q', currentKeyword);
        const data = await apiFetch(`/memos?${params}`);
        if (generation !== memoLoadGeneration) return;
        page = requestedPage;
        totalPages = Math.ceil(data.total / 10);
        status.textContent = currentKeyword ? `共找到 ${data.total} 条匹配笔记` : `共 ${data.total} 条笔记`;
        if (reset) list.innerHTML = '';
        if (data.items.length === 0 && reset) {
            list.innerHTML = renderMemoEmptyState(currentKeyword ? 'search' : currentVisibility === 'private' ? 'private' : 'empty');
            return;
        }
        data.items.forEach(m => list.insertAdjacentHTML('beforeend', renderMemoCard(m)));
        loadMore.style.display = page < totalPages ? 'block' : 'none';
        bindMemoEvents();
    } catch (e) {
        if (generation !== memoLoadGeneration) return;
        if (reset) list.innerHTML = renderMemoEmptyState(currentVisibility === 'private' && !Auth.token() ? 'login' : 'error');
        status.textContent = '加载失败，请重新搜索或重试加载。';
        status.classList.add('is-error');
        toast('加载失败: ' + e.message, 'error');
    } finally {
        if (generation === memoLoadGeneration) {
            isLoading = false;
            loadMore.disabled = false;
            list.setAttribute('aria-busy', 'false');
        }
    }
}

function searchMemos(immediate = false) {
    clearTimeout(memoSearchTimer);
    currentKeyword = document.getElementById('memo-search').value.trim();
    document.getElementById('memo-search-clear').disabled = !document.getElementById('memo-search').value;
    // Invalidate a previous response as soon as the query changes, including the debounce window.
    ++memoLoadGeneration;
    document.getElementById('load-more-btn').style.display = 'none';
    const status = document.getElementById('memo-search-status');
    status.classList.remove('is-error');
    status.textContent = currentKeyword ? '正在搜索笔记…' : '正在加载笔记…';
    if (immediate) loadMemos(true);
    else memoSearchTimer = setTimeout(() => loadMemos(true), 300);
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
        form.querySelector('.compose-options').open = false;
        selectedImages = [];
        previewImageUrls.forEach(url => URL.revokeObjectURL(url));
        previewImageUrls = [];
        document.getElementById('image-preview').innerHTML = '';
        setLocationEnabled(false);
        currentVisibility = memo.visibility;
        form.querySelector('#memo-visibility').value = currentVisibility;
        await loadMemos(true);
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

    previewImageUrls.forEach(url => URL.revokeObjectURL(url));
    previewImageUrls = [];
    selectedImages = Array.from(input.files);
    preview.innerHTML = '';
    selectedImages.forEach((f, idx) => {
        const url = URL.createObjectURL(f);
        previewImageUrls.push(url);
        preview.insertAdjacentHTML('beforeend', `<div class="img-preview-item"><img src="${url}" alt="${escHtml(f.name)}"><button type="button" class="img-remove" data-idx="${idx}" title="移除" aria-label="移除图片 ${escHtml(f.name)}">✕</button></div>`);
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
        list.insertAdjacentHTML('beforeend', renderComment(c, mid));
        const count = form.closest('.memo-card').querySelector('.memo-comment-count');
        count.textContent = Number(count.textContent) + 1;
        if (input) input.value = '';
        if (imgInput) imgInput.value = '';
        form.querySelector('.comment-image-status').textContent = '';
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
            this.setAttribute('aria-expanded', String(show));
            this.querySelector('.comment-toggle-label').textContent = show ? '收起评论' : '评论';
        };
    });

    // Delete comment
    document.querySelectorAll('.comment-del-btn').forEach(btn => {
        btn.onclick = async function () {
            if (!confirm('删除此评论？')) return;
            try {
                await apiFetch(`/memos/${this.dataset.mid}/comments/${this.dataset.cid}`, { method: 'DELETE' });
                const card = this.closest('.memo-card');
                this.closest('.comment-item').remove();
                const count = card.querySelector('.memo-comment-count');
                count.textContent = Math.max(0, Number(count.textContent) - 1);
                const list = card.querySelector('.comments-list');
                if (!list.querySelector('.comment-item')) list.innerHTML = '<div class="no-comments">还没有评论，聊聊你的想法吧。</div>';
            } catch (err) { toast(err.message, 'error'); }
        };
    });

    // Delete memo
    document.querySelectorAll('.memo-del-btn').forEach(btn => {
        btn.onclick = async function () {
            if (!confirm('删除此帖子？')) return;
            try {
                await apiFetch('/memos/' + this.dataset.mid, { method: 'DELETE' });
                toast('已删除', 'success');
                await loadMemos(true);
            } catch (err) { toast(err.message, 'error'); }
        };
    });

    // AI explain
    document.querySelectorAll('.ai-explain-btn').forEach(btn => {
        btn.onclick = async function () {
            const mid = this.dataset.mid;
            const card = this.closest('.memo-card');
            const expEl = document.getElementById('ai-exp-' + mid);
            if (expEl.style.display !== 'none') {
                expEl.style.display = 'none';
                this.setAttribute('aria-expanded', 'false');
                return;
            }
            if (!Auth.token()) { toast('请先登录可使用 AI 功能', 'error'); return; }

            const content = card.querySelector('.memo-content').textContent;
            const imgUrls = [...card.querySelectorAll('.memo-images .memo-img')].map(i => i.dataset.url);
            expEl.style.display = 'block';
            this.setAttribute('aria-expanded', 'true');
            expEl.innerHTML = '<div class="skeleton" style="height:60px"></div>';
            try {
                const r = await apiFetch('/ai/explain', {
                    method: 'POST', body: JSON.stringify({ content, image_urls: imgUrls }),
                });
                expEl.innerHTML = `<div class="ai-result"><b>AI 解读</b><br>${escHtml(r.explanation)}</div>`;
            } catch (err) { expEl.innerHTML = `<div class="ai-result error">AI 解析失败: ${escHtml(err.message)}</div>`; }
        };
    });

    // Image lightbox
    document.querySelectorAll('.memo-image-button').forEach(button => {
        button.onclick = function () {
            const image = this.querySelector('.memo-img');
            const dialog = document.createElement('dialog');
            dialog.className = 'memo-lightbox';
            dialog.setAttribute('aria-label', '查看图片');
            dialog.innerHTML = `<img src="${escHtml(image.src)}" alt="${escHtml(image.alt)}"><button type="button" class="memo-lightbox-close" aria-label="关闭图片">${memoIcon('close')}</button>`;
            dialog.querySelector('button').onclick = () => dialog.close();
            dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
            dialog.addEventListener('close', () => { dialog.remove(); button.focus(); });
            document.body.appendChild(dialog);
            dialog.showModal();
        };
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    await Auth.init();
    currentKeyword = document.getElementById('memo-search').value.trim();
    document.getElementById('memo-search-clear').disabled = !document.getElementById('memo-search').value;
    loadMemos(true);
    document.getElementById('load-more-btn')?.addEventListener('click', () => loadMemos());
    const searchInput = document.getElementById('memo-search');
    searchInput.addEventListener('input', e => { if (!e.isComposing && !memoSearchComposing) searchMemos(); });
    searchInput.addEventListener('compositionstart', () => { memoSearchComposing = true; clearTimeout(memoSearchTimer); });
    searchInput.addEventListener('compositionend', () => { memoSearchComposing = false; searchMemos(); });
    document.getElementById('memo-search-form').addEventListener('submit', e => { e.preventDefault(); if (!memoSearchComposing) searchMemos(true); });
    document.getElementById('memo-search-clear').addEventListener('click', () => { searchInput.value = ''; searchMemos(true); searchInput.focus(); });

    // The composer is rendered after the session resolves, so use delegation.
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

/* Personal space and composer share the resolved session. */
document.addEventListener('DOMContentLoaded', async () => {
    const area = document.getElementById('compose-area');
    await Auth.init();
    const user = Auth.user();
    if (user) {
        const avatar = escHtml(user.avatar_url || 'assets/images/default-avatar.svg');
        document.getElementById('memo-profile').innerHTML = `
            <a href="profile.html?uid=${user.id}" aria-label="查看我的资料"><img src="${avatar}" class="profile-avatar" width="64" height="64" alt="${escHtml(user.username)}的头像"></a>
            <h2>${escHtml(user.username)}</h2><div class="profile-handle">ID: ${user.id} · 我的知识空间</div>
            <p>让每一次思考，都留下足迹。</p>
            <a href="profile.html?uid=${user.id}" class="profile-link">查看个人主页 ${memoIcon('arrow')}</a>`;
        area.innerHTML = `<div class="compose-box">
            <form id="memo-form">
                <div class="compose-top">
                    <a href="profile.html?uid=${user.id}" aria-label="查看我的资料"><img src="${avatar}" class="compose-avatar" alt="${escHtml(user.username)}的头像" width="40" height="40"></a>
                    <div class="compose-writing">
                        <div class="compose-caption"><strong>记录此刻，${escHtml(user.username)}</strong><span>新笔记</span></div>
                        <label class="visually-hidden" for="memo-content">笔记内容</label>
                        <textarea id="memo-content" name="content" placeholder="有什么新鲜事，想和大家分享？" rows="2" required></textarea>
                    </div>
                </div>
                <div id="image-preview"></div>
                <div class="compose-bottom">
                    <label class="img-upload-btn" for="memo-images" tabindex="0" role="button">${memoIcon('image')}图片</label>
                    <input type="file" id="memo-images" accept="image/*" multiple>
                    <details class="compose-options">
                        <summary>${memoIcon('more')}发布选项</summary>
                        <div class="compose-settings">
                            <label class="location-toggle" title="匿名后仅你和管理员可见你的 ID"><input type="checkbox" id="memo-anonymous">匿名发布</label>
                            <label class="location-toggle" title="仅在开启后才会获取位置"><input type="checkbox" id="memo-location-enabled">发布时附带位置</label>
                            <input type="hidden" id="memo-location" name="location" value="">
                            <button type="button" class="locate-btn" id="memo-locate-btn" disabled>重新定位</button>
                            <span id="memo-location-text">定位未开启</span>
                            <p class="compose-setting-note">最多添加 9 张图片。私密笔记仅作者和管理员可见。</p>
                        </div>
                    </details>
                    <label class="visually-hidden" for="memo-visibility">发布可见范围</label>
                    <select id="memo-visibility" name="visibility"><option value="public">公开可见</option><option value="private">私密（作者和管理员可见）</option></select>
                    <button type="submit" class="btn btn-primary post-btn">发布</button>
                </div>
            </form>
        </div>`;
        document.getElementById('memo-visibility').value = currentVisibility;
        setLocationEnabled(false);
    } else {
        area.innerHTML = `<div class="compose-box login-prompt">
            <span class="login-compose-icon">${memoIcon('pen')}</span>
            <div class="login-prompt-content"><h2>让你的想法，也成为这里的风景</h2><p>登录后即可发布笔记、参与评论。<a href="register.html" class="guest-register">注册账号</a></p></div>
            <a href="login.html" class="btn btn-primary">去登录 ${memoIcon('arrow')}</a>
        </div>`;
    }
});

// Navigation, empty-state actions and options work for newly rendered content.
document.addEventListener('click', event => {
    const visibilityButton = event.target.closest('[data-visibility]');
    if (visibilityButton && visibilityButton.dataset.visibility !== currentVisibility) {
        currentVisibility = visibilityButton.dataset.visibility;
        const composeVisibility = document.getElementById('memo-visibility');
        if (composeVisibility) composeVisibility.value = currentVisibility;
        loadMemos(true);
    }
    if (event.target.closest('[data-compose-focus]')) {
        const textarea = document.getElementById('memo-content');
        if (!textarea) { window.location.href = 'login.html'; return; }
        textarea.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
        textarea.focus({ preventScroll: true });
    }
    if (event.target.closest('[data-retry-memos]')) loadMemos(true);
    if (event.target.closest('[data-clear-search]')) document.getElementById('memo-search-clear').click();
    document.querySelectorAll('.memo-menu[open], .compose-options[open]').forEach(menu => {
        if (!menu.contains(event.target)) menu.open = false;
    });
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        document.querySelectorAll('.memo-menu[open], .compose-options[open]').forEach(menu => {
            menu.open = false;
            menu.querySelector('summary').focus();
        });
    }
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.img-upload-btn')) {
        event.preventDefault();
        document.getElementById('memo-images').click();
    }
});
document.addEventListener('change', event => {
    if (event.target.matches('.comment-form input[name="image"]')) {
        const status = event.target.closest('form').querySelector('.comment-image-status');
        status.textContent = event.target.files[0] ? `已选择图片：${event.target.files[0].name}` : '';
    }
});

// Inline editing keeps the original post intact until a save succeeds.
function editorImageCount(editor) {
    return editor.attachments.length - editor.removedIds.size + editor.newImages.length;
}

function renderEditorImages(editor) {
    return editor.attachments.map(image => {
        const removed = editor.removedIds.has(image.id);
        return `<div class="memo-edit-image${removed ? ' is-removed' : ''}">
            <img src="${escHtml(image.url)}" alt="${escHtml(image.original_name)}">
            <span class="memo-edit-image-tag">${removed ? '已移除' : '原图'}</span>
            <button type="button" class="memo-edit-image-action" ${removed ? 'data-restore-existing' : 'data-remove-existing'}="${image.id}" aria-label="${removed ? '恢复' : '移除'}图片 ${escHtml(image.original_name)}" title="${removed ? '恢复图片' : '移除图片'}">${removed ? '恢复' : '✕'}</button>
        </div>`;
    }).join('') + editor.newImages.map(image => `<div class="memo-edit-image is-new">
        <img src="${image.url}" alt="${escHtml(image.file.name)}">
        <span class="memo-edit-image-tag">新增</span>
        <button type="button" class="memo-edit-image-action" data-remove-new="${image.key}" aria-label="移除新图片 ${escHtml(image.file.name)}" title="移除图片">✕</button>
    </div>`).join('');
}

function renderMemoEditor(mid) {
    const editor = memoEditors.get(mid);
    return `<form class="memo-edit-form" data-mid="${mid}" aria-label="编辑笔记" aria-busy="${editor.saving}" ${editor.saving ? 'inert' : ''}>
        <div class="memo-edit-heading"><span>${memoIcon('pen')}编辑笔记</span><small>保存后才会更新原帖</small></div>
        <label class="visually-hidden" for="memo-edit-content-${mid}">修改笔记内容</label>
        <textarea id="memo-edit-content-${mid}" class="memo-edit-content textarea" name="content" rows="6" required>${escHtml(editor.content)}</textarea>
        <div class="memo-edit-images">${renderEditorImages(editor)}</div>
        <div class="memo-edit-image-toolbar">
            <button type="button" class="memo-edit-add-images">${memoIcon('image')}添加图片</button>
            <input type="file" class="memo-edit-file" accept="image/jpeg,image/png,image/gif,image/webp" multiple hidden>
            <span class="memo-edit-image-count" role="status">${editorImageCount(editor)} / 9 张图片</span>
        </div>
        <p class="memo-edit-help">原图可以移除或恢复；新增图片会在保存后上传。</p>
        <p class="memo-edit-error" role="alert"></p>
        <div class="memo-edit-bottom"><button type="button" class="btn btn-sm memo-edit-cancel">取消</button><button type="submit" class="btn btn-primary btn-sm memo-edit-save">${editor.saving ? '保存中…' : '保存修改'}</button></div>
    </form>`;
}

function beginMemoEdit(card) {
    const mid = card.dataset.id;
    if (memoEditors.has(mid)) return;
    const memo = memoCache.get(mid);
    if (!memo) return;
    memoEditors.set(mid, {
        content: card.querySelector('.memo-content').textContent,
        attachments: memo.attachments || [],
        removedIds: new Set(),
        newImages: [],
        saving: false,
    });
    card.querySelector('.memo-menu').open = false;
    card.classList.add('is-editing');
    card.querySelector('.memo-header').insertAdjacentHTML('afterend', renderMemoEditor(mid));
    card.querySelector('.memo-edit-content').focus({ preventScroll: true });
}

function releaseMemoEditor(mid) {
    const editor = memoEditors.get(mid);
    if (editor) editor.newImages.forEach(image => URL.revokeObjectURL(image.url));
    memoEditors.delete(mid);
}

function updateEditorImages(form) {
    const editor = memoEditors.get(form.dataset.mid);
    form.querySelector('.memo-edit-images').innerHTML = renderEditorImages(editor);
    form.querySelector('.memo-edit-image-count').textContent = `${editorImageCount(editor)} / 9 张图片`;
    form.querySelector('.memo-edit-error').textContent = '';
}

async function saveMemoEdit(form) {
    const mid = form.dataset.mid;
    const editor = memoEditors.get(mid);
    if (!editor || editor.saving) return;
    editor.content = form.querySelector('.memo-edit-content').value;
    if (!editor.content.trim()) {
        form.querySelector('.memo-edit-error').textContent = '笔记内容不能为空。';
        return;
    }
    const data = new FormData();
    data.append('content', editor.content.trim());
    data.append('remove_attachment_ids', JSON.stringify([...editor.removedIds]));
    editor.newImages.forEach(image => data.append('images', image.file));
    editor.saving = true;
    form.setAttribute('aria-busy', 'true');
    form.inert = true;
    form.querySelector('.memo-edit-error').textContent = '';
    form.querySelectorAll('button, input, textarea').forEach(control => { control.disabled = true; });
    form.querySelector('.memo-edit-save').textContent = '保存中…';
    try {
        const memo = await apiFetch(`/memos/${mid}`, { method: 'PATCH', body: data });
        releaseMemoEditor(mid);
        const card = document.querySelector(`.memo-card[data-id="${mid}"]`);
        if (card) {
            card.outerHTML = renderMemoCard(memo);
            bindMemoEvents();
            document.querySelector(`.memo-card[data-id="${mid}"] .memo-menu summary`)?.focus({ preventScroll: true });
        }
        toast('笔记已更新', 'success');
    } catch (error) {
        editor.saving = false;
        // Keep both the draft and its selected files available for another try.
        const activeForm = document.querySelector(`.memo-edit-form[data-mid="${mid}"]`);
        if (activeForm) {
            activeForm.inert = false;
            activeForm.setAttribute('aria-busy', 'false');
            activeForm.querySelectorAll('button, input, textarea').forEach(control => { control.disabled = false; });
            activeForm.querySelector('.memo-edit-save').textContent = '保存修改';
            activeForm.querySelector('.memo-edit-error').textContent = `保存失败：${error.message}`;
        }
    }
}

document.addEventListener('input', event => {
    if (!event.target.matches('.memo-edit-content')) return;
    const editor = memoEditors.get(event.target.closest('form').dataset.mid);
    if (editor) editor.content = event.target.value;
});

document.addEventListener('change', event => {
    if (!event.target.matches('.memo-edit-file')) return;
    const form = event.target.closest('form');
    const editor = memoEditors.get(form.dataset.mid);
    const files = Array.from(event.target.files);
    event.target.value = '';
    if (editor.saving) return;
    if (editorImageCount(editor) + files.length > 9) {
        form.querySelector('.memo-edit-error').textContent = '每条笔记最多 9 张图片，请先移除部分图片。';
        return;
    }
    if (files.some(file => !['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(file.type))) {
        form.querySelector('.memo-edit-error').textContent = '图片支持 JPEG、PNG、GIF 和 WebP 格式。';
        return;
    }
    files.forEach(file => editor.newImages.push({ key: ++nextEditImageId, file, url: URL.createObjectURL(file) }));
    updateEditorImages(form);
});

document.addEventListener('submit', event => {
    if (!event.target.matches('.memo-edit-form')) return;
    event.preventDefault();
    saveMemoEdit(event.target);
});

document.addEventListener('click', event => {
    const form = event.target.closest('.memo-edit-form');
    if (!form) return;
    const editor = memoEditors.get(form.dataset.mid);
    if (!editor || editor.saving) return;
    if (event.target.closest('.memo-edit-add-images')) form.querySelector('.memo-edit-file').click();
    if (event.target.closest('.memo-edit-cancel')) {
        const card = form.closest('.memo-card');
        releaseMemoEditor(form.dataset.mid);
        form.remove();
        card.classList.remove('is-editing');
        card.querySelector('.memo-menu summary').focus({ preventScroll: true });
        return;
    }
    const remove = event.target.closest('[data-remove-existing]');
    const restore = event.target.closest('[data-restore-existing]');
    const removeNew = event.target.closest('[data-remove-new]');
    if (remove) editor.removedIds.add(Number(remove.dataset.removeExisting));
    if (restore) {
        if (editorImageCount(editor) >= 9) {
            form.querySelector('.memo-edit-error').textContent = '最多保留 9 张图片，请先移除其他图片再恢复。';
            return;
        }
        editor.removedIds.delete(Number(restore.dataset.restoreExisting));
    }
    if (removeNew) {
        const index = editor.newImages.findIndex(image => image.key === Number(removeNew.dataset.removeNew));
        if (index >= 0) {
            URL.revokeObjectURL(editor.newImages[index].url);
            editor.newImages.splice(index, 1);
        }
    }
    if (remove || restore || removeNew) updateEditorImages(form);
});

document.addEventListener('click', async event => {
    const button = event.target.closest('.memo-like-btn');
    if (!button || button.disabled) return;
    if (!Auth.token()) { toast('登录后即可点赞', 'error'); return; }
    const mid = button.dataset.mid;
    const wasLiked = button.getAttribute('aria-pressed') === 'true';
    button.disabled = true;
    try {
        const result = await apiFetch(`/memos/${mid}/like`, { method: wasLiked ? 'DELETE' : 'PUT' });
        const cached = memoCache.get(mid);
        if (cached) Object.assign(cached, result);
        const card = document.querySelector(`.memo-card[data-id="${mid}"]`);
        if (!card) return;
        const currentButton = card.querySelector('.memo-like-btn');
        currentButton.classList.toggle('is-liked', result.liked_by_me);
        currentButton.setAttribute('aria-pressed', String(result.liked_by_me));
        currentButton.setAttribute('aria-label', result.liked_by_me ? '取消点赞' : '点赞');
        currentButton.innerHTML = `${memoIcon('heart')}<span>${result.liked_by_me ? '已赞' : '赞'}</span><span class="memo-like-count">${result.like_count}</span>`;
        const likes = card.querySelector('.memo-likes');
        likes.innerHTML = renderMemoLikes(result.likes);
        likes.hidden = result.like_count === 0;
    } catch (error) {
        toast('点赞操作失败：' + error.message, 'error');
    } finally {
        button.disabled = false;
    }
});

// Delegated actions also work for cards added after publishing.
document.addEventListener('click', async (event) => {
    const button = event.target.closest('.memo-edit-btn, .memo-visibility-btn');
    if (!button) return;
    const updates = {};
    if (button.classList.contains('memo-edit-btn')) {
        beginMemoEdit(button.closest('.memo-card'));
        return;
    } else {
        updates.visibility = button.dataset.next;
        if (!confirm(updates.visibility === 'public' ? '公开后所有人都可以查看，继续？' : '设为私密后仅作者和管理员可见，继续？')) return;
    }
    try {
        await apiFetch(`/memos/${button.dataset.mid}`, {method: 'PATCH', body: JSON.stringify(updates)});
        await loadMemos(true);
    } catch (error) { toast(error.message, 'error'); }
});
