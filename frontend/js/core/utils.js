/* ═══════════════════════════════════════════════════════════════
   utils.js — Pure helpers shared by every page.
   No DOM wiring, no network. Load first.
═══════════════════════════════════════════════════════════════ */

/**
 * Escape text for insertion into HTML — including attribute values.
 * Quotes matter: these strings also land inside alt="…" / title="…",
 * where an unescaped " breaks out of the attribute.
 */
function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Parse a timestamp coming from the API.
 * SQLite stores UTC but serialises without a timezone, so a bare
 * "2026-01-01 12:00:00" would otherwise be read as local time.
 */
function parseServerDate(value) {
  const str = String(value || '').trim();
  if (!str) return new Date(NaN);
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(str)) {
    return new Date(str.replace(' ', 'T') + 'Z');
  }
  return new Date(str);
}

/** Relative time for feeds ("3 分钟前"), absolute date once it is a day old. */
function formatTime(value) {
  const d = parseServerDate(value);
  if (Number.isNaN(d.getTime())) return '--';
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  return d.toLocaleDateString('zh-CN');
}

/** Full local date-time, for profiles and detail views. */
function formatDateTime(value) {
  const d = parseServerDate(value);
  if (Number.isNaN(d.getTime())) return '--';
  return d.toLocaleString('zh-CN');
}

/** Placeholder cards shown while a list is loading. */
function makeSkeleton(count = 6, height = '180px') {
  return Array.from(
    { length: count },
    () => `<div class="skeleton" style="height:${height};border-radius:12px"></div>`
  ).join('');
}

/** Transient bottom-corner notification. */
function toast(msg, type = 'info') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

window.escHtml = escHtml;
window.parseServerDate = parseServerDate;
window.formatTime = formatTime;
window.formatDateTime = formatDateTime;
window.makeSkeleton = makeSkeleton;
window.toast = toast;
