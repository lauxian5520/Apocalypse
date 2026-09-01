/* ═══════════════════════════════════════════════════════════════
   auth.js — Client-side session state.

   The JWT itself is an HttpOnly cookie the page can never read; this
   object only caches "who am I", resolved once from /api/auth/me.
═══════════════════════════════════════════════════════════════ */
const CSRF_COOKIE_NAME = 'mw_csrf';

const Auth = {
  _user: null,
  _initPromise: null,

  /** Truthy once the current user is known. Not the real credential. */
  token() {
    return this._user ? '__cookie__' : '';
  },

  user() {
    return this._user;
  },

  set(_token, user) {
    this._user = user || null;
  },

  isAdmin() {
    return (this._user || {}).role === 'admin';
  },

  /** Read the double-submit CSRF value the server set alongside the JWT. */
  csrfToken() {
    const parts = (document.cookie || '').split(';').map(s => s.trim());
    const hit = parts.find(s => s.startsWith(CSRF_COOKIE_NAME + '='));
    return hit ? decodeURIComponent(hit.slice(CSRF_COOKIE_NAME.length + 1)) : '';
  },

  /** Resolve the current user. Cached — concurrent callers share one request. */
  async init(force = false) {
    if (!force && this._initPromise) return this._initPromise;
    this._initPromise = (async () => {
      try {
        const res = await fetch(API + '/auth/me', { credentials: 'include' });
        this._user = res.ok ? await res.json() : null;
      } catch (_) {
        this._user = null;
      }
      return this._user;
    })();
    return this._initPromise;
  },

  async logout() {
    try {
      await fetch(API + '/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': this.csrfToken() },
      });
    } catch (_) {
      // Ignore network errors: local state is cleared either way.
    }
    this.clear();
  },

  clear() {
    this._user = null;
    this._initPromise = null;
  },

  /** Guard for pages that make no sense logged out. */
  async requireLogin() {
    if (await this.init()) return true;
    toast('请先登录', 'error');
    window.location.href = 'login.html';
    return false;
  },
};

window.Auth = Auth;
