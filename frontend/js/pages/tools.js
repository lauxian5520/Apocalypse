/* tools.js — page script for tools.html. */
  const TOOLS = [
      {
          icon: '📝', name: 'Markdown 编辑器', desc: '实时 Markdown 预览', modal: `
  <h3 style="margin-bottom:1rem">Markdown 编辑器</h3>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;height:300px">
    <textarea id="md-in" class="textarea" style="height:100%" placeholder="输入 Markdown..."></textarea>
    <div id="md-out" style="padding:0.75rem;overflow-y:auto;background:rgba(255,255,255,0.03);border-radius:8px;font-size:0.9rem"></div>
  </div>`
      },
      {
          icon: '🎨', name: '颜色工具', desc: '颜色转换与调色板', modal: `
  <h3 style="margin-bottom:1rem">颜色工具</h3>
  <input type="color" id="color-picker" value="#6c63ff" style="width:100%;height:80px;border:none;border-radius:8px;cursor:pointer">
  <div id="color-values" style="margin-top:1rem;font-family:var(--font-mono);font-size:0.85rem;display:flex;flex-direction:column;gap:0.5rem"></div>`
      },
      {
          icon: '⏱', name: '专注计时器', desc: 'Pomodoro 番茄钟', modal: `
  <h3 style="margin-bottom:1.5rem">番茄钟</h3>
  <div style="text-align:center">
    <div id="timer-display" style="font-family:var(--font-display);font-size:4rem;letter-spacing:2px;color:var(--accent)">25:00</div>
    <div style="display:flex;justify-content:center;gap:1rem;margin-top:1.5rem">
      <button class="btn btn-primary" id="timer-start">开始</button>
      <button class="btn" id="timer-reset">重置</button>
    </div>
  </div>`
      },
      {
          icon: '🔐', name: '密码生成器', desc: '生成强随机密码', modal: `
  <h3 style="margin-bottom:1rem">密码生成器</h3>
  <div style="display:flex;flex-direction:column;gap:1rem">
    <div class="field"><label>长度: <span id="len-val">16</span></label>
    <input type="range" min="8" max="64" value="16" id="pwd-len" class="mp-volume-slider" style="width:100%;height:4px"></div>
    <label style="display:flex;align-items:center;gap:.5rem;font-size:.85rem;color:var(--text-2)">
      <input type="checkbox" id="pwd-sym" checked> 包含特殊符号</label>
    <div id="pwd-result" style="font-family:var(--font-mono);font-size:0.9rem;padding:0.75rem;background:rgba(255,255,255,0.05);border-radius:8px;word-break:break-all;letter-spacing:1px"></div>
    <button class="btn btn-primary" id="pwd-gen">生成密码</button>
    <button class="btn btn-sm" id="pwd-copy">复制</button>
  </div>`
      },
      { icon: '🌐', name: '实时翻译', desc: '大语言模型站内多语言翻译', modal: `
  <h3 id="translation-title">实时翻译</h3>
  <p class="translation-hint">AI实时翻译 · 登录后使用 · 支持 Ctrl / ⌘ + Enter</p>
  <form id="translation-form">
    <div class="translation-languages">
      <label>源语言<select id="translation-source"><option value="auto">自动检测</option></select></label>
      <button type="button" class="btn btn-sm" id="translation-swap" aria-label="交换语言">⇄</button>
      <label>目标语言<select id="translation-target"></select></label>
    </div>
    <div class="translation-panels">
      <label>原文<textarea id="translation-input" class="textarea" rows="9" maxlength="4000" placeholder="输入需要翻译的文本…" required></textarea></label>
      <label>译文<textarea id="translation-output" class="textarea" rows="9" placeholder="译文将在这里显示" readonly></textarea></label>
    </div>
    <div class="translation-actions">
      <span id="translation-count">0 / 4000</span>
      <button type="button" class="btn btn-sm" id="translation-copy" disabled>复制译文</button>
      <button type="submit" class="btn btn-primary" id="translation-submit">翻译</button>
    </div>
    <p id="translation-status" role="status" aria-live="polite"></p>
  </form>` },
      {
          icon: '📊', name: 'JSON 格式化', desc: '美化 JSON 数据', modal: `
  <h3 style="margin-bottom:1rem">JSON 格式化</h3>
  <textarea class="textarea" id="json-in" placeholder='{"key":"value"}' rows="5"></textarea>
  <button class="btn btn-primary btn-sm" onclick="try{document.getElementById('json-out').textContent=JSON.stringify(JSON.parse(document.getElementById('json-in').value),null,2)}catch(e){document.getElementById('json-out').textContent='错误: '+e.message}" style="margin:.5rem 0">格式化</button>
  <pre id="json-out" style="font-family:var(--font-mono);font-size:0.8rem;max-height:200px;overflow-y:auto;background:rgba(255,255,255,0.03);padding:.75rem;border-radius:8px"></pre>`
      },
      ...LocalTools.tools,
      { icon: '✉️', name: '联系作者', desc: '查看作者联系方式', url: 'space.html' },
  ];

  const grid = document.getElementById('tools-grid');
  const modal = document.getElementById('tool-modal');
  const modalClose = document.getElementById('modal-close');
  const modalContent = document.getElementById('modal-content');
  let disposeModal = () => {};
  let modalOpener = null;

  function closeToolModal() {
      disposeModal();
      disposeModal = () => {};
      modal.classList.remove('active');
      modalContent.replaceChildren();
      document.body.classList.remove('tool-modal-open');
      if (modalOpener?.isConnected) modalOpener.focus();
  }

  function renderTools(tools) {
      grid.innerHTML = tools.map((t, i) => `
  <div class="card tool-card" data-i="${i}" role="button" tabindex="0">
    <div class="tool-icon">${t.icon}</div>
    <div class="tool-name">${t.name}</div>
    <div class="tool-desc">${t.desc}</div>
  </div>
`).join('');

      grid.querySelectorAll('.tool-card').forEach(card => {
          card.addEventListener('click', () => {
              const t = tools[+card.dataset.i];
              if (t.url) { window.open(t.url, '_blank'); return; }
              if (t.action) { t.action(); return; }
              if (t.modal) {
                  closeToolModal();
                  modalOpener = card;
                  modalContent.innerHTML = t.modal;
                  modal.classList.add('active');
                  document.body.classList.add('tool-modal-open');
                  disposeModal = initModalTools(t);
                  modalClose.focus();
              }
          });
          card.addEventListener('keydown', (e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
          });
      });
  }

  function initModalTools(tool) {
      const disposeLocal = LocalTools.init(tool.id, modalContent);
      initTranslation();
      const mdIn = document.getElementById('md-in');
      if (mdIn) {
          mdIn.addEventListener('input', () => {
              document.getElementById('md-out').innerHTML = mdIn.value.replace(/\n/g, '<br>');
          });
      }

      const cp = document.getElementById('color-picker');
      if (cp) {
          function updateColor() {
              const hx = cp.value;
              const r = parseInt(hx.slice(1, 3), 16);
              const g = parseInt(hx.slice(3, 5), 16);
              const b = parseInt(hx.slice(5, 7), 16);
              document.getElementById('color-values').innerHTML = `<span>HEX: ${hx}</span><span>RGB: rgb(${r}, ${g}, ${b})</span>`;
          }
          cp.addEventListener('input', updateColor);
          updateColor();
      }

      let timerInterval = null;
      let timerSec = 25 * 60;
      const td = document.getElementById('timer-display');
      const ts = document.getElementById('timer-start');
      const tr = document.getElementById('timer-reset');
      if (ts && tr && td) {
          ts.addEventListener('click', () => {
              if (timerInterval) {
                  clearInterval(timerInterval);
                  timerInterval = null;
                  ts.textContent = '继续';
                  return;
              }
              timerInterval = setInterval(() => {
                  timerSec--;
                  if (timerSec < 0) {
                      clearInterval(timerInterval);
                      timerInterval = null;
                      td.textContent = '完成!';
                      return;
                  }
                  td.textContent = `${String(Math.floor(timerSec / 60)).padStart(2, '0')}:${String(timerSec % 60).padStart(2, '0')}`;
              }, 1000);
              ts.textContent = '暂停';
          });

          tr.addEventListener('click', () => {
              clearInterval(timerInterval);
              timerInterval = null;
              timerSec = 25 * 60;
              td.textContent = '25:00';
              ts.textContent = '开始';
          });
      }

      const pg = document.getElementById('pwd-gen');
      if (pg) {
          const pl = document.getElementById('pwd-len');
          pl.addEventListener('input', () => { document.getElementById('len-val').textContent = pl.value; });

          function gen() {
              const len = +pl.value;
              const sym = document.getElementById('pwd-sym').checked;
              const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' + (sym ? '!@#$%^&*()_+-=[]{}' : '');
              document.getElementById('pwd-result').textContent = Array.from({ length: len }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
          }

          pg.addEventListener('click', gen);
          gen();

          document.getElementById('pwd-copy').addEventListener('click', () => {
              navigator.clipboard.writeText(document.getElementById('pwd-result').textContent);
              toast('已复制！', 'success');
          });
      }
      return () => { disposeLocal(); clearInterval(timerInterval); };
  }

  function initTranslation() {
      const form = document.getElementById('translation-form');
      if (!form) return;
      const languages = { zh: '简体中文', en: '英语', ja: '日语', ko: '韩语', fr: '法语', de: '德语', es: '西班牙语', ru: '俄语' };
      const source = document.getElementById('translation-source');
      const target = document.getElementById('translation-target');
      const input = document.getElementById('translation-input');
      const output = document.getElementById('translation-output');
      const submit = document.getElementById('translation-submit');
      const copy = document.getElementById('translation-copy');
      const swap = document.getElementById('translation-swap');
      const status = document.getElementById('translation-status');
      for (const [value, name] of Object.entries(languages)) {
          source.add(new Option(name, value));
          target.add(new Option(name, value));
      }
      target.value = 'zh';
      const count = document.getElementById('translation-count');
      const updateCount = () => { count.textContent = `${input.value.length} / 4000`; };
      input.addEventListener('input', updateCount);
      swap.addEventListener('click', () => {
          if (source.value === 'auto') { status.textContent = '请先选择源语言，再交换语言。'; return; }
          const original = source.value;
          source.value = target.value;
          target.value = original;
          if (output.value) { input.value = output.value; output.value = ''; copy.disabled = true; updateCount(); }
      });
      copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(output.value); status.textContent = '译文已复制。'; }
          catch (_) { status.textContent = '无法自动复制，请选择译文手动复制。'; }
      });
      input.addEventListener('keydown', (e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); if (!submit.disabled) form.requestSubmit(); }
      });
      form.addEventListener('submit', async (e) => {
          e.preventDefault();
          if (!input.value.trim()) { status.textContent = '请输入需要翻译的文本。'; return; }
          if (!Auth.token()) { status.textContent = '请先登录后使用翻译。'; return; }
          submit.disabled = swap.disabled = source.disabled = target.disabled = input.readOnly = true;
          copy.disabled = true;
          output.value = '';
          submit.textContent = '翻译中…';
          status.textContent = '大语言模型正在翻译，请稍候…';
          try {
              const result = await apiFetch('/ai/translate', { method: 'POST', body: JSON.stringify({
                  text: input.value, source_language: source.value, target_language: target.value,
              }) });
              output.value = result.translation;
              copy.disabled = !output.value;
              status.textContent = '翻译完成。';
          } catch (err) { status.textContent = `翻译失败：${err.message}`; }
          finally {
              submit.disabled = swap.disabled = source.disabled = target.disabled = input.readOnly = false;
              submit.textContent = '翻译';
          }
      });
  }

  modalClose.addEventListener('click', closeToolModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeToolModal(); });
  document.addEventListener('keydown', (e) => {
      if (!modal.classList.contains('active')) return;
      if (e.key === 'Escape') closeToolModal();
      if (e.key === 'Tab') {
          const focusable = Array.from(modal.querySelectorAll('button, input, textarea, select, a[href]')).filter(el => !el.disabled && el.getClientRects().length);
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
  });

  document.getElementById('tools-search').addEventListener('input', function () {
      const q = this.value.toLowerCase();
      renderTools(TOOLS.filter(t => t.name.toLowerCase().includes(q) || t.desc.toLowerCase().includes(q)));
  });

  renderTools(TOOLS);
