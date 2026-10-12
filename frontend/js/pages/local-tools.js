/* Browser-only utilities. Each modal owns its state and resource cleanup. */
(() => {
    'use strict';
    const hint = '<p class="utility-hint">无需登录 · 仅在浏览器内处理 · 关闭后不保存输入</p>';
    const status = '<p class="utility-status" role="status" aria-live="polite"></p>';
    const shell = (title, body) => `<section class="local-tool"><h3>${title}</h3>${hint}${body}${status}</section>`;
    const tools = [
        { id: 'text', icon: '📋', name: '文本处理与统计', desc: '字符与单词统计、清理空行、逐行去重', modal: shell('文本处理与统计', `
          <div class="utility-panels">
            <label>原文<textarea id="text-input" class="textarea" rows="9" placeholder="输入文本…"></textarea></label>
            <label>处理结果<textarea id="text-output" class="textarea" rows="9" readonly placeholder="点击处理文本后查看结果"></textarea></label>
          </div>
          <dl id="text-stats" class="utility-stats" aria-live="polite"></dl>
          <div class="utility-options">
            <label><input id="text-empty" type="checkbox" checked> 去除空行</label>
            <label><input id="text-trim" type="checkbox" checked> 清理行首尾空格</label>
            <label><input id="text-unique" type="checkbox"> 逐行去重（保留首次出现）</label>
          </div>
          <div class="utility-actions"><button id="text-run" class="btn btn-primary">处理文本</button><button id="text-copy" class="btn" disabled>复制结果</button></div>`) },
        { id: 'timestamp', icon: '🕰️', name: '时间戳转换', desc: 'Unix 秒／毫秒时间戳、本地时间与 UTC 互转', modal: shell('时间戳转换', `
          <form id="timestamp-form">
            <div class="utility-fields"><label>Unix 时间戳<input id="timestamp-input" class="input" inputmode="numeric" placeholder="例如 1700000000" required></label>
            <label>单位<select id="timestamp-unit"><option value="seconds">秒</option><option value="milliseconds">毫秒</option></select></label></div>
            <div class="utility-actions"><button class="btn btn-primary" type="submit">时间戳 → 日期时间</button><button id="timestamp-now" class="btn" type="button">填入当前时间</button></div>
          </form>
          <form id="datetime-form"><label class="utility-field">本地日期时间<input id="datetime-input" class="input" type="datetime-local" step="0.001" required></label>
            <div class="utility-actions"><button class="btn btn-primary" type="submit">日期时间 → 时间戳</button></div></form>
          <label class="utility-field">转换结果<textarea id="timestamp-output" class="textarea" rows="4" readonly placeholder="本地时间、UTC、秒和毫秒将在这里显示"></textarea></label>
          <div class="utility-actions"><button id="timestamp-copy" class="btn" disabled>复制结果</button></div>`) },
        { id: 'encoding', icon: '🔄', name: '编码转换', desc: 'UTF-8 Base64 编解码、URL 编解码', modal: shell('编码转换', `
          <label class="utility-field">转换类型<select id="encoding-type"><option value="base64-encode">Base64 编码（UTF-8）</option><option value="base64-decode">Base64 解码（UTF-8）</option><option value="url-encode">URL 编码（encodeURIComponent）</option><option value="url-decode">URL 解码（decodeURIComponent）</option></select></label>
          <div class="utility-panels"><label>输入<textarea id="encoding-input" class="textarea" rows="9" placeholder="输入待转换内容…"></textarea></label><label>结果<textarea id="encoding-output" class="textarea" rows="9" readonly placeholder="选择类型后点击转换"></textarea></label></div>
          <div class="utility-actions"><button id="encoding-run" class="btn btn-primary">转换</button><button id="encoding-copy" class="btn" disabled>复制结果</button></div>`) },
        { id: 'image', icon: '🖼️', name: '图片压缩与格式转换', desc: 'JPEG / PNG / WebP 压缩、缩放与下载', modal: shell('图片压缩与格式转换', `
          <label id="image-drop" class="image-drop">点击选择或拖入图片<input id="image-file" type="file" accept="image/jpeg,image/png,image/webp"><span>JPEG、PNG、WebP · 单文件最多 20 MB</span></label>
          <div class="utility-fields image-settings">
            <label>输出格式<select id="image-format"><option value="image/webp">WebP</option><option value="image/jpeg">JPEG（透明区域填白）</option><option value="image/png">PNG（无损）</option></select></label>
            <label>质量：<span id="image-quality-label">80%</span><input id="image-quality" type="range" min="1" max="100" value="80"></label>
            <label>最长边（像素）<input id="image-max" class="input" type="number" min="1" max="10000" step="1" value="1920"></label>
          </div>
          <p class="utility-hint">保持比例且不放大。PNG 不使用质量参数；转换后的文件可能比原图更大。</p>
          <div class="utility-actions"><button id="image-run" class="btn btn-primary" disabled>压缩 / 转换</button><a id="image-download" class="btn" hidden>下载结果</a></div>
          <div class="utility-panels image-previews"><figure><figcaption>原图 <span id="image-original-info">尚未选择图片</span></figcaption><img id="image-original" alt="原图预览" hidden></figure><figure><figcaption>结果 <span id="image-result-info">尚未转换</span></figcaption><img id="image-result" alt="转换结果预览" hidden></figure></div>`) },
        { id: 'diff', icon: '🧾', name: '文本差异对比', desc: '按行比较新增、删除、未修改内容（每侧最多 500 行）', modal: shell('文本差异对比', `
          <div class="utility-panels"><label>原文本<textarea id="diff-before" class="textarea" rows="9" placeholder="输入原文本…"></textarea></label><label>新文本<textarea id="diff-after" class="textarea" rows="9" placeholder="输入新文本…"></textarea></label></div>
          <div class="utility-actions"><button id="diff-run" class="btn btn-primary">对比文本</button></div>
          <p id="diff-count" class="utility-hint">每侧最多 500 行；空白及行尾换行也参与对比。</p>
          <div id="diff-output" class="diff-output" aria-label="逐行差异结果"></div>`) },
    ];

    const lines = text => text === '' ? [] : text.replace(/\r\n?/g, '\n').split('\n');
    function textStats(text) {
        return [
            ['字符数', Array.from(text).length],
            ['非空白字符', Array.from(text.replace(/\s/g, '')).length],
            ['中文字符', (text.match(/\p{Script=Han}/gu) || []).length],
            ['英文单词', (text.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g) || []).length],
            ['行数', lines(text).length],
        ];
    }
    function processText(text, options) {
        let result = lines(text);
        if (options.trim) result = result.map(line => line.trim());
        if (options.empty) result = result.filter(line => line.trim() !== '');
        if (options.unique) result = [...new Set(result)];
        return result.join('\n');
    }
    function convertEncoding(text, type) {
        if (type === 'url-encode') return encodeURIComponent(text);
        if (type === 'url-decode') return decodeURIComponent(text);
        if (type === 'base64-encode') {
            const bytes = new TextEncoder().encode(text);
            let binary = '';
            for (const byte of bytes) binary += String.fromCharCode(byte);
            return btoa(binary);
        }
        const encoded = text.replace(/\s/g, '');
        // Accept standard padded or unpadded Base64; reject misplaced padding.
        if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}(?:==)?|[A-Za-z0-9+/]{3}=?)?$/.test(encoded)) {
            throw new Error('Base64 格式无效：请检查字符、长度及末尾的 = 填充。');
        }
        const binary = atob(encoded);
        if (btoa(binary).replace(/=+$/, '') !== encoded.replace(/=+$/, '')) throw new Error('Base64 格式无效：末尾填充位不正确。');
        try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Uint8Array.from(binary, char => char.charCodeAt(0))); }
        catch (_) { throw new Error('Base64 内容不是有效的 UTF-8 文本。'); }
    }
    function diffLines(before, after) {
        const a = lines(before), b = lines(after);
        if (a.length > 500 || b.length > 500) throw new Error('每侧最多支持 500 行，请缩短文本后重试。');
        const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
        for (let i = a.length - 1; i >= 0; i--) {
            for (let j = b.length - 1; j >= 0; j--) {
                table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
            }
        }
        const result = [];
        let i = 0, j = 0;
        while (i < a.length || j < b.length) {
            if (i < a.length && j < b.length && a[i] === b[j]) result.push({ type: 'same', text: a[i++], oldLine: i, newLine: ++j });
            else if (i < a.length && (j === b.length || table[i + 1][j] >= table[i][j + 1])) result.push({ type: 'removed', text: a[i++], oldLine: i, newLine: '' });
            else result.push({ type: 'added', text: b[j++], oldLine: '', newLine: j });
        }
        return result;
    }
    const pad = (n, size = 2) => String(n).padStart(size, '0');
    function localDate(date) {
        const year = date.getFullYear();
        const yearText = year < 0 ? `-${pad(-year, 6)}` : year > 9999 ? `+${pad(year, 6)}` : pad(year, 4);
        return `${yearText}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
    }
    function parseTimestamp(text, unit) {
        if (!/^[+-]?\d+$/.test(text.trim())) throw new Error('请输入整数时间戳，并选择正确的秒或毫秒单位。');
        const value = Number(text);
        const ms = unit === 'seconds' ? value * 1000 : value;
        if (!Number.isSafeInteger(ms) || !Number.isFinite(new Date(ms).getTime())) throw new Error('时间戳超出支持的日期范围。');
        return new Date(ms);
    }

    function init(id, root) {
        const $ = selector => root.querySelector(selector);
        if (!$('.local-tool')) return () => {};
        const statusNode = $('.utility-status');
        const message = (text, error = false) => {
            statusNode.textContent = text;
            statusNode.classList.toggle('is-error', error);
        };
        const copy = (button, output) => {
            const outputNode = $(output);
            $(button).addEventListener('click', async () => {
                try { await navigator.clipboard.writeText(outputNode.value); message('结果已复制。'); }
                catch (_) { outputNode.focus(); outputNode.select(); message('无法自动复制，请选择结果手动复制。', true); }
            });
        };
        if (id === 'text') {
            const updateStats = () => {
                const fragment = document.createDocumentFragment();
                for (const [name, count] of textStats($('#text-input').value)) {
                    const item = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
                    dt.textContent = name; dd.textContent = count; item.append(dt, dd); fragment.append(item);
                }
                $('#text-stats').replaceChildren(fragment);
            };
            $('#text-input').addEventListener('input', updateStats);
            updateStats();
            $('#text-run').addEventListener('click', () => {
                $('#text-output').value = processText($('#text-input').value, { trim: $('#text-trim').checked, empty: $('#text-empty').checked, unique: $('#text-unique').checked });
                $('#text-copy').disabled = false;
                message('处理完成。原文保持不变。');
            });
            copy('#text-copy', '#text-output');
        } else if (id === 'encoding') {
            const clear = () => { $('#encoding-output').value = ''; $('#encoding-copy').disabled = true; message(''); };
            $('#encoding-type').addEventListener('change', clear);
            $('#encoding-run').addEventListener('click', () => {
                clear();
                try {
                    $('#encoding-output').value = convertEncoding($('#encoding-input').value, $('#encoding-type').value);
                    $('#encoding-copy').disabled = false;
                    message('转换完成。');
                } catch (err) { message(err instanceof URIError ? 'URL 编解码失败：请检查百分号转义及 UTF-8 字符是否有效。' : err.message, true); }
            });
            copy('#encoding-copy', '#encoding-output');
        } else if (id === 'timestamp') {
            const show = date => {
                $('#timestamp-output').value = `本地时间：${localDate(date).replace('T', ' ')} (${Intl.DateTimeFormat().resolvedOptions().timeZone})\nUTC：${date.toISOString()}\nUnix 秒：${Math.floor(date.getTime() / 1000)}\nUnix 毫秒：${date.getTime()}`;
                // datetime-local controls support only positive, four-digit years reliably.
                $('#datetime-input').value = date.getFullYear() > 0 && date.getFullYear() <= 9999 ? localDate(date) : '';
                $('#timestamp-copy').disabled = false;
                message('转换完成。');
            };
            const run = fn => {
                $('#timestamp-output').value = ''; $('#timestamp-copy').disabled = true;
                try { show(fn()); } catch (err) { message(err.message, true); }
            };
            $('#timestamp-form').addEventListener('submit', e => { e.preventDefault(); run(() => parseTimestamp($('#timestamp-input').value, $('#timestamp-unit').value)); });
            $('#timestamp-now').addEventListener('click', () => {
                const now = Date.now();
                $('#timestamp-input').value = $('#timestamp-unit').value === 'seconds' ? Math.floor(now / 1000) : now;
                run(() => parseTimestamp($('#timestamp-input').value, $('#timestamp-unit').value));
            });
            $('#datetime-form').addEventListener('submit', e => {
                e.preventDefault();
                run(() => {
                    const value = $('#datetime-input').value;
                    const date = new Date(value);
                    const parts = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
                    if (!parts || !Number.isFinite(date.getTime()) || date.getFullYear() !== +parts[1] || date.getMonth() + 1 !== +parts[2] || date.getDate() !== +parts[3] || date.getHours() !== +parts[4] || date.getMinutes() !== +parts[5]) {
                        throw new Error('请输入有效的本地日期时间（该时区中此时间须存在）。');
                    }
                    $('#timestamp-input').value = $('#timestamp-unit').value === 'seconds' ? Math.floor(date.getTime() / 1000) : date.getTime();
                    return date;
                });
            });
            copy('#timestamp-copy', '#timestamp-output');
        } else if (id === 'diff') {
            $('#diff-run').addEventListener('click', () => {
                $('#diff-output').replaceChildren();
                $('#diff-count').textContent = '';
                try {
                    const result = diffLines($('#diff-before').value, $('#diff-after').value);
                    const counts = { added: 0, removed: 0, same: 0 };
                    const fragment = document.createDocumentFragment();
                    for (const line of result) {
                        counts[line.type]++;
                        const row = document.createElement('div');
                        row.className = `diff-line diff-${line.type}`;
                        for (const [className, text] of [['diff-number', line.oldLine], ['diff-number', line.newLine], ['diff-sign', { same: ' ', added: '+', removed: '−' }[line.type]], ['diff-text', line.text]]) {
                            const span = document.createElement('span'); span.className = className; span.textContent = text; row.append(span);
                        }
                        fragment.append(row);
                    }
                    $('#diff-output').replaceChildren(fragment);
                    $('#diff-count').textContent = `差异 ${counts.added + counts.removed} 行 · 新增 ${counts.added} · 删除 ${counts.removed} · 未修改 ${counts.same}`;
                    message(result.length ? '对比完成。' : '两段文本均为空，无差异。');
                } catch (err) { message(err.message, true); }
            });
        } else if (id === 'image') return initImage($, message);
        return () => {};
    }

    function initImage($, message) {
        let originalURL = '', resultURL = '', source = null, fileName = '', version = 0, disposed = false;
        const size = bytes => bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
        const clearResult = () => {
            if (resultURL) URL.revokeObjectURL(resultURL);
            resultURL = '';
            $('#image-result').removeAttribute('src'); $('#image-result').hidden = true;
            $('#image-download').removeAttribute('href'); $('#image-download').hidden = true;
            $('#image-result-info').textContent = '尚未转换';
        };
        const clearSource = () => {
            if (originalURL) URL.revokeObjectURL(originalURL);
            originalURL = ''; source = null;
            $('#image-original').removeAttribute('src'); $('#image-original').hidden = true;
            $('#image-original-info').textContent = '尚未选择图片';
            $('#image-run').disabled = true;
        };
        const convert = async () => {
            if (!source || disposed) return;
            const token = ++version;
            clearResult();
            const max = Number($('#image-max').value);
            if (!Number.isInteger(max) || max < 1 || max > 10000) { message('最长边请输入 1 到 10000 之间的整数。', true); return; }
            const type = $('#image-format').value, quality = Number($('#image-quality').value) / 100;
            const image = source;
            $('#image-run').disabled = true; message('正在转换图片…');
            const canvas = document.createElement('canvas');
            try {
                const scale = Math.min(1, max / Math.max(image.naturalWidth, image.naturalHeight));
                canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
                canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
                const ctx = canvas.getContext('2d');
                if (!ctx) throw new Error('浏览器无法创建图片画布。');
                if (type === 'image/jpeg') { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); }
                ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
                const blob = await new Promise(resolve => canvas.toBlob(resolve, type, quality));
                if (disposed || token !== version) return;
                if (!blob || blob.type !== type) throw new Error('浏览器不支持所选输出格式，请选择其他格式。');
                resultURL = URL.createObjectURL(blob);
                $('#image-result').src = resultURL; $('#image-result').hidden = false;
                $('#image-result-info').textContent = `${canvas.width} × ${canvas.height} · ${size(blob.size)} · ${type.split('/')[1].toUpperCase()}`;
                $('#image-download').href = resultURL;
                $('#image-download').download = `${fileName.replace(/\.[^.]+$/, '') || 'image'}-converted.${type === 'image/jpeg' ? 'jpg' : type.split('/')[1]}`;
                $('#image-download').hidden = false;
                message('转换完成，可以下载结果。');
            } catch (err) { if (!disposed && token === version) message(`转换失败：${err.message}`, true); }
            finally {
                canvas.width = canvas.height = 0;
                if (!disposed && token === version) $('#image-run').disabled = !source;
            }
        };
        const load = async file => {
            if (!file || disposed) return;
            const token = ++version;
            clearResult(); clearSource();
            if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { message('请选择 JPEG、PNG 或 WebP 图片。', true); return; }
            if (file.size > 20 * 1024 * 1024) { message('图片超过 20 MB，请选择更小的文件。', true); return; }
            originalURL = URL.createObjectURL(file);
            const image = new Image(); image.src = originalURL;
            message('正在读取图片…');
            try {
                await image.decode();
                if (disposed || token !== version) return;
                source = image; fileName = file.name;
                $('#image-original').src = originalURL; $('#image-original').hidden = false;
                $('#image-original-info').textContent = `${image.naturalWidth} × ${image.naturalHeight} · ${size(file.size)}`;
                $('#image-run').disabled = false;
                await convert();
            } catch (_) {
                if (!disposed && token === version) { clearSource(); message('无法读取图片，文件可能已损坏。', true); }
            }
        };
        $('#image-file').addEventListener('change', e => { const file = e.target.files[0]; e.target.value = ''; load(file); });
        $('#image-run').addEventListener('click', convert);
        const settingsChanged = () => {
            if (source) ++version;
            clearResult(); $('#image-run').disabled = !source;
            $('#image-quality').disabled = $('#image-format').value === 'image/png';
            $('#image-quality-label').textContent = $('#image-quality').disabled ? 'PNG 无损' : `${$('#image-quality').value}%`;
            if (source) message('设置已更改，请点击压缩 / 转换。');
        };
        for (const selector of ['#image-format', '#image-quality', '#image-max']) $(selector).addEventListener('input', settingsChanged);
        const drop = $('#image-drop');
        for (const event of ['dragenter', 'dragover']) drop.addEventListener(event, e => { e.preventDefault(); drop.classList.add('is-dragging'); });
        drop.addEventListener('dragleave', () => drop.classList.remove('is-dragging'));
        drop.addEventListener('drop', e => {
            e.preventDefault(); drop.classList.remove('is-dragging');
            if (e.dataTransfer.files.length !== 1) { message('请每次拖入一张图片。', true); return; }
            load(e.dataTransfer.files[0]);
        });
        return () => { disposed = true; ++version; clearResult(); clearSource(); };
    }
    window.LocalTools = { tools, init };
})();
