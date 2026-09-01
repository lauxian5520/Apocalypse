/* ═══════════════════════════════════════════════════════════════
   music.js — Global floating music player
═══════════════════════════════════════════════════════════════ */
(function () {
    const STORAGE_KEY = 'mw_music_state_v1';
        let tracks = [], currentIdx = 0, audio = new Audio();
    let pendingAutoplay = false;
    let lastSaveAt = 0;

    // Build player HTML
    const playerHTML = `
        <div id="music-player" aria-label="背景音乐控制">
            <button class="mp-circle" id="mp-prev" title="上一首" aria-label="上一首">⏮</button>
            <button class="mp-circle mp-circle-main" id="mp-play" title="播放/暂停" aria-label="播放或暂停">▶</button>
            <button class="mp-circle" id="mp-next" title="下一首" aria-label="下一首">⏭</button>
        </div>
  `;
    document.body.insertAdjacentHTML('beforeend', playerHTML);

    const player = document.getElementById('music-player');
    const playBtn = document.getElementById('mp-play');

    audio.volume = 0.7;

    function getCurrentTrack() {
        return tracks[currentIdx] || null;
    }

    function saveState(force) {
        const now = Date.now();
        if (!force && now - lastSaveAt < 1200) return;
        lastSaveAt = now;
        const t = getCurrentTrack();
        const payload = {
            trackId: t?.id || null,
            currentIdx,
            currentTime: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
            volume: Number.isFinite(audio.volume) ? audio.volume : 0.7,
            paused: audio.paused,
            updatedAt: now,
        };
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
        } catch (_) {
            // ignore storage failure
        }
    }

    function loadState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    async function tryStartPlayback() {
        if (!tracks.length) return false;
        try {
            await audio.play();
            playBtn.textContent = '⏸';
            pendingAutoplay = false;
            saveState(true);
            return true;
        } catch (_) {
            playBtn.textContent = '▶';
            pendingAutoplay = true;
            return false;
        }
    }

    function bindAutoplayUnlock() {
        const unlock = async () => {
            if (!pendingAutoplay) return;
            const ok = await tryStartPlayback();
            if (!ok) return;
            window.removeEventListener('click', unlock, true);
            window.removeEventListener('keydown', unlock, true);
            window.removeEventListener('touchstart', unlock, true);
        };
        window.addEventListener('click', unlock, true);
        window.addEventListener('keydown', unlock, true);
        window.addEventListener('touchstart', unlock, true);
    }

    function loadTrack(idx, options) {
        if (!tracks.length) return;
        const { autoplay = true, resumeTime = 0 } = options || {};
        const t = tracks[idx];
        audio.src = t.url;
        player.title = `正在播放：${t.title || '未知标题'}${t.artist ? ` - ${t.artist}` : ''}`;

        audio.onloadedmetadata = () => {
            if (Number.isFinite(resumeTime) && resumeTime > 0 && resumeTime < (audio.duration || Infinity)) {
                audio.currentTime = resumeTime;
            }
        };

        if (autoplay) {
            tryStartPlayback();
        } else {
            audio.pause();
            playBtn.textContent = '▶';
            saveState(true);
        }
    }

    function playPause() {
        if (!tracks.length) return;
        if (audio.paused) {
            tryStartPlayback();
        } else {
            audio.pause(); playBtn.textContent = '▶';
            saveState(true);
        }
    }

    audio.addEventListener('timeupdate', () => {
        saveState(false);
    });
    audio.addEventListener('ended', () => {
        currentIdx = (currentIdx + 1) % tracks.length;
        loadTrack(currentIdx, { autoplay: true, resumeTime: 0 });
    });
    audio.addEventListener('pause', () => saveState(true));
    audio.addEventListener('play', () => saveState(true));

    playBtn.addEventListener('click', playPause);
    document.getElementById('mp-prev').addEventListener('click', () => {
        currentIdx = (currentIdx - 1 + tracks.length) % tracks.length;
        loadTrack(currentIdx, { autoplay: true, resumeTime: 0 });
    });
    document.getElementById('mp-next').addEventListener('click', () => {
        currentIdx = (currentIdx + 1) % tracks.length;
        loadTrack(currentIdx, { autoplay: true, resumeTime: 0 });
    });
    // Fetch tracks from API
    async function fetchTracks() {
        try {
            const data = await fetch('/api/music').then(r => r.json());
            tracks = data;
            if (tracks.length) {
                const saved = loadState();
                if (saved && Number.isFinite(saved.volume)) {
                    audio.volume = Math.min(1, Math.max(0, Number(saved.volume)));
                }

                if (saved?.trackId) {
                    const idxById = tracks.findIndex(t => t.id === saved.trackId);
                    if (idxById >= 0) currentIdx = idxById;
                } else if (Number.isFinite(saved?.currentIdx) && tracks[saved.currentIdx]) {
                    currentIdx = saved.currentIdx;
                }

                const shouldPlay = saved ? !saved.paused : true;
                const resumeTime = saved && currentIdx >= 0 ? Number(saved.currentTime || 0) : 0;
                loadTrack(currentIdx, { autoplay: shouldPlay, resumeTime });

                if (!shouldPlay) {
                    audio.pause();
                    playBtn.textContent = '▶';
                }
            } else {
                player.style.display = 'none'; // Hide if no tracks
            }
        } catch {
            player.style.display = 'none';
        }
    }

    bindAutoplayUnlock();
    window.addEventListener('beforeunload', () => saveState(true));
    document.addEventListener('DOMContentLoaded', fetchTracks);
})();
