/* index.js — page script for index.html. */
// ── Time-rift engine ────────────────────────────────────────────
const sections = document.querySelectorAll('.rift-section');
let cur = 0, animating = false;
const indicator = document.getElementById('rift-indicator');

function buildDots() {
  sections.forEach((_, i) => {
    const d = document.createElement('div');
    d.className = 'rift-dot' + (i === 0 ? ' active' : '');
    d.addEventListener('click', () => goTo(i));
    indicator.appendChild(d);
  });
}
buildDots();

function goTo(next) {
  if (animating || next === cur || next < 0 || next >= sections.length) return;
  animating = true;
  const dir = next > cur ? 'above' : '';
  sections[cur].classList.remove('active');
  setTimeout(() => sections[cur].classList.add(dir), 0);
  sections[next].classList.add('active');
  cur = next;
  document.querySelectorAll('.rift-dot').forEach((d, i) => d.classList.toggle('active', i === cur));
  setTimeout(() => { animating = false; sections.forEach(s => s.classList.remove('above')); }, 900);
}

// The floating widgets sit on top of the sections and scroll internally.
// This page turns every wheel tick into a section change, so a wheel over
// 天启's reply used to scroll the reply *and* flip the page behind it at the
// same time — which is what made a long answer impossible to read.
const OVERLAYS = '.chat-dialog, #sprite-container, #sprite-clock, #music-player';

let lastWheel = 0;
window.addEventListener('wheel', e => {
  if (e.target instanceof Element && e.target.closest(OVERLAYS)) return;
  const now = Date.now();
  if (now - lastWheel < 900) return;
  lastWheel = now;
  goTo(e.deltaY > 0 ? cur + 1 : cur - 1);
}, { passive: true });

// Same reasoning for the other two ways in: an arrow key belongs to whatever
// field has focus, and a swipe belongs to whatever it started on.
window.addEventListener('keydown', e => {
  if (e.target instanceof Element && e.target.closest(OVERLAYS)) return;
  if (e.key === 'ArrowDown' || e.key === 'PageDown') goTo(cur + 1);
  if (e.key === 'ArrowUp'   || e.key === 'PageUp')   goTo(cur - 1);
});

let touchStartY = 0;
let touchOnOverlay = false;
window.addEventListener('touchstart', e => {
  touchStartY = e.touches[0].clientY;
  touchOnOverlay = e.target instanceof Element && !!e.target.closest(OVERLAYS);
});
window.addEventListener('touchend', e => {
  if (touchOnOverlay) return;
  const dy = touchStartY - e.changedTouches[0].clientY;
  if (Math.abs(dy) > 50) goTo(dy > 0 ? cur + 1 : cur - 1);
});
