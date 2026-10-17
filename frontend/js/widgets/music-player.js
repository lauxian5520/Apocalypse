/* ═══════════════════════════════════════════════════════════════
   music.js — Global floating music player
═══════════════════════════════════════════════════════════════ */
(function () {
    const STORAGE_KEY = 'mw_music_state_v1';
    let tracks = [], currentIdx = 0, audio = new Audio();
    let fetchingTracks = false;
    let pendingAutoplay = false;
    let playbackAttempt = 0;
    let lastSaveAt = 0;

    // Build player HTML
    const playerHTML = `
        <div id="music-player" aria-label="背景音乐控制">
            <button class="mp-circle" id="mp-prev" title="上一首" aria-label="上一首">⏮</button>
            <button class="mp-circle mp-circle-main" id="mp-play" title="播放/暂停" aria-label="播放或暂停">▶</button>
            <button class="mp-circle" id="mp-next" title="下一首" aria-label="下一首">⏭</button>
            <button class="mp-circle mp-vol" id="mp-mute" title="静音" aria-label="静音">🔊</button>
            <input type="range" id="mp-volume" class="mp-vol" min="0" max="100" value="70" step="1" title="音量" aria-label="音量">
            <span id="mp-status" role="status" aria-live="polite">音乐加载中</span>
            <a id="mp-library" href="admin.html" hidden>添加音乐</a>
            <button type="button" class="mp-circle mp-vol" id="mp-retry" title="刷新曲库" aria-label="刷新曲库">↻</button>
        </div>
  `;
    document.body.insertAdjacentHTML('beforeend', playerHTML);

    const player = document.getElementById('music-player');
    const playBtn = document.getElementById('mp-play');
    const muteBtn = document.getElementById('mp-mute');
    const volSlider = document.getElementById('mp-volume');
    const status = document.getElementById('mp-status');
    const retryBtn = document.getElementById('mp-retry');
    const libraryLink = document.getElementById('mp-library');

    function syncAvailability() {
        const empty = tracks.length === 0;
        player.dataset.empty = String(empty);
        ['mp-prev', 'mp-play', 'mp-next', 'mp-mute', 'mp-volume'].forEach(id => {
            document.getElementById(id).disabled = empty;
        });
        libraryLink.hidden = !empty || !window.Auth?.isAdmin();
    }
    syncAvailability();

    let lastVolume = 0.7;   // non-zero level to restore after unmuting

    function syncVolumeUI() {
        const pct = Math.round(audio.volume * 100);
        volSlider.value = String(pct);
        volSlider.style.setProperty('--mp-fill', `${pct}%`);
        volSlider.title = `音量 ${pct}%`;
        const muted = audio.volume === 0;
        muteBtn.textContent = muted ? '🔇' : '🔊';
        muteBtn.title = muted ? '取消静音' : '静音';
        muteBtn.setAttribute('aria-label', muteBtn.title);
    }

    function setVolume(v) {
        const clamped = Math.min(1, Math.max(0, v));
        audio.volume = clamped;
        if (clamped > 0) lastVolume = clamped;
        syncVolumeUI();
        saveState(true);
    }

    audio.volume = 0.7;
    syncVolumeUI();

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
            lastVolume: Number.isFinite(lastVolume) && lastVolume > 0 ? lastVolume : 0.7,
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
        const attempt = ++playbackAttempt;
        pendingAutoplay = false;
        try {
            await audio.play();
            if (attempt !== playbackAttempt) return false;
            playBtn.textContent = '⏸';
            status.hidden = true;
            saveState(true);
            return true;
        } catch (error) {
            if (attempt !== playbackAttempt) return false;
            playBtn.textContent = '▶';
            pendingAutoplay = error.name === 'NotAllowedError';
            if (pendingAutoplay) {
                status.textContent = '点击播放音乐';
                status.hidden = false;
            } else if (error.name !== 'AbortError') {
                status.textContent = '音乐播放失败，请重试或切换曲目';
                status.hidden = false;
            }
            return false;
        }
    }

    function bindAutoplayUnlock() {
        const unlock = (event) => {
            // The player's own controls handle their gesture. Starting here
            // first makes the play button immediately pause that same audio.
            if (!pendingAutoplay || player.contains(event.target)) return;
            tryStartPlayback();
        };
        window.addEventListener('click', unlock, true);
        window.addEventListener('keydown', unlock, true);
    }

    function pausePlayback() {
        ++playbackAttempt;
        pendingAutoplay = false;
        audio.pause();
        playBtn.textContent = '▶';
        saveState(true);
    }

    function loadTrack(idx, options) {
        if (!tracks.length) return;
        const { autoplay = true, resumeTime = 0 } = options || {};
        const t = tracks[idx];
        ++playbackAttempt;
        pendingAutoplay = false;
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
            pausePlayback();
        }
    }

    function playPause() {
        if (!tracks.length) return;
        if (audio.paused) {
            tryStartPlayback();
        } else {
            pausePlayback();
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
    audio.addEventListener('error', () => {
        ++playbackAttempt;
        pendingAutoplay = false;
        playBtn.textContent = '▶';
        status.textContent = '音乐播放失败，请重试或切换曲目';
        status.hidden = false;
    });

    playBtn.addEventListener('click', playPause);
    document.getElementById('mp-prev').addEventListener('click', () => {
        if (!tracks.length) return;
        currentIdx = (currentIdx - 1 + tracks.length) % tracks.length;
        loadTrack(currentIdx, { autoplay: true, resumeTime: 0 });
    });
    document.getElementById('mp-next').addEventListener('click', () => {
        if (!tracks.length) return;
        currentIdx = (currentIdx + 1) % tracks.length;
        loadTrack(currentIdx, { autoplay: true, resumeTime: 0 });
    });
    muteBtn.addEventListener('click', () => {
        setVolume(audio.volume > 0 ? 0 : (lastVolume || 0.7));
    });
    volSlider.addEventListener('input', () => {
        setVolume(Number(volSlider.value) / 100);
    });
    // Fetch tracks from API
    async function fetchTracks() {
        if (fetchingTracks) return;
        fetchingTracks = true;
        retryBtn.disabled = true;
        try {
            await window.Auth?.init();
            const response = await fetch('/api/music');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!Array.isArray(data)) throw new Error('曲库数据格式错误');
            tracks = data;
            syncAvailability();
            status.hidden = tracks.length > 0;
            if (tracks.length) {
                currentIdx = Math.min(currentIdx, tracks.length - 1);
                const saved = loadState();
                if (saved) {
                    if (Number.isFinite(saved.lastVolume) && saved.lastVolume > 0) {
                        lastVolume = saved.lastVolume;
                    }
                    const vol = Number.isFinite(saved.volume)
                        ? Math.min(1, Math.max(0, Number(saved.volume)))
                        : 0.7;
                    audio.volume = vol;
                    syncVolumeUI();
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
                pausePlayback();
                currentIdx = 0;
                status.textContent = '暂无音乐';
                player.title = '暂无音乐，可由管理员在后台上传曲目';
            }
        } catch {
            syncAvailability();
            status.hidden = false;
            status.textContent = '音乐加载失败';
            player.title = '曲库暂时无法加载，请点击刷新曲库重试';
        } finally {
            fetchingTracks = false;
            retryBtn.disabled = false;
        }
    }
    retryBtn.addEventListener('click', fetchTracks);
    window.addEventListener('focus', () => {
        if (!tracks.length) fetchTracks();
    });

    /** Drag to move, corner to scale. The player sits over the top-right of
     *  every page and used to cover whatever was underneath it.
     *
     *  Scaling is one font-size: music-player.css sizes the buttons in `em`,
     *  so the pill and its three circles grow together. The drag threshold in
     *  floating-panel.js is what keeps the buttons clickable — a press that
     *  never travels 4px is still a click. */
    function makeDraggable() {
        if (!window.FloatingPanel) return;
        const baseWidth = player.getBoundingClientRect().width || 132;
        window.FloatingPanel.make(player, {
            id: 'music',
            autoHeight: true,
            minWidth: 88,
            maxWidth: 340,
            ignore: '.mp-vol',
            onResize: (w) => {
                player.style.fontSize = `${(w / baseWidth).toFixed(3)}rem`;
            },
        });
    }

    bindAutoplayUnlock();
    window.addEventListener('beforeunload', () => saveState(true));
    document.addEventListener('DOMContentLoaded', () => {
        fetchTracks();
        makeDraggable();
    });
})();
