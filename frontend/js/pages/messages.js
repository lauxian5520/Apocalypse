/* ═══════════════════════════════════════════════════════════════
   messages.js — Direct-message page.
   Uses escHtml from js/core/utils.js.
═══════════════════════════════════════════════════════════════ */
(function () {
  let currentPeerId = 0;
  let selectedAttachment = null;

  function getQueryPeerId() {
    const q = new URLSearchParams(window.location.search);
    return Number(q.get('uid') || '0');
  }

  function guessAttachmentKind(message) {
    const mime = String(message.attachment_type || '').toLowerCase();
    if (mime.startsWith('image/') || mime === 'image') return 'image';
    if (mime.startsWith('video/') || mime === 'video') return 'video';

    const name = String(message.attachment_name || message.attachment_url || message.image_url || '').toLowerCase();
    if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(name)) return 'image';
    if (/\.(mp4|webm|ogg|mov|m4v|avi|mkv)$/.test(name)) return 'video';
    return 'file';
  }

  function renderAttachment(message) {
    const url = escHtml(message.attachment_url || message.image_url || '');
    if (!url) return '';

    const type = guessAttachmentKind(message);
    const safeName = escHtml(message.attachment_name || '下载附件');

    if (type === 'image') {
      return `<img src="${url}" class="msg-img" alt="聊天图片"><div class="msg-tools"><a class="btn btn-sm" href="${url}" download>保存图片</a></div>`;
    }
    if (type === 'video') {
      return `<video class="msg-video" controls src="${url}" preload="metadata"></video><div class="msg-tools"><a class="btn btn-sm" href="${url}" download>下载视频</a></div>`;
    }

    return `<div class="msg-tools"><span class="msg-file-chip">文件: ${safeName}</span><a class="btn btn-sm" href="${url}" download>下载文件</a></div>`;
  }

  function updateFileHint(file) {
    const hint = document.getElementById('dm-file-hint');
    if (!hint) return;
    hint.textContent = file ? `已选择: ${file.name}` : '未选择附件';
  }

  async function loadConversations(preferPeerId) {
    const list = document.getElementById('conv-list');
    const items = await apiFetch('/messages/conversations');
    const convs = items.items || [];
    if (!convs.length) {
      list.innerHTML = '<div style="padding:.8rem;color:var(--text-2)">暂无会话，去用户资料页发起私聊吧。</div>';
      if (preferPeerId) {
        // First-time chat: still open target peer even when conversation list is empty.
        await openConversation(preferPeerId, false);
      }
      return;
    }

    list.innerHTML = convs.map(c => `
      <div class="conv-item ${currentPeerId === c.user.id ? 'active' : ''}" data-uid="${c.user.id}">
        <img class="conv-avatar" src="${escHtml(c.user.avatar_url || 'assets/images/default-avatar.svg')}" alt="头像">
        <div style="min-width:0;flex:1">
          <div class="conv-name">${escHtml(c.user.username)}（ID:${c.user.id}）${c.unread ? ` <span style="color:var(--accent)">(${c.unread})</span>` : ''}</div>
          <div class="conv-last">${escHtml(c.last_message?.content || '')}</div>
        </div>
      </div>
    `).join('');

    const targetId = preferPeerId || currentPeerId || convs[0].user.id;
    if (targetId) {
      await openConversation(targetId, false);
    }

    list.querySelectorAll('.conv-item').forEach((el) => {
      el.addEventListener('click', () => {
        const uid = Number(el.dataset.uid || '0');
        openConversation(uid, true);
      });
    });
  }

  async function openConversation(uid, refreshList) {
    if (!uid) return;
    currentPeerId = uid;
    const messages = await apiFetch(`/messages/with/${uid}`);
    const msgBox = document.getElementById('dm-messages');
    const me = Auth.user();
    msgBox.innerHTML = messages.map(m => `
      <div class="bubble ${m.sender_id === me.id ? 'me' : 'peer'}">
        ${m.content ? `<div>${escHtml(m.content)}</div>` : ''}
        ${renderAttachment(m)}
      </div>
    `).join('') || '<div style="color:var(--text-2)">暂无消息，发一条试试。</div>';
    msgBox.scrollTop = msgBox.scrollHeight;

    try {
      const p = await apiFetch(`/users/${uid}/profile`);
      document.getElementById('dm-peer-name').textContent = `与 ${p.username} 的私聊`;
    } catch {
      document.getElementById('dm-peer-name').textContent = '私聊';
    }

    await apiFetch(`/messages/read/${uid}`, { method: 'PATCH' });
    if (refreshList) await loadConversations(uid);
  }

  async function sendMessage(e) {
    e.preventDefault();
    if (!currentPeerId) {
      toast('请先选择会话', 'error');
      return;
    }
    const input = document.getElementById('dm-input');
    const fileInput = document.getElementById('dm-file');
    const content = input.value.trim();
    const file = selectedAttachment || (fileInput?.files && fileInput.files[0]) || null;
    const hasAttachment = Boolean(file);
    if (!content && !hasAttachment) return;

    const fd = new FormData();
    fd.append('recipient_id', String(currentPeerId));
    fd.append('content', content);
    if (hasAttachment) fd.append('file', file);

    // Without this, a rejected message (too large, disabled peer, ...) failed
    // silently: the input was never cleared and no error was ever shown.
    try {
      await apiFetch('/messages', {
        method: 'POST',
        body: fd,
      });
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
    input.value = '';
    if (fileInput) fileInput.value = '';
    selectedAttachment = null;
    updateFileHint(null);
    await openConversation(currentPeerId, true);
  }

  async function init() {
    if (!(await Auth.requireLogin())) return;

    document.getElementById('dm-form').addEventListener('submit', sendMessage);
    const fileInput = document.getElementById('dm-file');
    if (fileInput) {
      fileInput.addEventListener('change', () => {
        selectedAttachment = (fileInput.files && fileInput.files[0]) || null;
        updateFileHint(selectedAttachment);
      });
    }
    const qPeerId = getQueryPeerId();
    await loadConversations(qPeerId);

    setInterval(() => {
      if (currentPeerId) openConversation(currentPeerId, false);
    }, 6000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
