/* ═══════════════════════════════════════════════════════════════
   floating-panel.js — make a fixed overlay draggable and resizable.

   The three floating widgets (music player, sprite, clock) sit on top of
   every page and used to cover buttons underneath them with no way out.
   This gives each one a grip and a corner handle, and remembers where the
   user put it.

   Deliberately generic: it knows nothing about music, sprites or clocks.
   A widget registers itself and says what resizing means to it via
   `onResize`, because a WebGL canvas and a text box scale differently.
═══════════════════════════════════════════════════════════════ */
(function () {
  if (window.FloatingPanel) return;

  const STORE_PREFIX = 'mw_panel_';
  // Below this the pointer is still considered to be clicking, not dragging.
  // Without it, the tiny movement during an ordinary click would swallow the
  // click and the play button would stop working.
  const DRAG_THRESHOLD = 4;
  const EDGE_MARGIN = 8;
  // How much of a panel must stay on screen. Enough to grab it back after a
  // window resize, or after opening a saved layout on a smaller display.
  const KEEP_VISIBLE = 32;

  const panels = new Map();

  // ── persistence ────────────────────────────────────────────────
  function load(id) {
    try {
      const raw = localStorage.getItem(STORE_PREFIX + id);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;   // private mode, blocked storage — defaults are fine
    }
  }

  function save(id, layout) {
    try {
      localStorage.setItem(STORE_PREFIX + id, JSON.stringify(layout));
    } catch (_) {
      // a lost layout is not worth an error
    }
  }

  function clear(id) {
    try {
      localStorage.removeItem(STORE_PREFIX + id);
    } catch (_) { /* ignore */ }
  }

  // ── geometry ───────────────────────────────────────────────────
  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  /** Keep a panel reachable: a saved position from a wide monitor must not
   *  leave it off-screen on a phone. */
  function clampToViewport(left, top, width, height) {
    return {
      left: clamp(left, EDGE_MARGIN - width + KEEP_VISIBLE,
                  window.innerWidth - KEEP_VISIBLE - EDGE_MARGIN),
      top: clamp(top, EDGE_MARGIN,
                 window.innerHeight - KEEP_VISIBLE - EDGE_MARGIN),
    };
  }

  /** Switch an element from its CSS `right`/`bottom` anchoring to explicit
   *  `left`/`top`, without letting it jump: the current rect is measured
   *  first and re-applied as the new coordinates. */
  function anchorTopLeft(el) {
    const r = el.getBoundingClientRect();
    el.style.left = `${r.left}px`;
    el.style.top = `${r.top}px`;
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    return r;
  }

  // ── the panel ──────────────────────────────────────────────────
  function make(el, options) {
    if (!el) return null;
    const opt = options || {};
    const id = opt.id || el.id;
    if (!id || panels.has(id)) return panels.get(id) || null;

    const state = {
      el,
      id,
      opt,
      base: null,        // the untouched default geometry, for reset
      moved: false,
    };

    const min = { w: opt.minWidth || 40, h: opt.minHeight || 28 };
    const max = { w: opt.maxWidth || 900, h: opt.maxHeight || 900 };

    function applySize(w, h) {
      const width = clamp(Math.round(w), min.w, max.w);
      const height = clamp(Math.round(h), min.h, max.h);
      el.style.width = `${width}px`;
      if (opt.autoHeight !== true) el.style.height = `${height}px`;
      if (typeof opt.onResize === 'function') opt.onResize(width, height);
      return { width, height };
    }

    function currentLayout() {
      const r = el.getBoundingClientRect();
      return { left: r.left, top: r.top, width: r.width, height: r.height };
    }

    function persist() {
      save(id, currentLayout());
    }

    // ── drag ─────────────────────────────────────────────────────
    const grip = opt.handle
      ? (typeof opt.handle === 'string' ? el.querySelector(opt.handle) : opt.handle)
      : el;

    let drag = null;

    function onPointerDown(e) {
      // Left button / touch / pen only, and never from the resize corner.
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest?.('.fp-resize')) return;
      if (opt.ignore && e.target.closest?.(opt.ignore)) return;

      const r = el.getBoundingClientRect();
      drag = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        originLeft: r.left,
        originTop: r.top,
        width: r.width,
        height: r.height,
        active: false,
      };
      grip.setPointerCapture?.(e.pointerId);
    }

    function onPointerMove(e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;

      if (!drag.active) {
        if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
        drag.active = true;
        state.moved = true;
        anchorTopLeft(el);
        el.classList.add('fp-dragging');
        document.body.classList.add('fp-drag-active');
      }

      e.preventDefault();
      const p = clampToViewport(drag.originLeft + dx, drag.originTop + dy,
                                drag.width, drag.height);
      el.style.left = `${p.left}px`;
      el.style.top = `${p.top}px`;
      if (typeof opt.onMove === 'function') opt.onMove(p.left, p.top);
    }

    function onPointerUp(e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      const wasDragging = drag.active;
      grip.releasePointerCapture?.(e.pointerId);
      drag = null;
      el.classList.remove('fp-dragging');
      document.body.classList.remove('fp-drag-active');
      if (!wasDragging) return;

      // The click that would follow this pointerup belongs to the drag, not to
      // the button underneath. Swallow exactly one.
      const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
      window.addEventListener('click', swallow, { capture: true, once: true });
      setTimeout(() => window.removeEventListener('click', swallow, true), 0);

      persist();
    }

    grip.addEventListener('pointerdown', onPointerDown);
    grip.addEventListener('pointermove', onPointerMove);
    grip.addEventListener('pointerup', onPointerUp);
    grip.addEventListener('pointercancel', onPointerUp);
    grip.classList.add('fp-grip');

    // ── resize ───────────────────────────────────────────────────
    let handle = null;
    if (opt.resizable !== false) {
      handle = document.createElement('div');
      handle.className = 'fp-resize';
      handle.title = '拖动调整大小，双击还原';
      handle.setAttribute('aria-label', '调整大小');
      el.appendChild(handle);

      let rs = null;
      handle.addEventListener('pointerdown', (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        e.stopPropagation();
        e.preventDefault();
        const r = el.getBoundingClientRect();
        rs = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, w: r.width, h: r.height };
        handle.setPointerCapture?.(e.pointerId);
        el.classList.add('fp-resizing');
      });

      handle.addEventListener('pointermove', (e) => {
        if (!rs || e.pointerId !== rs.pointerId) return;
        e.preventDefault();
        let w = rs.w + (e.clientX - rs.x);
        let h = rs.h + (e.clientY - rs.y);
        // A square sprite must stay square, or the WebGL camera skews.
        if (opt.aspect) h = w / opt.aspect;
        applySize(w, h);
      });

      const endResize = (e) => {
        if (!rs || e.pointerId !== rs.pointerId) return;
        handle.releasePointerCapture?.(e.pointerId);
        rs = null;
        el.classList.remove('fp-resizing');
        persist();
      };
      handle.addEventListener('pointerup', endResize);
      handle.addEventListener('pointercancel', endResize);

      // The escape hatch: a panel dragged somewhere useless is one
      // double-click from being back where it started.
      handle.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        e.preventDefault();
        reset(id);
      });
    }

    // ── restore ──────────────────────────────────────────────────
    // Measured before anything is applied, so reset() has somewhere to go back to.
    state.base = {
      cssText: el.style.cssText,
      rect: currentLayout(),
    };

    const saved = load(id);
    if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
      if (Number.isFinite(saved.width) && saved.width > 0) {
        applySize(saved.width, saved.height || saved.width);
      }
      const r = el.getBoundingClientRect();
      const p = clampToViewport(saved.left, saved.top, r.width, r.height);
      el.style.left = `${p.left}px`;
      el.style.top = `${p.top}px`;
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      state.moved = true;
    }

    panels.set(id, state);
    return state;
  }

  // ── reset ──────────────────────────────────────────────────────
  function reset(id) {
    const state = panels.get(id);
    if (!state) return;
    clear(id);
    state.el.style.cssText = state.base.cssText;
    state.moved = false;
    // Let the widget resize itself back to whatever its stylesheet says.
    if (typeof state.opt.onResize === 'function') {
      const r = state.el.getBoundingClientRect();
      state.opt.onResize(Math.round(r.width), Math.round(r.height));
    }
    if (typeof state.opt.onMove === 'function') {
      const r = state.el.getBoundingClientRect();
      state.opt.onMove(r.left, r.top);
    }
  }

  function resetAll() {
    Array.from(panels.keys()).forEach(reset);
  }

  // Panels that were fine on a large window must not end up off a small one.
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      panels.forEach((state) => {
        if (!state.moved) return;
        const r = state.el.getBoundingClientRect();
        const p = clampToViewport(r.left, r.top, r.width, r.height);
        if (p.left !== r.left || p.top !== r.top) {
          state.el.style.left = `${p.left}px`;
          state.el.style.top = `${p.top}px`;
          save(state.id, { left: p.left, top: p.top, width: r.width, height: r.height });
        }
      });
    }, 120);
  });

  window.FloatingPanel = { make, reset, resetAll };
})();
