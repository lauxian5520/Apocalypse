/* harness-stream.js — SSE client for the harness event stream.

   apiFetch() only returns JSON and would swallow the body, so streaming
   endpoints use a bare fetch, exactly as sprite-chat.js does. The frame format
   is fixed by backend/core/sse.py: "data: " with a space, one JSON object per
   line, terminated by "data: [DONE]". */

(function () {
    const API = '/api';

    /** POST to `path` and invoke `onEvent` for each event the server emits.
     *  Resolves when the stream closes; rejects only if it never opened. */
    async function streamEvents(path, body, { onEvent, onError }) {
        const res = await fetch(API + path, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': window.Auth?.csrfToken?.() || '',
            },
            credentials: 'include',
            body: JSON.stringify(body || {}),
        });

        // Before the stream opens the server can still answer normally, so a
        // failure here carries a readable {"detail": ...} body.
        if (!res.ok) {
            let detail = '';
            try {
                const err = await res.json();
                detail = err?.detail || err?.error || '';
            } catch (_) { detail = ''; }
            throw new Error(detail || `HTTP ${res.status} ${res.statusText}`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error('当前浏览器不支持流式响应');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const lines = buffer.split('\n');
            // The last piece may be a partial line; hold it for the next read.
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6);
                if (payload === '[DONE]') continue;

                let obj;
                try {
                    obj = JSON.parse(payload);
                } catch (_) {
                    continue;  /* ignore malformed SSE chunks */
                }
                // Once headers are flushed the server reports failures in-band.
                if (obj.error) onError?.(obj.error);
                else onEvent?.(obj);
            }
        }
    }

    window.HarnessStream = { streamEvents };
})();
