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

let lastWheel = 0;
window.addEventListener('wheel', e => {
  const now = Date.now();
  if (now - lastWheel < 900) return;
  lastWheel = now;
  goTo(e.deltaY > 0 ? cur + 1 : cur - 1);
}, { passive: true });

window.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' || e.key === 'PageDown') goTo(cur + 1);
  if (e.key === 'ArrowUp'   || e.key === 'PageUp')   goTo(cur - 1);
});

let touchStartY = 0;
window.addEventListener('touchstart', e => { touchStartY = e.touches[0].clientY; });
window.addEventListener('touchend', e => {
  const dy = touchStartY - e.changedTouches[0].clientY;
  if (Math.abs(dy) > 50) goTo(dy > 0 ? cur + 1 : cur - 1);
});
