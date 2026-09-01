/* ═══════════════════════════════════════════════════════════════
   api.js — The one place that talks to the backend.

   Handles the cookie session, the CSRF header on writes, and turns
   error responses into Errors carrying the server's message.
═══════════════════════════════════════════════════════════════ */
const API = '/api';
const SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const method = (options.method || 'GET').toUpperCase();

  // FormData must keep the browser-generated multipart boundary.
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  if (!SAFE_METHODS.includes(method)) {
    const csrf = Auth.csrfToken();
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }

  let res;
  try {
    res = await fetch(API + path, { ...options, headers, credentials: 'include' });
  } catch (err) {
    throw new Error(`Network error (${API + path}): ${err.message}`);
  }

  if (!res.ok) {
    if (res.status === 401) Auth.clear();
    throw new Error(await describeError(res, path));
  }

  if (res.status === 204) return null;
  const type = (res.headers.get('content-type') || '').toLowerCase();
  return type.includes('application/json') ? res.json() : null;
}

/** Build a message that names the endpoint and the server's own explanation. */
async function describeError(res, path) {
  const status = `HTTP ${res.status} ${res.statusText}`;
  const type = (res.headers.get('content-type') || '').toLowerCase();

  if (type.includes('application/json')) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const msg = Array.isArray(detail)
      ? detail.map(e => e.msg || e.message || JSON.stringify(e)).join('；')
      : (detail || body.message || 'Request failed');
    return `${status} (${API + path}): ${msg}`;
  }

  const raw = (await res.text().catch(() => '')).trim();
  const preview = raw ? raw.slice(0, 180).replace(/\s+/g, ' ') : 'empty response body';
  return `${status} (${API + path}): non-JSON response -> ${preview}`;
}

window.API = API;
window.apiFetch = apiFetch;
