/* sprite-chat.js — floating 3D assistant: SSE chat, page summary, world clock. */
(function () {
  if (window.__MW_SPRITE_CHAT_LOADED) return;
  window.__MW_SPRITE_CHAT_LOADED = true;

  const pageName = (location.pathname.split('/').pop() || '').toLowerCase();
  const disableAiChatOnPage = pageName === 'messages.html';

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
  }

  function ensureChatUI() {
    if (disableAiChatOnPage) return;
    if (document.getElementById('chat-dialog')) return;
    const tpl = `
      <div class="chat-dialog" id="chat-dialog">
        <div class="chat-dialog-header">
          <div class="chat-dialog-title"><span>小七</span></div>
          <div class="chat-dialog-actions">
            <button class="chat-mini-btn" id="chat-summary" title="总结当前页面">总结本页</button>
            <button class="chat-dialog-close" id="chat-close">✕</button>
          </div>
        </div>
        <div class="chat-messages" id="chat-messages">
          <div class="msg msg-ai">你好，我是 小七。登录后我可以与你对话，并帮你总结当前页面内容。</div>
        </div>
        <div class="typing-indicator" id="typing-indicator" style="display:none">
          <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
        </div>
        <div class="chat-dialog-footer">
          <input type="text" id="chat-input" placeholder="输入消息，或输入“总结本页”" autocomplete="off">
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

  function toggleChat() {
    chatDialog.classList.toggle('open');
    if (chatDialog.classList.contains('open')) {
      chatInput.focus();
    }
  }

  function pageSummaryText() {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,.chat-dialog,#sprite-container,.noise-overlay,.header,.nav,.nav-links,.nav-user,.nav-toggle').forEach((n) => n.remove());
    const txt = (clone.innerText || '').replace(/\s+/g, ' ').trim();
    return txt.slice(0, 5000);
  }

  async function summarizeCurrentPage() {
    if (!(await isLoggedIn())) {
      appendMsg('ai', '请先登录后再使用页面总结。');
      return;
    }

    const text = pageSummaryText();
    if (!text) {
      appendMsg('ai', '当前页面没有可总结的文本内容。');
      return;
    }

    typing.style.display = 'flex';
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
      appendMsg('ai', '请先登录才能使用 AI 对话。');
      return;
    }

    appendMsg('user', text);
    history.push({ role: 'user', content: text });
    // Keep the conversation bounded so requests don't grow without limit.
    if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);

    typing.style.display = 'flex';

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

  // 3D sprite (with graceful fallback)
  if (!hasThree) {
    container.innerHTML = '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:52px;">✨</div>';
    container.title = '打开 小七';
    container.addEventListener('click', (e) => {
      if (disableAiChatOnPage) return;
      e.stopPropagation();
      toggleChat();
    });
    window.toggleSpriteChat = toggleChat;
    return;
  }

  const W = 180;
  const H = 180;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
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


  let time = 0;
  let mx = 0;
  let my = 0;
  let isHovered = false;

  const greetEl = document.createElement('div');
  greetEl.className = 'sprite-greeting';
  container.appendChild(greetEl);

  let isGreeting = false;
  let greetTimeout = null;

  container.addEventListener('mouseenter', () => {
    isHovered = true;
    if (isGreeting || !window.gsap) {
        greetEl.textContent = '✨ 点击和我聊聊 (小七)';
        greetEl.classList.add('show');
        return;
    }
    isGreeting = true;

    const reactions = [
      { msg: "(>////<)", eyeScale: { x: 1, y: 0.15 }, color: { r: 1, g: 0.5, b: 0.6 }, shape: 'sphere' },
      { msg: "😳", eyeScale: { x: 1.8, y: 1.8 }, color: { r: 1, g: 0.4, b: 0.4 }, shape: 'sphere' },
      { msg: "(〃∀〃)", eyeScale: { x: 1.2, y: 1.2 }, color: { r: 1, g: 0.6, b: 0.8 }, shape: 'arc', rot: Math.PI },
      { msg: "✨", eyeScale: { x: 1.5, y: 1.5 }, color: { r: 1, g: 1, b: 0.4 }, shape: 'sphere' },
      { msg: "(/▽＼)", eyeScale: { x: 1, y: 0.1 }, color: { r: 1, g: 0.5, b: 0.7 }, shape: 'arc', rot: 0 }
    ];
    const r = reactions[Math.floor(Math.random() * reactions.length)];
    
    greetEl.textContent = r.msg;
    greetEl.classList.add('show');

    if (r.shape === 'arc') {
      leftEye.geometry = archedEyeGeo;
      rightEye.geometry = archedEyeGeo;
      leftEye.rotation.x = r.rot;
      rightEye.rotation.x = r.rot;
    } else {
      leftEye.geometry = eyeGeo;
      rightEye.geometry = eyeGeo;
      leftEye.rotation.x = 0;
      rightEye.rotation.x = 0;
    }

    gsap.to([leftEar.material.color, rightEar.material.color], { r: r.color.r, g: r.color.g, b: r.color.b, duration: 0.3 });
    gsap.to([leftEye.scale, rightEye.scale], { x: r.eyeScale.x, y: r.eyeScale.y, duration: 0.3 });
    gsap.to(particleSphere.position, { y: particleSphere.position.y + 2, duration: 0.1, yoyo: true, repeat: 1 });
  });

  container.addEventListener('mouseleave', () => {
    isHovered = false;
    mx = 0;
    my = 0;
    
    if (greetTimeout) clearTimeout(greetTimeout);
    greetTimeout = setTimeout(() => {
        if (window.gsap) {
            gsap.to([leftEar.material.color, rightEar.material.color], { r: 1, g: 1, b: 1, duration: 1 });
            gsap.to([leftEye.scale, rightEye.scale], {
                x: 1, y: 1, z: 1,
                duration: 0.5,
                onComplete: () => {
                    leftEye.geometry = eyeGeo;
                    rightEye.geometry = eyeGeo;
                    leftEye.rotation.x = 0;
                    rightEye.rotation.x = 0;
                }
            });
        }
        greetEl.classList.remove('show');
        isGreeting = false;
    }, 500);
  });

  window.addEventListener('mousemove', (e) => {
    const rect = container.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    mx = ((e.clientX - centerX) / window.innerWidth) * 4; 
    my = ((e.clientY - centerY) / window.innerHeight) * 4;
    mx = Math.max(-1.5, Math.min(1.5, mx));
    my = Math.max(-1.5, Math.min(1.5, my));
  });

  container.addEventListener('click', (e) => {
    if (disableAiChatOnPage) return;
    e.stopPropagation();
    toggleChat();
  });

  const base = sphereGeo.attributes.position.array.slice();

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
    
    container.style.transform = `translateY(${Math.sin(time * 0.8) * 15}px)`;
    leftEar.position.y = 5 + Math.sin(time * 1.5) * 1.5;
    rightEar.position.y = 5 + Math.cos(time * 1.5) * 1.5;
    leftEar.rotation.z = Math.sin(time) * 0.2;
    rightEar.rotation.z = -Math.sin(time) * 0.2;

    particleSphere.rotation.y += 0.005;
    const targetRotX = my * 0.4;
    const targetRotY = mx * 0.4;
    particleSphere.rotation.x += (targetRotX - particleSphere.rotation.x) * 0.05;
    particleSphere.rotation.y += (targetRotY - particleSphere.rotation.y) * 0.05;

    eyeGroup.position.copy(particleSphere.position);
    const lookFactorX = 1.2;
    const lookFactorY = 0.8;
    leftEye.position.x = -1.8 + mx * lookFactorX;
    leftEye.position.y = 1 - my * lookFactorY;
    rightEye.position.x = 1.8 + mx * lookFactorX;
    rightEye.position.y = 1 - my * lookFactorY;

    renderer.render(scene, camera);
  }

  animate();
  window.toggleSpriteChat = toggleChat;
})();


