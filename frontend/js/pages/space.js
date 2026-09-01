/* space.js — page script for space.html. */
document.querySelectorAll('.copy-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
        const v = btn.getAttribute('data-copy') || '';
        if (!v) return;
        try {
            await navigator.clipboard.writeText(v);
            toast('已复制: ' + v, 'success');
        } catch (_) {
            const ta = document.createElement('textarea');
            ta.value = v;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
            toast('已复制: ' + v, 'success');
        }
    });
});

const cards = document.querySelectorAll('.contact-card');
cards.forEach((card) => {
    card.addEventListener('mousemove', (e) => {
        const r = card.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width;
        const py = (e.clientY - r.top) / r.height;
        const ry = (px - 0.5) * 6;
        const rx = (0.5 - py) * 4;
        card.style.transform = `translateY(-4px) rotateX(${rx}deg) rotateY(${ry}deg)`;
    });
    card.addEventListener('mouseleave', () => {
        card.style.transform = '';
    });
});
