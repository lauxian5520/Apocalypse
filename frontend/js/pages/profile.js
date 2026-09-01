/* ═══════════════════════════════════════════════════════════════
   profile.js — Public user profile.
   Uses escHtml / formatDateTime from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */
(function () {
  function qs(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  async function loadProfile() {
    const uid = qs('uid');
    const card = document.getElementById('profile-card');
    if (!card) return;

    await Auth.init();
    if (!Auth.token()) {
      card.innerHTML = '<div style="color:var(--text-2)">请先登录后查看用户资料。</div>';
      return;
    }

    if (!uid) {
      card.innerHTML = '<div style="color:var(--danger)">缺少用户 ID 参数。</div>';
      return;
    }

    card.innerHTML = '<div style="color:var(--text-2)">加载中...</div>';
    try {
      const p = await apiFetch(`/users/${encodeURIComponent(uid)}/profile`);
      card.innerHTML = `
        <img class="profile-avatar" src="${escHtml(p.avatar_url || 'assets/images/default-avatar.svg')}" alt="头像">
        <div style="flex:1">
          <div class="profile-name">${escHtml(p.username)}</div>
          <div class="profile-sub">ID: ${p.id} · 注册于 ${formatDateTime(p.created_at)}</div>
          <div class="stats">
            <div class="stat-item"><div class="stat-label">发帖数</div><div class="stat-value">${p.memo_count}</div></div>
            <div class="stat-item"><div class="stat-label">评论数</div><div class="stat-value">${p.comment_count}</div></div>
          </div>
          <div class="actions">
            <a class="btn btn-primary btn-sm" href="messages.html?uid=${p.id}">私聊</a>
            <a class="btn btn-sm" href="memos.html">返回笔记</a>
          </div>
        </div>
      `;
    } catch (e) {
      card.innerHTML = `<div style="color:var(--danger)">加载失败: ${escHtml(e.message)}</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', loadProfile);
})();
