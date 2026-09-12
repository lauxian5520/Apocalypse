/* ═══════════════════════════════════════════════════════════════
   split-pane.js — drag the dividers of a CSS grid, and remember where.

   The Harness workbench is three columns: session list, conversation,
   inspector. Their widths used to be hard-coded, so reading a long tool
   result meant squinting at a 380px column while the conversation had room
   to spare — and there was nothing the user could do about it.

   Deliberately generic, like floating-panel.js: it knows nothing about
   harness sessions. A page hands it a grid element and describes the tracks;
   this decides nothing about what the columns contain.

   Two things are less obvious than they look:

   - **Widths are stored for the side tracks, never for the middle one.**
     The middle track is `1fr`, so it absorbs every window resize on its own.
     Storing all three would fight the viewport: restore a layout saved on a
     wide monitor onto a laptop and the columns would overflow instead of the
     flexible one simply getting smaller.
   - **The page's own media queries still win.** Below the breakpoint the grid
     collapses to a stacked layout, and a saved three-column width would
     re-impose a layout that does not fit. So the inline style is removed
     entirely at narrow widths rather than recalculated.
═══════════════════════════════════════════════════════════════ */
(function () {
  if (window.SplitPane) return;

  const STORE_PREFIX = 'mw_split_';
  // Matches the `@media (max-width: 1200px)` rule in css/pages/harness.css,
  // where the grid stops being three columns. Kept in sync by hand; there is
  // no way to read a media query's breakpoint back out of CSS.
  const STACK_BELOW = 1200;

  const panes = new Map();

  // ── persistence ────────────────────────────────────────────────
  function load(id) {
    try {
      const raw = localStorage.getItem(STORE_PREFIX + id);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      // Private windows and cleared site data both throw here. A missing
      // layout is not an error — the defaults are perfectly usable.
      return null;
    }
  }

  function save(id, sizes) {
    try {
      localStorage.setItem(STORE_PREFIX + id, JSON.stringify(sizes));
    } catch (e) { /* not worth breaking the page over */ }
  }

  function clear(id) {
    try {
      localStorage.removeItem(STORE_PREFIX + id);
    } catch (e) { /* ignore */ }
  }

  // ── layout ─────────────────────────────────────────────────────
  function clamp(state, sizes) {
    const total = state.grid.clientWidth;
    const handles = state.tracks.length - 1;
    const out = sizes.slice();

    state.tracks.forEach(function (track, i) {
      if (track.flexible) return;
      const min = track.min || 120;
      out[i] = Math.max(min, out[i]);
    });

    // The flexible track must keep its own minimum; take the overflow back
    // off the side tracks, largest first, so one greedy pane cannot squeeze
    // the conversation to nothing.
    const flexIndex = state.tracks.findIndex(function (t) { return t.flexible; });
    const flexMin = state.tracks[flexIndex].min || 320;
    let used = out.reduce(function (sum, w, i) {
      return i === flexIndex ? sum : sum + w;
    }, 0) + handles * state.handleSize;

    let overflow = used + flexMin - total;
    while (overflow > 0) {
      const shrinkable = out
        .map(function (w, i) { return { i: i, w: w }; })
        .filter(function (x) {
          return x.i !== flexIndex && x.w > (state.tracks[x.i].min || 120);
        })
        .sort(function (a, b) { return b.w - a.w; });
      if (!shrinkable.length) break;
      const target = shrinkable[0];
      const room = target.w - (state.tracks[target.i].min || 120);
      const take = Math.min(room, overflow);
      out[target.i] -= take;
      overflow -= take;
    }
    return out;
  }

  function apply(state) {
    if (window.innerWidth < STACK_BELOW) {
      // Hand the layout back to the stylesheet's stacked rules.
      state.grid.style.removeProperty('grid-template-columns');
      state.handles.forEach(function (h) { h.hidden = true; });
      return;
    }
    state.handles.forEach(function (h) { h.hidden = false; });

    state.sizes = clamp(state, state.sizes);
    const parts = state.tracks.map(function (track, i) {
      return track.flexible ? 'minmax(0, 1fr)' : state.sizes[i] + 'px';
    });
    // Interleave the handles so the grid has a real track for each one; a
    // divider positioned absolutely would not move with the columns.
    const columns = [];
    parts.forEach(function (p, i) {
      if (i) columns.push(state.handleSize + 'px');
      columns.push(p);
    });
    state.grid.style.gridTemplateColumns = columns.join(' ');
  }

  // ── dragging ───────────────────────────────────────────────────
  function beginDrag(state, handleIndex, event) {
    if (window.innerWidth < STACK_BELOW) return;
    event.preventDefault();

    const startX = event.clientX;
    const before = handleIndex;          // track left of this handle
    const after = handleIndex + 1;       // track right of it
    const startSizes = state.sizes.slice();
    const flexIndex = state.tracks.findIndex(function (t) { return t.flexible; });

    state.grid.classList.add('split-dragging');

    function onMove(e) {
      const dx = e.clientX - startX;
      const next = startSizes.slice();

      // Only fixed tracks carry a width. Dragging a handle next to the
      // flexible track moves the fixed one and lets `1fr` take the remainder,
      // which is what keeps the middle column honest at any window size.
      if (before !== flexIndex) next[before] = startSizes[before] + dx;
      if (after !== flexIndex) next[after] = startSizes[after] - dx;

      state.sizes = next;
      apply(state);
    }

    function onUp() {
      state.grid.classList.remove('split-dragging');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      save(state.id, state.sizes);
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  // ── public ─────────────────────────────────────────────────────
  function make(opt) {
    const grid = typeof opt.grid === 'string'
      ? document.querySelector(opt.grid) : opt.grid;
    if (!grid) return null;

    const state = {
      id: opt.id,
      grid: grid,
      tracks: opt.tracks,
      handleSize: opt.handleSize || 6,
      handles: [],
      sizes: opt.tracks.map(function (t) { return t.size || 0; }),
    };

    const saved = load(opt.id);
    if (Array.isArray(saved) && saved.length === state.tracks.length) {
      state.sizes = saved.map(function (w, i) {
        return state.tracks[i].flexible ? 0 : Number(w) || state.tracks[i].size;
      });
    }

    // Insert a handle before every track but the first.
    const children = Array.prototype.slice.call(grid.children);
    children.forEach(function (child, i) {
      if (!i) return;
      const handle = document.createElement('div');
      handle.className = 'split-handle';
      handle.setAttribute('role', 'separator');
      handle.setAttribute('aria-orientation', 'vertical');
      handle.title = '拖动调整宽度，双击复位';
      const index = i - 1;
      handle.addEventListener('pointerdown', function (e) {
        beginDrag(state, index, e);
      });
      handle.addEventListener('dblclick', function () {
        state.sizes = state.tracks.map(function (t) { return t.size || 0; });
        apply(state);
        save(state.id, state.sizes);
      });
      grid.insertBefore(handle, child);
      state.handles.push(handle);
    });

    apply(state);
    window.addEventListener('resize', function () { apply(state); });

    panes.set(opt.id, state);
    return state;
  }

  function reset(id) {
    const state = panes.get(id);
    if (!state) return;
    clear(id);
    state.sizes = state.tracks.map(function (t) { return t.size || 0; });
    apply(state);
  }

  window.SplitPane = { make: make, reset: reset };
})();
