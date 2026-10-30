// Admin -> AI calls (templates/admin_ai_calls.html): every AI call with its
// prompts, answer, model, tokens, cost and time, newest first, with filters,
// and one call in full in a dialog. "All calls in this generation" filters to
// its group (one post's caption, hashtags, review...). Data: /api/admin/ai-calls
// (services/llm_trace.py). Vanilla JS like performance.js.
(function () {
    const rows = document.getElementById('aiRows');
    if (!rows) return;
    const $ = (id) => document.getElementById(id);
    const STATUS = { ok: 'Answered', empty: 'Empty answer', error: 'Failed' };
    let page = 1;
    let group = null;  // "All calls in this generation"

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function number(n) { return Number(n || 0).toLocaleString(); }
    function money(n) { return '$' + Number(n || 0).toFixed(n >= 1 ? 2 : 5); }
    function when(iso) {
        return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit' });
    }
    function seconds(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms'; }
    // "agents/caption_agent.py:generate_caption" -> "caption_agent · generate_caption"
    function featureName(f) {
        const [path, fn] = String(f || '').split(':');
        return path.replace(/^.*\//, '').replace(/\.py$/, '') + (fn ? ' · ' + fn : '');
    }

    function query() {
        const q = new URLSearchParams({ page: page });
        [['user', 'aiUser'], ['feature', 'aiFeature'], ['status', 'aiStatus'], ['days', 'aiDays'], ['q', 'aiSearch']].forEach(function ([k, id]) {
            const v = $(id).value.trim();
            if (v) q.set(k, v);
        });
        if (group) { q.set('group', group); q.delete('days'); }
        return q.toString();
    }

    function tile(label, value, sub) {
        const box = el('div', 'perf-kpi');
        box.append(el('span', 'perf-kpi-label', label), el('strong', '', value));
        if (sub) box.appendChild(el('span', 'perf-kpi-sub', sub));
        return box;
    }

    function load() {
        rows.textContent = '';
        fetch('/api/admin/ai-calls?' + query(), { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                if (!r.success) throw new Error(r.error || 'failed');
                const t = r.totals || {};
                const totals = $('aiTotals');
                totals.textContent = '';
                totals.append(
                    tile('Calls', number(t.calls), t.errors ? number(t.errors) + ' failed or empty' : 'none failed'),
                    tile('Tokens in', number(t.input_tokens)),
                    tile('Tokens out', number(t.output_tokens)),
                    tile('Cost', money(t.cost_usd), 'estimated from the providers\' rates'));
                $('aiIntro').textContent = r.tracing
                    ? `Every call to the AI: the prompts sent, the answer, model, tokens, cost and time. Kept ${r.keep_days} days. Admins only.`
                    : 'Keeping AI calls is switched off (LLM_TRACING=false). Older calls still show until they are deleted.';
                const select = $('aiFeature');
                const chosen = select.value;
                select.textContent = '';
                select.appendChild(el('option', '', 'All')).value = '';
                (r.features || []).forEach(function (f) {
                    const o = el('option', '', featureName(f));
                    o.value = f;
                    o.title = f;
                    select.appendChild(o);
                });
                select.value = chosen;
                $('aiCount').textContent = number(r.total) + ' call' + (r.total === 1 ? '' : 's');
                $('aiEmpty').classList.toggle('d-none', r.calls.length > 0);
                $('aiPage').textContent = r.total ? 'Page ' + page + ' of ' + Math.max(1, Math.ceil(r.total / r.page_size)) : '';
                $('aiPrev').disabled = page <= 1;
                $('aiNext').disabled = page * r.page_size >= r.total;
                r.calls.forEach(function (c) {
                    const tr = el('tr', 'ai-row');
                    tr.tabIndex = 0;
                    tr.setAttribute('role', 'button');
                    tr.setAttribute('aria-label', 'Open call ' + c.id + ': ' + featureName(c.feature));
                    const feature = el('td');
                    feature.append(el('strong', '', featureName(c.feature)), el('span', 'perf-sub ai-preview', c.preview));
                    const status = el('td');
                    status.appendChild(el('span', 'perf-badge ' + (c.status === 'ok' ? 'is-up' : 'is-down'), STATUS[c.status] || c.status));
                    if (c.attempt > 1) status.appendChild(el('span', 'perf-sub', ' try ' + c.attempt));
                    tr.append(el('td', 'ai-when', when(c.created_at)), el('td', '', c.user_email || (c.user_id ? 'user ' + c.user_id : 'system')),
                        feature, el('td', 'ai-model', (c.model || c.provider)), status,
                        el('td', 'num', number(c.input_tokens) + ' / ' + number(c.output_tokens)),
                        el('td', 'num', money(c.cost_usd)), el('td', 'num', seconds(c.latency_ms)));
                    tr.addEventListener('click', function () { open(c.id); });
                    tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(c.id); } });
                    rows.appendChild(tr);
                });
                const note = $('aiGroupNote');
                note.classList.toggle('d-none', !group);
                if (group) {
                    note.textContent = '';
                    const back = el('button', 'perf-btn', 'Show all calls again');
                    back.type = 'button';
                    back.addEventListener('click', function () { group = null; page = 1; load(); });
                    note.append(el('span', '', 'Showing one generation\'s calls only. '), back);
                }
            })
            .catch(function () {
                $('aiEmpty').textContent = 'AI calls are unavailable right now.';
                $('aiEmpty').classList.remove('d-none');
            });
    }

    // ── One call in full ──────────────────────────────────────────────
    function block(title, text, id) {
        const wrap = el('section', 'ai-block');
        const head = el('div', 'ai-block-head');
        head.appendChild(el('h3', '', title));
        if (text) {
            const copy = el('button', 'perf-btn', 'Copy');
            copy.type = 'button';
            copy.addEventListener('click', function () {
                navigator.clipboard.writeText(text).then(() => { copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1500); });
            });
            head.appendChild(copy);
        }
        const pre = el('pre', 'ai-pre', text || '(none)');
        if (id) pre.id = id;
        wrap.append(head, pre);
        return wrap;
    }
    function pretty(text) {
        try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
    }
    function open(id) {
        const body = $('aiDialogBody');
        body.textContent = 'Loading...';
        $('aiDialog').showModal();
        fetch('/api/admin/ai-calls/' + id, { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                const c = r.call;
                if (!c) { body.textContent = 'This call is no longer kept.'; return; }
                $('aiDialogTitle').textContent = featureName(c.feature) + ' · ' + when(c.created_at);
                body.textContent = '';
                const meta = el('dl', 'ai-meta');
                [['Result', (STATUS[c.status] || c.status) + (c.attempt > 1 ? ' (try ' + c.attempt + ')' : '')],
                 ['User', c.user_email || (c.user_id ? 'user ' + c.user_id : 'system / scheduler')],
                 ['Called from', c.feature], ['Provider', c.provider], ['Model', c.model || '–'],
                 ['Answer type', c.kind === 'json' ? 'JSON' : 'Text' + (c.streamed ? ' (streamed)' : '')],
                 ['Tokens', number(c.input_tokens) + ' in · ' + number(c.output_tokens) + ' out'],
                 ['Cost', money(c.cost_usd)], ['Time', seconds(c.latency_ms)],
                 ['Settings', ['temperature ' + (c.temperature ?? '–'), 'max tokens ' + (c.max_tokens ?? '–'),
                               c.reasoning_effort ? 'reasoning ' + c.reasoning_effort : null].filter(Boolean).join(' · ')],
                 ['Request', c.request_id || '–']].forEach(function ([k, v]) {
                    meta.append(el('dt', '', k), el('dd', '', v));
                });
                body.appendChild(meta);
                if (c.group_id) {
                    const all = el('button', 'perf-btn', 'All calls in this generation');
                    all.type = 'button';
                    all.addEventListener('click', function () { group = c.group_id; page = 1; $('aiDialog').close(); load(); });
                    body.appendChild(all);
                }
                if (c.error) body.appendChild(el('p', 'inbox-error ai-error', c.error));
                body.append(block('System prompt', c.system_prompt), block('User prompt', c.user_prompt),
                    block('Answer', c.kind === 'json' && c.output ? pretty(c.output) : c.output));
            })
            .catch(function () { body.textContent = 'This call couldn\'t be loaded.'; });
    }

    $('aiFilters').addEventListener('submit', function (e) { e.preventDefault(); page = 1; load(); });
    $('aiClear').addEventListener('click', function () {
        ['aiUser', 'aiFeature', 'aiStatus', 'aiSearch'].forEach((id) => { $(id).value = ''; });
        $('aiDays').value = '7';
        group = null;
        page = 1;
        load();
    });
    $('aiPrev').addEventListener('click', function () { if (page > 1) { page -= 1; load(); } });
    $('aiNext').addEventListener('click', function () { page += 1; load(); });
    load();
})();
