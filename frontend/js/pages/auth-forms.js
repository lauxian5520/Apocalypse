/* ═══════════════════════════════════════════════════════════════
   auth-forms.js — Login and registration form handling.
   (Session state itself lives in js/core/auth.js.)
═══════════════════════════════════════════════════════════════ */

// ── Register Page ────────────────────────────────────────────────
async function initRegister() {
    const form = document.getElementById('register-form');
    if (!form) return;

    let captchaToken = '';

    async function loadCaptcha() {
        const data = await apiFetch('/auth/captcha');
        document.getElementById('captcha-img').src = data.image;
        captchaToken = data.token;
    }
    await loadCaptcha();
    document.getElementById('captcha-refresh').addEventListener('click', loadCaptcha);

    form.addEventListener('submit', async e => {
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        btn.disabled = true; btn.textContent = '注册中…';
        try {
            const fd = new FormData();
            fd.append('username', form.username.value);
            fd.append('email', form.email.value);
            fd.append('password', form.password.value);
            fd.append('captcha', form.captcha.value.toUpperCase());
            fd.append('captcha_token', captchaToken);
            const avatar = form.querySelector('#register-avatar')?.files?.[0];
            if (avatar) fd.append('avatar', avatar);

            await apiFetch('/auth/register', {
                method: 'POST',
                body: fd,
            });
            toast('注册成功！正在跳转登录…', 'success');
            setTimeout(() => window.location.href = 'login.html', 1200);
        } catch (err) {
            toast(err.message, 'error');
            loadCaptcha(); // Refresh captcha on error
        } finally {
            btn.disabled = false; btn.textContent = '注 册';
        }
    });
}

// ── Login Page ───────────────────────────────────────────────────
async function initLogin() {
    const form = document.getElementById('login-form');
    if (!form) return;

    form.addEventListener('submit', async e => {
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        btn.disabled = true; btn.textContent = '登录中…';
        try {
            const data = await apiFetch('/auth/login', {
                method: 'POST',
                body: JSON.stringify({ username: form.username.value, password: form.password.value }),
            });
            Auth.set(null, data.user);
            toast('欢迎回来，' + data.user.username + '！', 'success');
            setTimeout(() => window.location.href = 'memos.html', 800);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            btn.disabled = false; btn.textContent = '登 录';
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initRegister();
    initLogin();
});
