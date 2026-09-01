/* register.js — page script for register.html. */
document.getElementById('captcha-img')?.addEventListener('click', () =>
    document.getElementById('captcha-refresh')?.click()
);
document.getElementById('register-avatar')?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    const preview = document.getElementById('register-avatar-preview');
    if (!preview) return;
    if (!f) {
        preview.src = 'assets/images/default-avatar.svg';
        return;
    }
    preview.src = URL.createObjectURL(f);
});
