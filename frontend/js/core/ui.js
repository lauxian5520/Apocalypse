/* ═══════════════════════════════════════════════════════════════
   ui.js — Chrome shared by every page: animated background,
   cursor trail, scroll progress, navigation, widget bootstrap.

   Load last of the core scripts; it is the only one with side effects.
═══════════════════════════════════════════════════════════════ */

// ── Particle background ──────────────────────────────────────────
class AntigravityBackground {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.particles = [];
    this.mouse = { x: -1000, y: -1000 };
    this.particleCount = window.innerWidth < 768 ? 60 : 130;
    this.connectionDistance = 130;
    this.init();
    this.animate();
    this.handleResize();
    this.handleMouse();
  }

  init() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
    this.createParticles();
  }

  createParticles() {
    this.particles = Array.from({ length: this.particleCount }, () => ({
      x: Math.random() * this.canvas.width,
      y: Math.random() * this.canvas.height,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      r: Math.random() * 1.5 + 0.5,
      a: Math.random(),
    }));
  }

  handleResize() {
    window.addEventListener('resize', () => {
      this.canvas.width = window.innerWidth;
      this.canvas.height = window.innerHeight;
      this.createParticles();
    });
  }

  handleMouse() {
    window.addEventListener('mousemove', e => {
      this.mouse.x = e.clientX;
      this.mouse.y = e.clientY;
    });
  }

  drawParticles() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > this.canvas.width) p.vx *= -1;
      if (p.y < 0 || p.y > this.canvas.height) p.vy *= -1;

      const dx = p.x - this.mouse.x;
      const dy = p.y - this.mouse.y;
      if (Math.sqrt(dx * dx + dy * dy) < 80) {
        p.x += dx * 0.03;
        p.y += dy * 0.03;
      }

      this.ctx.beginPath();
      this.ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      this.ctx.fillStyle = `rgba(108,99,255,${p.a * 0.7})`;
      this.ctx.fill();

      for (let j = i + 1; j < this.particles.length; j++) {
        const q = this.particles[j];
        const d = Math.hypot(p.x - q.x, p.y - q.y);
        if (d < this.connectionDistance) {
          this.ctx.beginPath();
          this.ctx.moveTo(p.x, p.y);
          this.ctx.lineTo(q.x, q.y);
          this.ctx.strokeStyle = `rgba(108,99,255,${(1 - d / this.connectionDistance) * 0.18})`;
          this.ctx.lineWidth = 0.8;
          this.ctx.stroke();
        }
      }
    }
  }

  animate() {
    this.drawParticles();
    requestAnimationFrame(() => this.animate());
  }
}

// ── Cursor trail ─────────────────────────────────────────────────
function initCursorTrail() {
  const style = document.createElement('style');
  style.textContent =
    '@keyframes cursorFade{to{opacity:0;transform:translate(-50%,-50%) scale(0)}}';
  document.head.appendChild(style);

  document.addEventListener('mousemove', e => {
    const dot = document.createElement('div');
    Object.assign(dot.style, {
      position: 'fixed', left: e.clientX + 'px', top: e.clientY + 'px',
      width: '5px', height: '5px', borderRadius: '50%',
      background: 'rgba(108,99,255,0.7)', pointerEvents: 'none',
      zIndex: '99999', transform: 'translate(-50%,-50%)',
      animation: 'cursorFade 0.6s forwards',
    });
    document.body.appendChild(dot);
    setTimeout(() => dot.remove(), 600);
  });
}

// ── Scroll progress bar ──────────────────────────────────────────
function initScrollProgress() {
  const bar = document.createElement('div');
  bar.className = 'scroll-progress';
  document.body.prepend(bar);
  window.addEventListener('scroll', () => {
    const scrollable = document.body.scrollHeight - window.innerHeight;
    bar.style.width = (scrollable > 0 ? (window.scrollY / scrollable) * 100 : 0) + '%';
  });
}

// ── Reveal-on-scroll ─────────────────────────────────────────────
function initRevealAnimations() {
  const observer = new IntersectionObserver(
    entries => {
      entries.forEach(e => {
        if (!e.isIntersecting) return;
        e.target.style.opacity = '1';
        e.target.style.transform = 'translateY(0)';
      });
    },
    { threshold: 0.1 }
  );
  document.querySelectorAll('.card, .section-header, .reveal').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(28px)';
    el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(el);
  });
}

// ── Navigation ───────────────────────────────────────────────────
function renderNavUser() {
  const area = document.getElementById('nav-user-area');
  if (!area) return;

  const user = Auth.user();
  if (!user) {
    area.innerHTML = `
      <a href="login.html" class="btn btn-sm">登录</a>
      <a href="register.html" class="btn btn-sm btn-primary">注册</a>`;
    return;
  }

  const avatar = escHtml(user.avatar_url || 'assets/images/default-avatar.svg');
  area.innerHTML = `
    <a href="memos.html" class="btn btn-sm">我的空间</a>
    <a href="messages.html" class="btn btn-sm">私聊</a>
    ${Auth.isAdmin() ? '<a href="admin.html" class="btn btn-sm">后台</a>' : ''}
    <a href="profile.html?uid=${user.id}" title="查看个人资料">
      <img src="${avatar}" class="nav-avatar" title="${escHtml(user.username)}" id="nav-avatar-btn">
    </a>
    <button type="button" class="btn btn-sm" id="nav-logout-btn">退出</button>`;

  document.getElementById('nav-logout-btn')?.addEventListener('click', () => {
    Auth.logout().finally(() => {
      window.location.href = 'index.html';
    });
  });
}

function initNav() {
  const toggle = document.querySelector('.nav-toggle');
  const links = document.querySelector('.nav-links');
  toggle?.addEventListener('click', () => {
    links?.classList.toggle('open');
    toggle.classList.toggle('active');
  });

  const current = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a').forEach(a => {
    if (a.getAttribute('href') === current) a.classList.add('active');
  });

  Auth.init().finally(renderNavUser);
}

// ── Widgets ──────────────────────────────────────────────────────
const THREE_CDN = 'https://cdn.jsdelivr.net/npm/three@0.161.0/build/three.min.js';

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded === '1') resolve();
      else existing.addEventListener('load', () => resolve(), { once: true });
      return;
    }
    const s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = () => {
      s.dataset.loaded = '1';
      resolve();
    };
    s.onerror = () => reject(new Error(`load failed: ${src}`));
    document.body.appendChild(s);
  });
}

/** Ensure the sprite assistant exists even on pages that don't include it. */
function ensureSpriteAssistant() {
  if (window.__spriteAssistantBooted) return;
  window.__spriteAssistantBooted = true;

  if (!document.querySelector('link[href$="sprite-chat.css"]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'css/components/sprite-chat.css';
    document.head.appendChild(link);
  }
  if (!document.getElementById('sprite-container')) {
    const el = document.createElement('div');
    el.id = 'sprite-container';
    document.body.appendChild(el);
  }

  const chain = typeof window.THREE === 'undefined' ? loadScript(THREE_CDN) : Promise.resolve();
  chain.then(() => loadScript('js/widgets/sprite-chat.js')).catch(() => {});
}

// ── Boot ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.createElement('canvas');
  canvas.id = 'bg-canvas';
  canvas.style.cssText = 'position:fixed;inset:0;z-index:0;pointer-events:none;';
  document.getElementById('canvas-container')?.appendChild(canvas) ||
    document.body.insertAdjacentElement('afterbegin', canvas);
  new AntigravityBackground('bg-canvas');

  initCursorTrail();
  initScrollProgress();
  initRevealAnimations();
  initNav();
  ensureSpriteAssistant();
});

window.renderNavUser = renderNavUser;
