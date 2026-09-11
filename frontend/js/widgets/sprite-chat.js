/* sprite-chat.js — floating 3D assistant 天启: SSE chat, page summary, world clock.

   The sprite expresses itself through its own face — eye shape, eye colour,
   aura — not through a text bubble. The bubble it used to have was a separate
   element with its own show/hide timers, and those timers raced with the
   hover state: re-entering within the hide delay left the wrong caption on
   screen and reset the face while the pointer was still on it. A single mood
   on the model has no second state to fall out of sync with. */
(function () {
  if (window.__MW_SPRITE_CHAT_LOADED) return;
  window.__MW_SPRITE_CHAT_LOADED = true;

  const NAME = '天启';

  const pageName = (location.pathname.split('/').pop() || '').toLowerCase();
  // Pages that already own the conversation. Only the DM view qualifies: a
  // message there is addressed to a person, and a second box that answers as
  // 天启 reads as that person replying.
  // The harness workbench used to be on this list. It is a different agent in
  // a different session, not the same conversation twice, and opting it out
  // left the sprite sitting there inert — clicking it did nothing at all.
  const CHAT_HOSTING_PAGES = ['messages.html'];
  const disableAiChatOnPage = CHAT_HOSTING_PAGES.includes(pageName);

  // Assigned by the 3D section below. Chat code calls it without caring
  // whether WebGL ever came up.
  let setMood = () => {};

  function ensureContainer() {
    let el = document.getElementById('sprite-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'sprite-container';
      document.body.appendChild(el);
    }
    return el;
  }

  const container = ensureContainer();
  const hasThree = typeof window.THREE !== 'undefined';

  function ensureClockUI() {
    let clock = document.getElementById('sprite-clock');
    if (clock) return clock;
    clock = document.createElement('div');
    clock.id = 'sprite-clock';
    clock.innerHTML = `
      <div class="sprite-clock-date" id="sprite-clock-date">----.--.--</div>
      <div class="sprite-clock-time" id="sprite-clock-time">--:--:--</div>
      <div class="sprite-clock-meta" id="sprite-clock-meta">定位中...</div>
    `;
    document.body.appendChild(clock);
    return clock;
  }

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  function formatByTimezone(timeZone) {
    const now = new Date();
    const dateText = new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(now).replace(/\//g, '-');
    const timeText = new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(now);
    return { dateText, timeText };
  }

  async function resolveTimezoneByCoords(lat, lon) {
    const endpoints = [
      `https://timeapi.io/api/TimeZone/coordinate?latitude=${lat}&longitude=${lon}`,
      `https://api.bigdatacloud.net/data/timezone-by-location?latitude=${lat}&longitude=${lon}`,
    ];

    for (const url of endpoints) {
      try {
        const data = await fetchJsonWithTimeout(url, 4500);
        const tz = data?.timeZone || data?.timezone || data?.ianaTimeId || data?.ianaTimeZone;
        if (typeof tz === 'string' && tz.includes('/')) return tz;
      } catch (_) {
        // try next endpoint
      }
    }
    return null;
  }

  async function fetchJsonWithTimeout(url, timeoutMs = 5000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  function getCurrentPosition(timeoutMs = 7000) {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error('geolocation unsupported'));
        return;
      }
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: false,
        timeout: timeoutMs,
        maximumAge: 5 * 60 * 1000,
      });
    });
  }

  async function resolveClockLocale() {
    const sysTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    const fallback = { timeZone: sysTz, region: '本机时区', source: 'system' };

    try {
      const pos = await getCurrentPosition(7000);
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;

      let region = '定位地区';
      let countryCode = '';
      try {
        const geo = await fetchJsonWithTimeout(
          `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=zh`,
          4500
        );
        const city = geo.city || geo.locality || '';
        const area = geo.principalSubdivision || '';
        const country = geo.countryName || '';
        countryCode = (geo.countryCode || '').toUpperCase();
        region = [city || area, country].filter(Boolean).join(' · ') || region;
      } catch (_) {
        // ignore region reverse lookup failure
      }

      let timeZone = await resolveTimezoneByCoords(lat, lon);
      // China uses unified UTC+08:00; force a correct IANA zone when API lookup is unavailable.
      if (!timeZone && countryCode === 'CN') {
        timeZone = 'Asia/Shanghai';
      }
      if (!timeZone) {
        timeZone = sysTz;
      }

      return { timeZone, region, source: 'geo' };
    } catch (_) {
      try {
        const ip = await fetchJsonWithTimeout('https://ipapi.co/json/', 4500);
        const timeZone = ip.timezone || sysTz;
        const region = [ip.city, ip.country_name].filter(Boolean).join(' · ') || 'IP定位地区';
        return { timeZone, region, source: 'ip' };
      } catch (_) {
        return fallback;
      }
    }
  }

  function initSpriteClock() {
    const root = ensureClockUI();
    const dateEl = document.getElementById('sprite-clock-date');
    const timeEl = document.getElementById('sprite-clock-time');
    const metaEl = document.getElementById('sprite-clock-meta');

    const state = {
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      region: '本机时区',
      source: 'system',
    };

    function render() {
      const v = formatByTimezone(state.timeZone);
      dateEl.textContent = v.dateText;
      timeEl.textContent = v.timeText;

      const zoneText = state.timeZone || 'UTC';
      metaEl.textContent = `${state.region} · ${zoneText}`;

      root.classList.remove('tick');
      void root.offsetWidth;
      root.classList.add('tick');
    }

    render();
    setInterval(render, 1000);

    resolveClockLocale().then((resolved) => {
      state.timeZone = resolved.timeZone;
      state.region = resolved.region;
      state.source = resolved.source;
      render();
    });

    makeClockDraggable(root);
  }

  /** The clock scales as a whole: its stylesheet sizes everything in `em`, so
   *  one font-size on the root moves date, time and meta together. */
  function makeClockDraggable(root) {
    if (!window.FloatingPanel) return;
    const baseWidth = root.getBoundingClientRect().width || 136;
    window.FloatingPanel.make(root, {
      id: 'clock',
      autoHeight: true,
      minWidth: 96,
      maxWidth: 420,
      onResize: (w) => {
        root.style.fontSize = `${(w / baseWidth).toFixed(3)}rem`;
      },
    });
  }

  function ensureChatUI() {
    if (disableAiChatOnPage) return;
    if (document.getElementById('chat-dialog')) return;
    const tpl = `
      <div class="chat-dialog" id="chat-dialog">
        <div class="chat-dialog-header">
          <div class="chat-dialog-title"><span>${NAME}</span></div>
          <div class="chat-dialog-actions">
            <button class="chat-mini-btn" id="chat-summary" title="总结当前页面">总结本页</button>
            <button class="chat-dialog-close" id="chat-close">✕</button>
          </div>
        </div>
        <div class="chat-messages" id="chat-messages">
          <div class="msg msg-ai">${NAME}已就绪。登录后即可下达指令，或让我清算当前页面的内容。</div>
        </div>
        <div class="typing-indicator" id="typing-indicator" style="display:none">
          <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
        </div>
        <div class="chat-dialog-footer">
          <input type="text" id="chat-input" placeholder="输入指令，或输入“总结本页”" autocomplete="off">
          <button id="chat-send">➤</button>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML('beforeend', tpl);
  }

  ensureChatUI();

  if (disableAiChatOnPage) {
    initSpriteClock();
    // Keep sprite and clock on messages page, but disable AI chat panel to avoid context confusion.
  }

  const chatDialog = document.getElementById('chat-dialog');
  const chatMessages = document.getElementById('chat-messages');
  const chatInput = document.getElementById('chat-input');
  const chatSend = document.getElementById('chat-send');
  const chatClose = document.getElementById('chat-close');
  const typing = document.getElementById('typing-indicator');
  const chatSummary = document.getElementById('chat-summary');

  const history = [];
  // Must match MAX_HISTORY_MESSAGES in backend/routers/ai.py.
  const MAX_HISTORY = 20;
  if (!disableAiChatOnPage) {
    initSpriteClock();
  }

  // Auth.init() is kicked off on DOMContentLoaded and resolves asynchronously.
  // Reading Auth.token() directly raced with it and told logged-in users
  // "请先登录" whenever they were quick (or /auth/me was slow).
  async function isLoggedIn() {
    try {
      if (window.Auth?.user?.()) return true;
      return Boolean(await window.Auth?.init?.());
    } catch (_) {
      return false;
    }
  }

  function appendMsg(role, text) {
    const el = document.createElement('div');
    el.className = `msg msg-${role}`;
    el.textContent = text;
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  /** Put the dialog beside the sprite wherever the sprite currently is.
   *  Without this, dragging the sprite to one corner would still open its
   *  chat in the opposite one. */
  function positionChatNearSprite() {
    if (!chatDialog) return;
    const s = container.getBoundingClientRect();
    // `offsetWidth`/`offsetHeight`, not `getBoundingClientRect()`: the dialog
    // owns a `transform: scale()` open/close transition, and a rect measured
    // mid-transition is the *scaled* size. Anchoring off that put a 520px
    // panel where a 468px one fitted and left the rest below the fold.
    const dw = chatDialog.offsetWidth;
    const dh = chatDialog.offsetHeight;
    const gap = 16;

    // Prefer the side with room; fall back to overlapping the edge rather
    // than pushing the dialog off screen.
    let left = s.left - dw - gap;
    if (left < gap) left = s.right + gap;
    left = Math.max(gap, Math.min(left, window.innerWidth - dw - gap));

    let top = s.bottom - dh;
    top = Math.max(gap, Math.min(top, window.innerHeight - dh - gap));

    chatDialog.style.left = `${Math.round(left)}px`;
    chatDialog.style.top = `${Math.round(top)}px`;
    chatDialog.style.right = 'auto';
    chatDialog.style.bottom = 'auto';
  }

  /* The dialog is opened nearly empty and grows as the reply streams in, but
     `top` was pinned once at open time from the height it had *then*. A full
     answer pushed the bottom of the panel — the tail of the reply and the
     whole input row — below the fold, where the auto-scroll-to-bottom then
     parked the newest text. On screen that looked like a frozen box the wheel
     would not move. Re-anchoring on every size change keeps the panel's foot
     beside the sprite and inside the viewport, so it grows upwards. */
  if (chatDialog && typeof ResizeObserver === 'function') {
    new ResizeObserver(() => {
      if (chatDialog.classList.contains('open')) positionChatNearSprite();
    }).observe(chatDialog);
  }

  window.addEventListener('resize', () => {
    if (chatDialog?.classList.contains('open')) positionChatNearSprite();
  });

  function toggleChat() {
    if (!chatDialog) return;
    const opening = !chatDialog.classList.contains('open');
    if (opening) positionChatNearSprite();
    chatDialog.classList.toggle('open');
    if (chatDialog.classList.contains('open')) {
      chatInput.focus();
    }
  }

  function pageSummaryText() {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,.chat-dialog,#sprite-container,#sprite-clock,.noise-overlay,.header,.nav,.nav-links,.nav-user,.nav-toggle').forEach((n) => n.remove());
    const txt = (clone.innerText || '').replace(/\s+/g, ' ').trim();
    return txt.slice(0, 5000);
  }

  async function summarizeCurrentPage() {
    if (!(await isLoggedIn())) {
      appendMsg('ai', '未授权。先登录，再下达指令。');
      return;
    }

    const text = pageSummaryText();
    if (!text) {
      appendMsg('ai', '当前页面没有可总结的文本内容。');
      return;
    }

    typing.style.display = 'flex';
    setMood('lock');
    try {
      const ret = await window.apiFetch('/ai/summarize', {
        method: 'POST',
        body: JSON.stringify({ text, context: document.title || '当前页面' }),
      });
      typing.style.display = 'none';
      appendMsg('ai', `【页面总结】\n${ret?.summary || '暂无总结结果'}`);
    } catch (e) {
      typing.style.display = 'none';
      appendMsg('ai', `页面总结失败: ${e.message}`);
    } finally {
      setMood('idle');
    }
  }

  let sending = false;

  async function sendMsg() {
    if (sending) return;
    const text = (chatInput.value || '').trim();
    if (!text) return;
    chatInput.value = '';
    sending = true;
    if (chatSend) chatSend.disabled = true;
    try {
      await doSendMsg(text);
    } finally {
      sending = false;
      if (chatSend) chatSend.disabled = false;
      setMood('idle');
    }
  }

  async function doSendMsg(text) {

    if (/^总结本页$|^\/summary$/i.test(text)) {
      appendMsg('user', text);
      await summarizeCurrentPage();
      return;
    }

    if (!(await isLoggedIn())) {
      appendMsg('user', text);
      appendMsg('ai', '未授权。先登录，再下达指令。');
      return;
    }

    appendMsg('user', text);
    history.push({ role: 'user', content: text });
    // Keep the conversation bounded so requests don't grow without limit.
    if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);

    typing.style.display = 'flex';
    setMood('lock');

    try {
      const res = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': window.Auth?.csrfToken?.() || '',
        },
        credentials: 'include',
        body: JSON.stringify({ messages: history }),
      });

      if (!res.ok) {
        typing.style.display = 'none';
        history.pop();
        let detail = '';
        try {
          const err = await res.json();
          detail = err?.detail || err?.error || '';
        } catch (_) {
          detail = '';
        }
        appendMsg('ai', `AI 请求失败: ${detail || `HTTP ${res.status} ${res.statusText}`}`);
        return;
      }

      const msgEl = document.createElement('div');
      msgEl.className = 'msg msg-ai';
      chatMessages.appendChild(msgEl);
      chatMessages.scrollTop = chatMessages.scrollHeight;

      const reader = res.body?.getReader();
      if (!reader) {
        typing.style.display = 'none';
        history.pop();
        msgEl.textContent = 'AI 响应流初始化失败';
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';
      let full = '';
      typing.style.display = 'none';
      setMood('speak');

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          try {
            const obj = JSON.parse(data);
            if (obj.error) {
              history.pop();
              msgEl.textContent = `AI 服务错误: ${obj.error}`;
              chatMessages.scrollTop = chatMessages.scrollHeight;
              return;
            }
            if (obj.delta) {
              full += obj.delta;
              msgEl.textContent = full;
              chatMessages.scrollTop = chatMessages.scrollHeight;
            }
          } catch (_) {
            // ignore malformed SSE chunks
          }
        }
      }

      if (full) {
        history.push({ role: 'assistant', content: full });
        if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);
      } else {
        history.pop();
        msgEl.textContent = 'AI 没有返回任何内容，请稍后再试。';
      }
    } catch (e) {
      typing.style.display = 'none';
      history.pop();
      appendMsg('ai', `连接失败: ${e.message}`);
    }
  }

  if (!disableAiChatOnPage) {
    chatSend?.addEventListener('click', sendMsg);
    chatInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendMsg();
      }
    });
    chatClose?.addEventListener('click', () => chatDialog.classList.remove('open'));
    chatSummary?.addEventListener('click', summarizeCurrentPage);

    document.addEventListener('click', (e) => {
      if (!chatDialog.classList.contains('open')) return;
      if (chatDialog.contains(e.target)) return;
      if (container.contains(e.target)) return;
      chatDialog.classList.remove('open');
    });
  }

  // ── the sprite ─────────────────────────────────────────────────
  // Canvas is rendered larger than its box; the overflow is what gives the
  // sprite its halo. Kept as a ratio so resizing preserves the look.
  const RENDER_SCALE = 1.5;

  function boxSize() {
    const r = container.getBoundingClientRect();
    return { w: Math.round(r.width) || 120, h: Math.round(r.height) || 120 };
  }

  /** No WebGL: a glyph that still opens the chat and still drags.
   *
   *  This path has to cover more than a missing library. `new WebGLRenderer()`
   *  *throws* when a context cannot be created — headless browsers, blocked
   *  GPUs, some remote desktops — and an uncaught throw here used to take the
   *  click handler and `toggleSpriteChat` down with it, leaving the assistant
   *  unreachable with no visible error. */
  function useFallbackSprite() {
    container.innerHTML = `<div class="sprite-fallback">✨</div>`;
    container.title = `打开 ${NAME}（拖动可移动，右下角可缩放）`;
    container.addEventListener('click', (e) => {
      if (disableAiChatOnPage) return;
      e.stopPropagation();
      toggleChat();
    });
    window.FloatingPanel?.make(container, {
      id: 'sprite',
      aspect: 1,
      minWidth: 64,
      maxWidth: 320,
      onMove: () => {
        if (chatDialog?.classList.contains('open')) positionChatNearSprite();
      },
    });
    window.toggleSpriteChat = toggleChat;
  }

  if (!hasThree) {
    useFallbackSprite();
    return;
  }

  const box = boxSize();
  let W = box.w * RENDER_SCALE;
  let H = box.h * RENDER_SCALE;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (e) {
    useFallbackSprite();
    return;
  }
  renderer.setSize(W, H);
  renderer.setPixelRatio(window.devicePixelRatio);
  container.innerHTML = '';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(75, W / H, 0.1, 1000);
  camera.position.z = 20;

  const sphereGeo = new THREE.SphereGeometry(7, 128, 128);
  const mat = new THREE.PointsMaterial({
    size: 0.12,
    color: 0xffffff,
    transparent: true,
    opacity: 0.85,
    blending: THREE.AdditiveBlending,
    vertexColors: true
  });

  const count = sphereGeo.attributes.position.count;
  const colors = [];
  for (let i = 0; i < count; i++) {
    colors.push(1, 1, 1);
  }
  sphereGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

  const particleSphere = new THREE.Points(sphereGeo, mat);
  scene.add(particleSphere);

  const eyeGroup = new THREE.Group();
  scene.add(eyeGroup);

  const eyeGeo = new THREE.SphereGeometry(0.5, 32, 32);
  const eyeMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  const archedEyeGeo = new THREE.TorusGeometry(0.6, 0.15, 16, 32, Math.PI);

  const leftEye = new THREE.Mesh(eyeGeo, eyeMat);
  leftEye.position.set(-1.8, 1, 6.5);
  eyeGroup.add(leftEye);

  const rightEye = new THREE.Mesh(eyeGeo, eyeMat);
  rightEye.position.set(1.8, 1, 6.5);
  eyeGroup.add(rightEye);

  const earGeo = new THREE.SphereGeometry(1.2, 32, 32);
  const earMat = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.4,
    blending: THREE.AdditiveBlending
  });

  const leftEar = new THREE.Mesh(earGeo, earMat);
  leftEar.scale.set(0.8, 1.5, 0.8);
  leftEar.position.set(-8, 5, 0);
  scene.add(leftEar);

  const rightEar = new THREE.Mesh(earGeo, earMat);
  rightEar.scale.set(0.8, 1.5, 0.8);
  rightEar.position.set(8, 5, 0);
  scene.add(rightEar);

  // ── moods ──────────────────────────────────────────────────────
  // The face is the whole vocabulary now, so each mood has to be readable at
  // a glance: eye shape, tilt, size and the colour of both eyes and aura.
  // `slant` mirrors between the eyes, which is what makes a brow read as a
  // brow rather than as two unrelated arcs.
  const MOODS = {
    idle:   { arc: false, rot: 0, slant: 0,    scale: { x: 1,    y: 1 },    eye: [1, 1, 1],        aura: [1, 1, 1],       dur: 0.55 },
    // hover — the four below are picked at random so it does not feel scripted
    glare:  { arc: true,  rot: 0, slant: 0.55, scale: { x: 1.25, y: 1 },    eye: [1, 0.34, 0.22],  aura: [1, 0.3, 0.2],   dur: 0.26 },
    narrow: { arc: false, rot: 0, slant: 0,    scale: { x: 1.5,  y: 0.22 }, eye: [1, 0.45, 0.25],  aura: [1, 0.36, 0.18], dur: 0.24 },
    scan:   { arc: false, rot: 0, slant: 0,    scale: { x: 1.35, y: 1.35 }, eye: [1, 0.68, 0.2],   aura: [1, 0.55, 0.15], dur: 0.3 },
    charge: { arc: false, rot: 0, slant: 0,    scale: { x: 0.75, y: 0.75 }, eye: [1, 0.85, 0.55],  aura: [1, 0.72, 0.3],  dur: 0.2 },
    // working / talking
    lock:   { arc: false, rot: 0, slant: 0,    scale: { x: 0.45, y: 0.45 }, eye: [1, 0.12, 0.1],   aura: [1, 0.1, 0.08],  dur: 0.18 },
    speak:  { arc: false, rot: 0, slant: 0,    scale: { x: 1.1,  y: 1.1 },  eye: [1, 0.62, 0.28],  aura: [1, 0.45, 0.2],  dur: 0.3 },
  };
  const HOVER_MOODS = ['glare', 'narrow', 'scan', 'charge'];

  let currentMood = 'idle';
  let hovering = false;
  // A "busy" mood outranks hover: while the model is answering, the face
  // should not flip back to idle because the pointer wandered off.
  let busyMood = null;

  const tween = (targets, props, duration) => {
    if (window.gsap) {
      gsap.to(targets, { ...props, duration, overwrite: 'auto' });
      return;
    }
    // No GSAP (it is a CDN script and may not load): apply instantly rather
    // than leaving the face frozen in whatever it was.
    (Array.isArray(targets) ? targets : [targets]).forEach((t) => {
      Object.entries(props).forEach(([k, v]) => { if (k in t) t[k] = v; });
    });
  };

  function applyMood(name) {
    const m = MOODS[name] || MOODS.idle;
    currentMood = name;

    leftEye.geometry = m.arc ? archedEyeGeo : eyeGeo;
    rightEye.geometry = m.arc ? archedEyeGeo : eyeGeo;
    leftEye.rotation.x = m.rot;
    rightEye.rotation.x = m.rot;
    // Mirrored, so the pair reads as one expression.
    leftEye.rotation.z = m.slant;
    rightEye.rotation.z = -m.slant;

    tween([leftEye.scale, rightEye.scale], { x: m.scale.x, y: m.scale.y, z: 1 }, m.dur);
    tween(eyeMat.color, { r: m.eye[0], g: m.eye[1], b: m.eye[2] }, m.dur);
    tween([leftEar.material.color, rightEar.material.color],
          { r: m.aura[0], g: m.aura[1], b: m.aura[2] }, m.dur);
  }

  setMood = function (name) {
    if (name === 'idle') {
      busyMood = null;
      applyMood(hovering ? pickHoverMood() : 'idle');
      return;
    }
    busyMood = name;
    applyMood(name);
  };

  let lastHoverMood = '';
  function pickHoverMood() {
    // Never the same face twice running; repetition is what made the old
    // random bubble feel broken rather than alive.
    const pool = HOVER_MOODS.filter((m) => m !== lastHoverMood);
    lastHoverMood = pool[Math.floor(Math.random() * pool.length)];
    return lastHoverMood;
  }

  container.addEventListener('mouseenter', () => {
    hovering = true;
    if (busyMood) return;
    applyMood(pickHoverMood());
    tween(particleSphere.position, { y: particleSphere.position.y + 1.5 }, 0.12);
    setTimeout(() => tween(particleSphere.position, { y: 0 }, 0.2), 130);
  });

  container.addEventListener('mouseleave', () => {
    hovering = false;
    if (busyMood) return;
    applyMood('idle');
  });

  // ── blink ──────────────────────────────────────────────────────
  // Only when the eyes are round; squeezing an already-narrowed brow reads as
  // a glitch, not a blink.
  function scheduleBlink() {
    setTimeout(() => {
      const m = MOODS[currentMood];
      if (m && !m.arc && m.scale.y > 0.5 && window.gsap) {
        gsap.to([leftEye.scale, rightEye.scale], {
          y: 0.08, duration: 0.07, yoyo: true, repeat: 1, overwrite: 'auto',
        });
      }
      scheduleBlink();
    }, 3500 + Math.random() * 5000);
  }
  scheduleBlink();

  // ── gaze ───────────────────────────────────────────────────────
  // Tracked from the sprite's live centre, so it keeps working after the
  // sprite is dragged somewhere else. Normalising by a fixed radius rather
  // than the window means the gaze does not go slack on a wide monitor.
  const GAZE_RADIUS = 420;
  let mx = 0;
  let my = 0;
  let gazeX = 0;
  let gazeY = 0;

  window.addEventListener('mousemove', (e) => {
    const r = container.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    mx = Math.max(-1, Math.min(1, (e.clientX - cx) / GAZE_RADIUS));
    my = Math.max(-1, Math.min(1, (e.clientY - cy) / GAZE_RADIUS));
  }, { passive: true });

  // Touch has no hover; a tap still deserves a reaction.
  window.addEventListener('touchmove', (e) => {
    const t = e.touches?.[0];
    if (!t) return;
    const r = container.getBoundingClientRect();
    mx = Math.max(-1, Math.min(1, (t.clientX - (r.left + r.width / 2)) / GAZE_RADIUS));
    my = Math.max(-1, Math.min(1, (t.clientY - (r.top + r.height / 2)) / GAZE_RADIUS));
  }, { passive: true });

  container.addEventListener('click', (e) => {
    if (disableAiChatOnPage) return;
    e.stopPropagation();
    toggleChat();
  });

  container.title = `打开 ${NAME}（拖动可移动，右下角可缩放）`;

  // ── drag / resize ──────────────────────────────────────────────
  window.FloatingPanel?.make(container, {
    id: 'sprite',
    aspect: 1,
    minWidth: 64,
    maxWidth: 320,
    onResize: (w, h) => {
      W = w * RENDER_SCALE;
      H = h * RENDER_SCALE;
      renderer.setSize(W, H);
      camera.aspect = W / H;
      camera.updateProjectionMatrix();
    },
    onMove: () => {
      if (chatDialog?.classList.contains('open')) positionChatNearSprite();
    },
  });

  const base = sphereGeo.attributes.position.array.slice();
  let time = 0;

  function animate() {
    requestAnimationFrame(animate);
    time += 0.015;

    const pos = sphereGeo.attributes.position.array;
    for (let i = 0; i < count; i++) {
        const px = base[i * 3];
        const py = base[i * 3 + 1];
        const pz = base[i * 3 + 2];
        const noise = Math.sin(px * 0.4 + time) * Math.cos(py * 0.4 + time) * Math.sin(pz * 0.4 + time);
        const displacement = 1 + noise * 0.15;
        pos[i * 3] = px * displacement;
        pos[i * 3 + 1] = py * displacement;
        pos[i * 3 + 2] = pz * displacement;
    }
    sphereGeo.attributes.position.needsUpdate = true;

    // The idle bob is a transform, and dragging writes left/top, so the two
    // never fight over the same property.
    container.style.transform = `translateY(${Math.sin(time * 0.8) * 15}px)`;
    leftEar.position.y = 5 + Math.sin(time * 1.5) * 1.5;
    rightEar.position.y = 5 + Math.cos(time * 1.5) * 1.5;
    leftEar.rotation.z = Math.sin(time) * 0.2;
    rightEar.rotation.z = -Math.sin(time) * 0.2;

    // Ease toward the pointer instead of snapping, so the gaze reads as
    // following rather than teleporting.
    gazeX += (mx - gazeX) * 0.12;
    gazeY += (my - gazeY) * 0.12;

    particleSphere.rotation.y += 0.005;
    particleSphere.rotation.x += (gazeY * 0.45 - particleSphere.rotation.x) * 0.05;
    particleSphere.rotation.y += (gazeX * 0.45 - particleSphere.rotation.y) * 0.05;

    eyeGroup.position.copy(particleSphere.position);
    const lookX = 1.5;
    const lookY = 1.1;
    leftEye.position.x = -1.8 + gazeX * lookX;
    leftEye.position.y = 1 - gazeY * lookY;
    rightEye.position.x = 1.8 + gazeX * lookX;
    rightEye.position.y = 1 - gazeY * lookY;
    // Push the eyes forward as they travel out, so they stay on the surface
    // of the sphere instead of sinking into it at the extremes.
    const depth = 6.5 - (Math.abs(gazeX) + Math.abs(gazeY)) * 0.5;
    leftEye.position.z = depth;
    rightEye.position.z = depth;

    renderer.render(scene, camera);
  }

  animate();
  applyMood('idle');
  window.toggleSpriteChat = toggleChat;
})();
