/* ═══════════════════════════════════════════════════════════════
   profile.js — Public user profile.
   Uses escHtml / formatDateTime from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */
(function () {
  function qs(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  async function loadProfile() {
    const card = document.getElementById('profile-card');
    if (!card) return;

    await Auth.init();
    if (!Auth.token()) {
      card.innerHTML = '<div style="color:var(--text-2)">请先登录后查看用户资料。</div>';
      return;
    }

    const uid = qs('uid') || Auth.user().id;

    card.innerHTML = '<div style="color:var(--text-2)">加载中...</div>';
    try {
      const p = await apiFetch(`/users/${encodeURIComponent(uid)}/profile`);
      const isSelf = p.id === Auth.user().id;
      card.innerHTML = `
        <img class="profile-avatar" src="${escHtml(p.avatar_url || 'assets/images/default-avatar.svg')}" alt="头像">
        <div style="flex:1">
          <div class="profile-name">${escHtml(p.username)}</div>
          <div class="profile-sub">ID: ${p.id} · 注册于 ${formatDateTime(p.created_at)}</div>
          ${isSelf ? `
          <form id="username-form" class="username-form">
            <label for="profile-username">用户名</label>
            <div class="username-controls">
              <input id="profile-username" class="input" name="username" type="text" value="${escHtml(p.username)}" required maxlength="50" autocomplete="username">
              <button class="btn btn-primary btn-sm" type="submit">保存用户名</button>
            </div>
            <small>修改后请使用新用户名登录。</small>
          </form>` : ''}
          <div class="stats">
            <div class="stat-item"><div class="stat-label">发帖数</div><div class="stat-value">${p.memo_count}</div></div>
            <div class="stat-item"><div class="stat-label">评论数</div><div class="stat-value">${p.comment_count}</div></div>
          </div>
          <div class="actions">
            ${isSelf ? '' : `<a class="btn btn-primary btn-sm" href="messages.html?uid=${p.id}">私聊</a>`}
            <a class="btn btn-sm" href="memos.html">返回笔记</a>
          </div>
        </div>
      `;
      document.getElementById('username-form')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = document.getElementById('profile-username');
        const username = input.value.trim();
        if (!username) {
          toast('用户名不能为空', 'error');
          input.focus();
          return;
        }
        const button = event.target.querySelector('button[type=submit]');
        button.disabled = true;
        try {
          const user = await apiFetch('/auth/me', {
            method: 'PATCH', body: JSON.stringify({ username }),
          });
          Auth.set(null, user);
          renderNavUser();
          card.querySelector('.profile-name').textContent = user.username;
          input.value = user.username;
          toast('用户名已更新，下次请使用新用户名登录', 'success');
        } catch (error) {
          toast(error.message, 'error');
        } finally {
          button.disabled = false;
        }
      });
    } catch (e) {
      card.innerHTML = `<div style="color:var(--danger)">加载失败: ${escHtml(e.message)}</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', loadProfile);
})();
