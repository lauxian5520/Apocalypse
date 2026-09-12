/* ═══════════════════════════════════════════════════════════════
   split-pane.js — drag the dividers of a CSS grid, and remember where.

   The Harness workbench is three columns: session list, conversation,
   inspector. Their widths used to be hard-coded, so reading a long tool
   result meant squinting at a 380px column while the conversation had room
   to spare — and there was nothing the user could do about it.

   Deliberately generic, like floating-panel.js: it knows nothing about
   harness sessions. A page hands it a grid element and describes the tracks;
   this decides nothing about what they contain. Works on either axis — the
   workbench uses it horizontally for the three columns and vertically to let
   the conversation take room back from the composer.

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
  // Track kinds:
  //   { size, min }   fixed, draggable, width/height persisted
  //   { flexible }    takes the remainder as `1fr`; never persisted
  //   { auto }        sized by its own content; no handle beside it
  const AXIS = {
    col: { prop: 'gridTemplateColumns', client: 'clientWidth', point: 'clientX',
           cursor: 'col-resize', cls: 'split-handle', dir: 'vertical' },
    row: { prop: 'gridTemplateRows', client: 'clientHeight', point: 'clientY',
           cursor: 'row-resize', cls: 'split-handle-row', dir: 'horizontal' },
  };
  // Matches the `@media (max-width: 1200px)` rule in css/pages/harness.css,
  // where the grid stops being three columns. Kept in sync by hand; there is
  // no way to read a media query's breakpoint back out of CSS.
  const STACK_BELOW = 1200;

  const panes_registry = new Map();

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
    const total = state.grid[state.axis.client] - autoExtent(state);
    const handles = state.boundaries.length;
    const out = sizes.slice();

    state.tracks.forEach(function (track, i) {
      if (track.flexible || track.auto) return;
      out[i] = Math.max(track.min || 120, out[i]);
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
          return x.i !== flexIndex && !state.tracks[x.i].auto
            && x.w > (state.tracks[x.i].min || 120);
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

  // Auto tracks are measured, not configured: the head's height depends on how
  // the title wraps, which is not knowable from CSS.
  function autoExtent(state) {
    let total = 0;
    state.tracks.forEach(function (track, i) {
      if (!track.auto) return;
      const el = state.panes[i];
      if (el) total += state.axis === AXIS.row ? el.offsetHeight : el.offsetWidth;
    });
    return total;
  }

  function apply(state) {
    if (state.axis === AXIS.col && window.innerWidth < STACK_BELOW) {
      // Hand the layout back to the stylesheet's stacked rules.
      state.grid.style.removeProperty('grid-template-columns');
      state.handles.forEach(function (h) { h.hidden = true; });
      return;
    }
    state.handles.forEach(function (h) { h.hidden = false; });

    state.sizes = clamp(state, state.sizes);
    const parts = state.tracks.map(function (track, i) {
      if (track.auto) return 'auto';
      return track.flexible ? 'minmax(0, 1fr)' : state.sizes[i] + 'px';
    });
    // Interleave the handles so the grid has a real track for each one; a
    // divider positioned absolutely would not move with the panes.
    const out = [];
    parts.forEach(function (p, i) {
      if (i && state.boundaries.indexOf(i - 1) >= 0) out.push(state.handleSize + 'px');
      out.push(p);
    });
    state.grid.style[state.axis.prop] = out.join(' ');
  }

  // ── dragging ───────────────────────────────────────────────────
  function beginDrag(state, handleIndex, event) {
    if (state.axis === AXIS.col && window.innerWidth < STACK_BELOW) return;
    event.preventDefault();

    const startX = event[state.axis.point];
    const before = handleIndex;          // track left of this handle
    const after = handleIndex + 1;       // track right of it
    const startSizes = state.sizes.slice();
    const flexIndex = state.tracks.findIndex(function (t) { return t.flexible; });

    state.grid.classList.add('split-dragging');

    function onMove(e) {
      const dx = e[state.axis.point] - startX;
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

    const axis = AXIS[opt.axis === 'row' ? 'row' : 'col'];
    const panes = Array.prototype.slice.call(grid.children);

    const state = {
      id: opt.id,
      grid: grid,
      axis: axis,
      tracks: opt.tracks,
      panes: panes,
      handleSize: opt.handleSize || 6,
      handles: [],
      // A boundary gets a handle only when both sides are resizable. An `auto`
      // track is sized by its own content, so a divider next to one would
      // promise a drag that cannot do anything.
      boundaries: [],
      sizes: opt.tracks.map(function (t) { return t.size || 0; }),
    };

    state.tracks.forEach(function (track, i) {
      if (i === 0) return;
      if (track.auto || state.tracks[i - 1].auto) return;
      state.boundaries.push(i - 1);
    });

    const saved = load(opt.id);
    if (Array.isArray(saved) && saved.length === state.tracks.length) {
      state.sizes = saved.map(function (w, i) {
        const track = state.tracks[i];
        return (track.flexible || track.auto) ? 0 : (Number(w) || track.size);
      });
    }

    state.boundaries.forEach(function (boundary) {
      const handle = document.createElement('div');
      handle.className = axis.cls;
      handle.setAttribute('role', 'separator');
      handle.setAttribute('aria-orientation', axis.dir);
      handle.title = '拖动调整大小，双击复位';
      handle.addEventListener('pointerdown', function (e) {
        beginDrag(state, boundary, e);
      });
      handle.addEventListener('dblclick', function () {
        state.sizes = state.tracks.map(function (t) { return t.size || 0; });
        apply(state);
        save(state.id, state.sizes);
      });
      // Insert before the pane on the far side of this boundary.
      grid.insertBefore(handle, panes[boundary + 1]);
      state.handles.push(handle);
    });

    apply(state);
    window.addEventListener('resize', function () { apply(state); });

    panes_registry.set(opt.id, state);
    return state;
  }

  function reset(id) {
    const state = panes_registry.get(id);
    if (!state) return;
    clear(id);
    state.sizes = state.tracks.map(function (t) { return t.size || 0; });
    apply(state);
  }

  window.SplitPane = { make: make, reset: reset };
})();
