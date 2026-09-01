/* admin.js — page script for admin.html. */
document.addEventListener('DOMContentLoaded', async () => {
    await Auth.init();
    if (!Auth.token() || !Auth.isAdmin()) {
        document.getElementById('no-perm').style.display = 'block';
        return;
    }
    document.getElementById('admin-main').style.display = 'block';
    loadTracks();
    loadUsers();

    document.getElementById('user-search-btn')?.addEventListener('click', () => loadUsers());
    document.getElementById('user-refresh-btn')?.addEventListener('click', () => {
        const input = document.getElementById('user-keyword');
        if (input) input.value = '';
        loadUsers();
    });

    const usersTbody = document.getElementById('users-tbody');
    usersTbody?.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;

        const uid = Number(btn.dataset.uid || '0');
        if (!uid) return;

        if (btn.classList.contains('js-toggle-role')) {
            toggleRole(uid, btn.dataset.role || 'user');
            return;
        }
        if (btn.classList.contains('js-toggle-status')) {
            const currentDisabled = btn.dataset.disabled === '1';
            toggleStatus(uid, !currentDisabled);
            return;
        }
        if (btn.classList.contains('js-reset-password')) {
            resetUserPassword(uid);
            return;
        }
        if (btn.classList.contains('js-remove-user')) {
            removeUser(uid);
        }
    });
});

async function loadTracks() {
    const list = document.getElementById('track-list');
    try {
        const tracks = await apiFetch('/music');
        if (!tracks.length) {
            list.innerHTML = '<div style="color:var(--text-2)">暂无曲目</div>';
            return;
        }

        list.innerHTML = tracks.map((t) => `
  <div class="track-item" data-id="${t.id}">
    <div class="track-item-info">
      <div class="track-title">${t.title}</div>
      <div class="track-artist">${t.artist || '未知艺术家'} · 排序: ${t.sort_order}</div>
    </div>
    <audio controls src="${t.url}"></audio>
    <div class="track-item-actions">
      <button class="btn btn-sm btn-danger" onclick="deleteTrack(${t.id})">删除</button>
      <button class="btn btn-sm" onclick="toggleTrack(${t.id}, ${!t.is_active})">${t.is_active ? '停用' : '启用'}</button>
    </div>
  </div>
`).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

document.getElementById('music-upload-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const fd = new FormData(form);
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true;
    btn.textContent = '上传中...';

    try {
        await apiFetch('/music', {
            method: 'POST',
            body: fd,
        });
        toast('上传成功', 'success');
        form.reset();
        loadTracks();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '上传曲目';
    }
});

async function deleteTrack(id) {
    if (!confirm('确认删除此曲目？')) return;
    await apiFetch('/music/' + id, { method: 'DELETE' });
    toast('已删除', 'success');
    loadTracks();
}

async function toggleTrack(id, active) {
    await apiFetch('/music/' + id, { method: 'PATCH', body: JSON.stringify({ is_active: active }) });
    loadTracks();
}

async function triggerRefresh() {
    const el = document.getElementById('refresh-result');
    el.textContent = '刷新中...';
    try {
        const r = await apiFetch('/feeds/refresh', { method: 'POST' });
        el.textContent = '成功: ' + r.message;
    } catch (e) {
        el.textContent = '失败: ' + e.message;
    }
}

async function loadUsers() {
    const tbody = document.getElementById('users-tbody');
    const kw = (document.getElementById('user-keyword')?.value || '').trim();
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-2)">加载中...</td></tr>';
    try {
        const ret = await apiFetch(`/admin/users?page=1&page_size=100&keyword=${encodeURIComponent(kw)}`);
        const users = ret.items || [];
        if (!users.length) {
            tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-2)">暂无用户</td></tr>';
            return;
        }
        tbody.innerHTML = users.map(u => `
          <tr>
            <td><img src="${u.avatar_url || 'assets/images/default-avatar.svg'}" alt="头像"></td>
            <td>${u.id}</td>
            <td>${u.username}</td>
            <td>${u.email}</td>
            <td><span class="role-badge ${u.role === 'admin' ? 'role-admin' : 'role-user'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span></td>
            <td>${u.is_disabled ? '<span style="color:#ff8d8d">已禁用</span>' : '<span style="color:#8df7c6">正常</span>'}</td>
            <td>
                                    <button class="btn btn-sm js-toggle-role" data-uid="${u.id}" data-role="${u.role}">切换身份</button>
                                    <button class="btn btn-sm js-toggle-status" data-uid="${u.id}" data-disabled="${u.is_disabled ? '1' : '0'}">${u.is_disabled ? '启用' : '禁用'}</button>
                                    <button class="btn btn-sm js-reset-password" data-uid="${u.id}">重置密码</button>
                                    <button class="btn btn-sm btn-danger js-remove-user" data-uid="${u.id}">删除</button>
            </td>
          </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:#ff8d8d">加载失败: ${e.message}</td></tr>`;
    }
}

async function toggleRole(userId, curRole) {
    const next = curRole === 'admin' ? 'user' : 'admin';
    if (!confirm(`确定将该用户角色改为 ${next === 'admin' ? '管理员' : '普通用户'} 吗？`)) return;
    try {
        await apiFetch(`/admin/users/${userId}/role`, { method: 'PATCH', body: JSON.stringify({ role: next }) });
        toast('角色已更新', 'success');
        loadUsers();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function toggleStatus(userId, isDisabled) {
    const text = isDisabled ? '禁用' : '启用';
    if (!confirm(`确定${text}该用户吗？`)) return;
    try {
        await apiFetch(`/admin/users/${userId}/status`, { method: 'PATCH', body: JSON.stringify({ is_disabled: isDisabled }) });
        toast('状态已更新', 'success');
        loadUsers();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function resetUserPassword(userId) {
    const pwd = prompt('请输入重置后的新密码（至少 8 位）');
    if (!pwd) return;
    const pwd2 = prompt('请再次输入新密码以确认');
    if (pwd2 === null) return;
    if (pwd !== pwd2) {
        toast('两次输入不一致，请重试', 'error');
        return;
    }
    try {
        await apiFetch(`/admin/users/${userId}/reset-password`, {
            method: 'PATCH',
            body: JSON.stringify({ new_password: pwd, password: pwd }),
        });
        toast('密码已重置', 'success');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function removeUser(userId) {
    if (!confirm('删除用户将级联删除其帖子、评论和私聊消息，是否继续？')) return;
    try {
        await apiFetch(`/admin/users/${userId}`, { method: 'DELETE' });
        toast('用户已删除', 'success');
        loadUsers();
    } catch (e) {
        toast(e.message, 'error');
    }
}
