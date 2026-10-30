// Admin -> AI calls (templates/admin_ai_calls.html), laid out like an LLM
// tracing tool:
//   Runs     every AI call: name (the part of the app), input, output, error,
//            start time, latency, tokens, cost, user
//   Traces   one row per generation or request (the calls sharing a group)
//   Monitor  run count, latency (P50 / P99), tokens and cost per day, and per name
// A search box, filter chips and a time range apply to all three, with a stats
// strip on top. Clicking a run or trace opens the trace drawer: its runs as a
// tree (or a waterfall of when each ran) on the left, the selected run on the
// right - input as SYSTEM / HUMAN messages, output as the AI message, plus
// Feedback (good / bad and a note), Playground (run the prompts again, as
// edited, maybe with another model, and compare the answers), Metadata and
// Raw. j / k move through the list, Esc closes.
// Data: /api/admin/ai-calls[/traces|/traces/<group>|/stats|/<id>] (services/llm_trace.py).
// Vanilla JS like performance.js.
(function () {
    const main = document.querySelector('.ls-main');
    if (!main) return;
    const $ = (id) => document.getElementById(id);
    const state = {
        view: 'runs', runsPage: 1, tracesPage: 1, list: [],
        filters: { q: '', status: '', feature: '', user: '', days: '7', feedback: '' },
        drawer: null,  // {group, trace, selected, mode, listIndex}
    };
    const detailCache = {};

    // ── Small helpers ─────────────────────────────────────────────────
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }
    function icon(classes) {
        const i = el('i', classes);
        i.setAttribute('aria-hidden', 'true');
        return i;
    }
    function number(n) { return Number(n || 0).toLocaleString(); }
    function money(n) { return '$' + Number(n || 0).toFixed(n >= 1 ? 2 : 5); }
    function seconds(ms) { return ms >= 1000 ? (ms / 1000).toFixed(2) + 's' : (ms || 0) + 'ms'; }
    function when(iso) {
        return new Date(iso).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
    }
    // "agents/caption_agent.py:generate_caption" -> "caption_agent.generate_caption"
    function runName(f) {
        const [path, fn] = String(f || '').split(':');
        return path.replace(/^.*\//, '').replace(/\.py$/, '') + (fn ? '.' + fn : '');
    }
    function latencyPill(ms) {
        return el('span', 'ls-lat ' + (ms < 2500 ? 'is-fast' : ms < 10000 ? 'is-mid' : 'is-slow'), seconds(ms));
    }
    function statusLabel(s) { return { ok: 'Success', error: 'Error', empty: 'Empty answer' }[s] || s; }
    function getJSON(url) {
        return fetch(url, { credentials: 'same-origin' }).then((r) => r.json().then((d) => {
            if (!r.ok || d.success === false) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
        }));
    }
    function query(extra) {
        const q = new URLSearchParams();
        const f = state.filters;
        if (f.q) q.set('q', f.q);
        if (f.status) q.set('status', f.status);
        if (f.feature) q.set('feature', f.feature);
        if (f.user) q.set('user', f.user);
        if (f.days) q.set('days', f.days);
        if (f.feedback) q.set('feedback', f.feedback);
        Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v));
        return q.toString();
    }
    function rowKeys(tr, open) {
        tr.tabIndex = 0;
        tr.addEventListener('click', open);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); open(); } });
    }

    // ── Filters: search, chips, time range ────────────────────────────
    function renderChips() {
        const box = $('lsChips');
        box.textContent = '';
        const f = state.filters;
        const FEEDBACK = { good: 'Good answer', bad: 'Bad answer', rated: 'Has feedback', unrated: 'No feedback yet' };
        [['status', 'Status', statusLabel(f.status)], ['feature', 'Name', runName(f.feature)], ['user', 'User', f.user],
         ['feedback', 'Feedback', FEEDBACK[f.feedback]], ['q', 'Search', f.q]]
            .filter(([k]) => f[k]).forEach(function ([k, label, value]) {
                const chip = el('span', 'ls-chip');
                chip.append(document.createTextNode(label + ': ' + value));
                const x = el('button');
                x.type = 'button';
                x.setAttribute('aria-label', 'Remove the ' + label + ' filter');
                x.appendChild(icon('fas fa-xmark'));
                x.addEventListener('click', function () {
                    f[k] = '';
                    if (k === 'q') $('lsSearch').value = '';
                    state.runsPage = 1;
                    state.tracesPage = 1;
                    refresh();
                });
                chip.appendChild(x);
                box.appendChild(chip);
            });
    }
    function toggleFilterPop(open) {
        const pop = $('lsFilterPop');
        pop.hidden = !open;
        $('lsFilterBtn').setAttribute('aria-expanded', String(open));
        if (open) {
            $('lsStatus').value = state.filters.status;
            $('lsFeature').value = state.filters.feature;
            $('lsUser').value = state.filters.user;
            $('lsFeedback').value = state.filters.feedback;
            $('lsStatus').focus();
        }
    }
    $('lsFilterBtn').addEventListener('click', function () { toggleFilterPop($('lsFilterPop').hidden); });
    $('lsFilterApply').addEventListener('click', function () {
        state.filters.status = $('lsStatus').value;
        state.filters.feature = $('lsFeature').value;
        state.filters.user = $('lsUser').value.trim();
        state.filters.feedback = $('lsFeedback').value;
        toggleFilterPop(false);
        refresh();
    });
    document.addEventListener('click', function (e) {
        if (!$('lsFilterPop').hidden && !e.target.closest('.ls-filter-menu')) toggleFilterPop(false);
    });
    $('lsSearchForm').addEventListener('submit', function (e) {
        e.preventDefault();
        state.filters.q = $('lsSearch').value.trim();
        refresh();
    });
    $('lsDays').addEventListener('change', function () { state.filters.days = this.value; refresh(); });
    $('lsRefresh').addEventListener('click', refresh);

    // ── Stats strip (and Monitor's charts) ────────────────────────────
    function stat(label, value) {
        const box = el('div', 'ls-stat');
        box.append(el('span', '', label), el('strong', '', value));
        return box;
    }
    function bars(box, days, series, fmt) {
        box.textContent = '';
        if (!days.length) { box.appendChild(el('div', 'ls-empty', 'No runs in this range.')); return; }
        const top = Math.max(...days.map((d) => Math.max(...series.map((s) => s.value(d)))), 0.000001);
        days.forEach(function (d) {
            const col = el('div', 'ls-bar-col');
            col.title = d.day + ' · ' + series.map((s) => s.name + ' ' + fmt(s.value(d))).join(' · ');
            const stack = el('div', 'ls-bar-stack');
            series.forEach(function (s) {
                const bar = el('span', 'ls-bar ' + (s.cls || ''));
                bar.style.height = (100 * s.value(d) / top).toFixed(1) + '%';
                stack.appendChild(bar);
            });
            col.append(stack, el('span', 'ls-bar-label', d.day.slice(5)));
            box.appendChild(col);
        });
    }
    function loadStats() {
        getJSON('/api/admin/ai-calls/stats?' + query()).then(function (r) {
            const s = r.summary;
            const box = $('lsStats');
            box.textContent = '';
            box.append(stat('Run count', number(s.calls)), stat('Error rate', s.error_rate + '%'), stat('Latency P50', seconds(s.p50_ms)),
                stat('Latency P99', seconds(s.p99_ms)), stat('Total tokens', number(s.tokens)), stat('Total cost', money(s.cost_usd)));
            if (state.view !== 'monitor') return;
            bars($('lsChartRuns'), r.days, [{ name: 'runs', value: (d) => d.calls }, { name: 'errors', value: (d) => d.errors, cls: 'is-bad' }], number);
            bars($('lsChartLatency'), r.days, [{ name: 'P99', value: (d) => d.p99_ms }, { name: 'P50', value: (d) => d.p50_ms, cls: 'is-light' }], seconds);
            bars($('lsChartTokens'), r.days, [{ name: 'tokens', value: (d) => d.tokens, cls: 'is-dark' }], number);
            bars($('lsChartCost'), r.days, [{ name: 'cost', value: (d) => d.cost_usd, cls: 'is-dark' }], money);
            const body = $('lsFeatureRows');
            body.textContent = '';
            r.features.forEach(function (f) {
                const tr = el('tr');
                const name = el('td');
                const wrap = el('span', 'ls-name');
                const ic = el('span', 'ls-icon is-llm');
                ic.appendChild(icon('fas fa-robot'));
                wrap.append(ic, el('strong', '', runName(f.feature)));
                name.appendChild(wrap);
                tr.append(name, el('td', 'num', number(f.calls)), el('td', 'num' + (f.errors ? ' ls-err' : ''), (f.calls ? Math.round(1000 * f.errors / f.calls) / 10 : 0) + '%'),
                    el('td', 'num', number(f.tokens)), el('td', 'num', money(f.cost_usd)), el('td', 'num', seconds(f.avg_ms)), el('td', 'num', seconds(f.p95_ms)));
                tr.title = 'Show the runs of ' + f.feature;
                rowKeys(tr, function () { state.filters.feature = f.feature; switchView('runs'); });
                body.appendChild(tr);
            });
            if (!r.features.length) body.appendChild(el('tr')).appendChild(el('td', 'ls-empty', 'No runs in this range.')).colSpan = 7;
        }).catch(function () { $('lsStats').textContent = ''; });
    }

    function feedbackCell(c) {
        const td = el('td', 'ls-fb-cell');
        if (c.rating) {
            td.appendChild(icon('fas ' + (c.rating > 0 ? 'fa-thumbs-up is-good' : 'fa-thumbs-down is-bad')));
            td.title = (c.rating > 0 ? 'Good answer' : 'Bad answer') + (c.feedback_note ? ': ' + c.feedback_note : '');
        } else if (c.feedback_note) {
            td.appendChild(icon('fas fa-note-sticky'));
            td.title = c.feedback_note;
        }
        return td;
    }

    // ── Runs ──────────────────────────────────────────────────────────
    function loadRuns() {
        const body = $('lsRunRows');
        getJSON('/api/admin/ai-calls?' + query({ page: state.runsPage })).then(function (r) {
            body.textContent = '';
            $('lsTracing').textContent = r.tracing ? 'Recording · kept ' + r.keep_days + ' days' : 'Recording is off';
            const select = $('lsFeature');
            const chosen = state.filters.feature;
            select.textContent = '';
            select.appendChild(el('option', '', 'Any')).value = '';
            (r.features || []).forEach(function (f) { const o = el('option', '', runName(f)); o.value = f; select.appendChild(o); });
            select.value = chosen;
            $('lsRunsCount').textContent = number(r.total);
            $('lsRunsEmpty').classList.toggle('d-none', r.calls.length > 0);
            $('lsRunsPage').textContent = r.total ? `${(state.runsPage - 1) * r.page_size + 1}–${(state.runsPage - 1) * r.page_size + r.calls.length} of ${number(r.total)}` : '';
            $('lsRunsPrev').disabled = state.runsPage <= 1;
            $('lsRunsNext').disabled = state.runsPage * r.page_size >= r.total;
            state.list = r.calls.map((c) => ({ kind: 'run', id: c.id, group: c.group_id }));
            r.calls.forEach(function (c, index) {
                const tr = el('tr');
                tr.dataset.index = index;
                const name = el('td');
                const wrap = el('span', 'ls-name');
                wrap.appendChild(el('span', 'ls-status is-' + c.status));
                const ic = el('span', 'ls-icon is-llm');
                ic.appendChild(icon('fas fa-robot'));
                wrap.append(ic, el('strong', '', runName(c.feature)));
                if (c.attempt > 1) wrap.appendChild(el('span', 'ls-pill', 'try ' + c.attempt));
                name.appendChild(wrap);
                name.title = c.feature + ' · ' + (c.model || c.provider);
                const latency = el('td', 'num');
                latency.appendChild(latencyPill(c.latency_ms));
                tr.append(name, el('td', 'ls-mono', c.preview), el('td', 'ls-mono', c.output_preview || '–'),
                    el('td', 'ls-err', c.error || ''), el('td', '', when(c.created_at)), latency,
                    el('td', 'num', number(c.input_tokens + c.output_tokens)), el('td', 'num', money(c.cost_usd)),
                    feedbackCell(c), el('td', '', c.user_email || (c.user_id ? 'user ' + c.user_id : 'system')));
                tr.lastChild.title = tr.lastChild.textContent;
                rowKeys(tr, function () { openFromList(index); });
                body.appendChild(tr);
            });
        }).catch(function (err) {
            body.textContent = '';
            $('lsRunsEmpty').textContent = 'Runs are unavailable right now (' + err.message + ').';
            $('lsRunsEmpty').classList.remove('d-none');
        });
    }
    $('lsRunsPrev').addEventListener('click', function () { if (state.runsPage > 1) { state.runsPage -= 1; loadRuns(); } });
    $('lsRunsNext').addEventListener('click', function () { state.runsPage += 1; loadRuns(); });

    // ── Traces ────────────────────────────────────────────────────────
    function loadTraces() {
        const body = $('lsTraceRows');
        getJSON('/api/admin/ai-calls/traces?' + query({ page: state.tracesPage })).then(function (r) {
            body.textContent = '';
            $('lsTracesCount').textContent = number(r.total);
            $('lsTracesEmpty').classList.toggle('d-none', r.traces.length > 0);
            $('lsTracesPage').textContent = r.total ? `${(state.tracesPage - 1) * r.page_size + 1}–${(state.tracesPage - 1) * r.page_size + r.traces.length} of ${number(r.total)}` : '';
            $('lsTracesPrev').disabled = state.tracesPage <= 1;
            $('lsTracesNext').disabled = state.tracesPage * r.page_size >= r.total;
            state.list = r.traces.map((t) => ({ kind: 'trace', group: t.group_id }));
            r.traces.forEach(function (t, index) {
                const tr = el('tr');
                tr.dataset.index = index;
                const name = el('td');
                const wrap = el('span', 'ls-name');
                const ic = el('span', 'ls-icon is-chain');
                ic.appendChild(icon('fas fa-link'));
                wrap.append(ic, el('strong', '', t.features.map(runName).join(' → ')));
                name.appendChild(wrap);
                name.title = t.features.join('\n');
                const status = el('td');
                status.appendChild(el('span', 'ls-pill ' + (t.errors ? 'is-error' : 'is-ok'), t.errors ? t.errors + ' error' + (t.errors === 1 ? '' : 's') : 'Success'));
                const latency = el('td', 'num');
                latency.appendChild(latencyPill(t.duration_ms));
                tr.append(name, status, el('td', '', when(t.started_at)), latency, el('td', 'num', number(t.calls)),
                    el('td', 'num', number(t.input_tokens + t.output_tokens)), el('td', 'num', money(t.cost_usd)),
                    el('td', '', t.user_email || (t.user_id ? 'user ' + t.user_id : 'system')));
                rowKeys(tr, function () { openFromList(index); });
                body.appendChild(tr);
            });
        }).catch(function (err) {
            body.textContent = '';
            $('lsTracesEmpty').textContent = 'Traces are unavailable right now (' + err.message + ').';
            $('lsTracesEmpty').classList.remove('d-none');
        });
    }
    $('lsTracesPrev').addEventListener('click', function () { if (state.tracesPage > 1) { state.tracesPage -= 1; loadTraces(); } });
    $('lsTracesNext').addEventListener('click', function () { state.tracesPage += 1; loadTraces(); });

    // ── The trace drawer ──────────────────────────────────────────────
    let lastFocus = null;
    function markOpenRow() {
        document.querySelectorAll('.ls-table tbody tr.is-open').forEach((tr) => tr.classList.remove('is-open'));
        const d = state.drawer;
        if (!d || d.listIndex === null) return;
        const body = state.view === 'traces' ? $('lsTraceRows') : $('lsRunRows');
        const tr = body.querySelector(`tr[data-index="${d.listIndex}"]`);
        if (tr) tr.classList.add('is-open');
    }
    function openFromList(index) {
        const item = state.list[index];
        if (!item) return;
        openDrawer(item.group, item.kind === 'run' ? item.id : null, index);
    }
    function openDrawer(group, runId, listIndex) {
        if ($('lsDrawer').hidden) lastFocus = document.activeElement;
        state.drawer = { group, trace: null, selected: runId, mode: (state.drawer && state.drawer.mode) || 'tree', listIndex };
        $('lsDrawer').hidden = false;
        $('lsDrawerBack').hidden = false;
        document.body.style.overflow = 'hidden';
        $('lsPrevRun').disabled = !(listIndex > 0);
        $('lsNextRun').disabled = !(listIndex !== null && listIndex < state.list.length - 1);
        $('lsTree').textContent = '';
        $('lsDetail').textContent = 'Loading...';
        markOpenRow();
        $('lsDrawerClose').focus();
        if (!group) {  // a call outside any group: a trace of one
            showRun(runId);
            $('lsDrawerTitle').textContent = 'Run';
            return;
        }
        getJSON('/api/admin/ai-calls/traces/' + encodeURIComponent(group)).then(function (r) {
            if (!state.drawer || state.drawer.group !== group) return;
            state.drawer.trace = r.trace;
            const t = r.trace;
            $('lsDrawerTitle').textContent = t.calls.map((c) => runName(c.feature)).filter((v, i, a) => a.indexOf(v) === i).slice(0, 3).join(' → ')
                + (t.calls.length > 3 ? ' …' : '');
            $('lsDrawerSub').textContent = when(t.started_at) + ' · ' + (t.user_email || (t.user_id ? 'user ' + t.user_id : 'system'));
            renderTree();
            if (runId) showRun(runId); else showTraceSummary();
        }).catch(function () { $('lsDetail').textContent = 'This trace is no longer kept.'; });
    }
    function closeDrawer() {
        state.drawer = null;
        $('lsDrawer').hidden = true;
        $('lsDrawerBack').hidden = true;
        document.body.style.overflow = '';
        markOpenRow();
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    $('lsDrawerClose').addEventListener('click', closeDrawer);
    $('lsDrawerBack').addEventListener('click', closeDrawer);
    $('lsPrevRun').addEventListener('click', function () { if (state.drawer && state.drawer.listIndex > 0) openFromList(state.drawer.listIndex - 1); });
    $('lsNextRun').addEventListener('click', function () {
        if (state.drawer && state.drawer.listIndex !== null && state.drawer.listIndex < state.list.length - 1) openFromList(state.drawer.listIndex + 1);
    });
    document.addEventListener('keydown', function (e) {
        if (!state.drawer || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
        if (e.key === 'Escape') { e.preventDefault(); closeDrawer(); }
        else if (e.key === 'j') $('lsNextRun').click();
        else if (e.key === 'k') $('lsPrevRun').click();
    });
    document.querySelectorAll('.ls-seg button').forEach(function (b) {
        b.addEventListener('click', function () {
            if (!state.drawer) return;
            state.drawer.mode = b.dataset.tree;
            document.querySelectorAll('.ls-seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
            renderTree();
        });
    });

    function node(label, metaParts, opts) {
        const b = el('button', 'ls-node' + (opts.child ? ' is-child' : ''));
        b.type = 'button';
        b.setAttribute('role', 'treeitem');
        b.setAttribute('aria-selected', String(!!opts.selected));
        const ic = el('span', 'ls-icon ' + (opts.child ? 'is-llm' : 'is-chain'));
        ic.appendChild(icon(opts.child ? 'fas fa-robot' : 'fas fa-link'));
        const mainPart = el('span', 'ls-node-main');
        mainPart.append(el('strong', '', label), el('span', 'ls-node-meta', metaParts.join(' · ')));
        const right = el('span', 'ls-node-meta');
        right.append(el('span', 'ls-status is-' + (opts.status || 'ok')), latencyPill(opts.ms));
        b.append(ic, mainPart, right);
        b.addEventListener('click', opts.onClick);
        return b;
    }
    function renderTree() {
        const d = state.drawer;
        const box = $('lsTree');
        box.textContent = '';
        if (!d || !d.trace) return;
        const t = d.trace;
        box.appendChild(node('Trace', [t.calls.length + ' runs', number(t.totals.input_tokens + t.totals.output_tokens) + ' tokens', money(t.totals.cost_usd)],
            { selected: !d.selected, status: t.totals.errors ? 'error' : 'ok', ms: t.duration_ms, onClick: function () { d.selected = null; renderTree(); showTraceSummary(); } }));
        const total = Math.max(1, t.duration_ms);
        t.calls.forEach(function (c) {
            box.appendChild(node(runName(c.feature),
                [(c.model || c.provider), number(c.input_tokens + c.output_tokens) + ' tok'].concat(c.attempt > 1 ? ['try ' + c.attempt] : []),
                { child: true, selected: d.selected === c.id, status: c.status, ms: c.latency_ms, onClick: function () { showRun(c.id); } }));
            if (d.mode === 'waterfall') {
                const lane = el('div', 'ls-node-bar');
                lane.setAttribute('aria-hidden', 'true');
                const bar = el('span', 'is-' + c.status);
                bar.style.left = (100 * c.start_ms / total).toFixed(2) + '%';
                bar.style.width = Math.max(0.8, 100 * c.latency_ms / total).toFixed(2) + '%';
                lane.appendChild(bar);
                box.appendChild(lane);
            }
        });
    }

    function pill(text, cls) { return el('span', 'ls-pill' + (cls ? ' ' + cls : ''), text); }
    function copyBtn(text) {
        const b = el('button', 'ls-copy', 'Copy');
        b.type = 'button';
        b.addEventListener('click', function () {
            navigator.clipboard.writeText(text || '').then(() => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy'; }, 1400); });
        });
        return b;
    }
    function message(role, cls, text, code) {
        const box = el('div', 'ls-msg');
        const head = el('div', 'ls-msg-head');
        head.append(el('span', 'ls-role ' + cls, role), copyBtn(text));
        box.append(head, el('pre', 'ls-msg-body' + (code ? ' is-code' : ''), text || '(empty)'));
        return box;
    }
    function pretty(text) {
        try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
    }
    function showTraceSummary() {
        const t = state.drawer && state.drawer.trace;
        const box = $('lsDetail');
        box.textContent = '';
        if (!t) return;
        const head = el('div', 'ls-detail-head');
        head.append(el('h3', '', 'Trace'), pill(t.totals.errors ? t.totals.errors + ' error' + (t.totals.errors === 1 ? '' : 's') : 'Success', t.totals.errors ? 'is-error' : 'is-ok'),
            pill(seconds(t.duration_ms)), pill(number(t.totals.input_tokens + t.totals.output_tokens) + ' tokens'), pill(money(t.totals.cost_usd)));
        box.appendChild(head);
        const meta = el('dl', 'ls-meta');
        [['Runs', t.calls.length], ['Started', new Date(t.started_at).toLocaleString()], ['User', t.user_email || (t.user_id ? 'user ' + t.user_id : 'system / scheduler')],
         ['Tokens', number(t.totals.input_tokens) + ' in · ' + number(t.totals.output_tokens) + ' out'], ['Trace id', t.group_id]]
            .forEach(([k, v]) => meta.append(el('dt', '', k), el('dd', '', v)));
        box.append(meta, el('p', 'ls-sub', 'Pick a run on the left for its prompts and answer. The waterfall shows which runs went at the same time.'));
    }
    function showRun(id) {
        const d = state.drawer;
        if (!d) return;
        d.selected = id;
        renderTree();
        const box = $('lsDetail');
        box.textContent = 'Loading...';
        // startTab: re-drawn after feedback, it stays on the tab that was open
        const render = function (c, startTab) {
            if (!state.drawer || state.drawer.selected !== id) return;
            let current = startTab || 'run';
            const again = function (fresh) { render(fresh, current); };
            box.textContent = '';
            const head = el('div', 'ls-detail-head');
            head.append(el('h3', '', runName(c.feature)), pill(statusLabel(c.status), 'is-' + c.status), pill(seconds(c.latency_ms)),
                pill(number(c.input_tokens) + ' → ' + number(c.output_tokens) + ' tokens'), pill(money(c.cost_usd)), pill(c.model || c.provider));
            head.appendChild(thumbs(c, again));
            box.appendChild(head);
            const tabs = el('div', 'ls-subtabs');
            tabs.setAttribute('role', 'tablist');
            const panel = el('div');
            const show = {
                run: function () {
                    panel.textContent = '';
                    if (c.error) panel.append(el('div', 'ls-section-label', 'Error'), el('pre', 'ls-error-box', c.error));
                    panel.append(el('div', 'ls-section-label', 'Input'), message('SYSTEM', 'is-system', c.system_prompt),
                        message('HUMAN', 'is-human', c.user_prompt), el('div', 'ls-section-label', 'Output'),
                        message('AI', 'is-ai', c.output === null ? '' : (c.kind === 'json' ? pretty(c.output) : c.output), c.kind === 'json'));
                    panel.style.cssText = 'display:flex;flex-direction:column;gap:10px';
                },
                metadata: function () {
                    panel.textContent = '';
                    panel.removeAttribute('style');
                    const meta = el('dl', 'ls-meta');
                    [['Name', c.feature], ['Run id', c.id], ['Trace id', c.group_id || '–'], ['Request id', c.request_id || '–'],
                     ['User', c.user_email || (c.user_id ? 'user ' + c.user_id : 'system / scheduler')], ['Provider', c.provider],
                     ['Model', c.model || '–'], ['Answer type', c.kind === 'json' ? 'JSON' : 'Text'], ['Streamed', c.streamed ? 'Yes' : 'No'],
                     ['Try', c.attempt], ['Temperature', c.temperature ?? '–'], ['Max tokens', c.max_tokens ?? '–'],
                     ['Reasoning effort', c.reasoning_effort || '–'], ['Start time', new Date(new Date(c.created_at).getTime() - c.latency_ms).toLocaleString()],
                     ['End time', new Date(c.created_at).toLocaleString()]].forEach(([k, v]) => meta.append(el('dt', '', k), el('dd', '', String(v))));
                    panel.appendChild(meta);
                },
                raw: function () {
                    panel.textContent = '';
                    panel.removeAttribute('style');
                    const raw = JSON.stringify(c, null, 2);
                    panel.appendChild(message('RAW', '', raw, true));
                },
            };
            show.feedback = function () { panel.textContent = ''; panel.removeAttribute('style'); feedbackPanel(panel, c, again); };
            show.playground = function () { panel.textContent = ''; panel.removeAttribute('style'); playgroundPanel(panel, c); };
            ['run', 'feedback', 'playground', 'metadata', 'raw'].forEach(function (key) {
                const label = { run: 'Run', feedback: c.rating ? (c.rating > 0 ? 'Feedback \u2713' : 'Feedback \u2717') : 'Feedback',
                                playground: 'Playground', metadata: 'Metadata', raw: 'Raw' }[key];
                const tab = el('button', 'ls-tab', label);
                tab.type = 'button';
                tab.setAttribute('role', 'tab');
                tab.setAttribute('aria-selected', String(key === current));
                tab.addEventListener('click', function () {
                    current = key;
                    tabs.querySelectorAll('.ls-tab').forEach((x) => x.setAttribute('aria-selected', String(x === tab)));
                    show[key]();
                });
                tabs.appendChild(tab);
            });
            box.append(tabs, panel);
            show[current]();
        };
        if (detailCache[id]) { render(detailCache[id]); return; }
        getJSON('/api/admin/ai-calls/' + id).then(function (r) { detailCache[id] = r.call; render(r.call); })
            .catch(function () { box.textContent = 'This run is no longer kept.'; });
    }

    // ── Feedback: an admin's verdict on a run's answer ────────────────
    function saveFeedback(c, rating, note, rerender) {
        return fetch('/api/admin/ai-calls/' + c.id + '/feedback', {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rating: rating, note: note }),
        }).then((r) => r.json()).then(function (r) {
            if (!r.success) throw new Error(r.error || 'not saved');
            detailCache[c.id] = r.call;
            const row = state.list.findIndex((x) => x.id === c.id);  // the table's icon follows
            if (row >= 0) {
                const cell = $('lsRunRows').querySelector(`tr[data-index="${row}"] .ls-fb-cell`);
                if (cell) cell.replaceWith(feedbackCell(r.call));
            }
            rerender(r.call);
            return r.call;
        });
    }
    function thumbs(c, rerender) {
        const group = el('span', 'ls-thumbs');
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', 'Rate this answer');
        [[1, 'fa-thumbs-up', 'Good answer'], [-1, 'fa-thumbs-down', 'Bad answer']].forEach(function ([value, cls, label]) {
            const b = el('button', 'ls-icon-btn' + (c.rating === value ? (value > 0 ? ' is-good' : ' is-bad') : ''));
            b.type = 'button';
            b.title = label;
            b.setAttribute('aria-label', label);
            b.setAttribute('aria-pressed', String(c.rating === value));
            b.appendChild(icon('fas ' + cls));
            b.addEventListener('click', function () {
                saveFeedback(c, c.rating === value ? 0 : value, c.feedback_note || '', rerender);  // the same again clears it
            });
            group.appendChild(b);
        });
        return group;
    }
    function feedbackPanel(panel, c, rerender) {
        const wrap = el('div', 'ls-fb');
        const rate = el('div', 'ls-fb-row');
        rate.append(el('span', 'ls-section-label', 'Was this a good answer?'), thumbs(c, rerender));
        const label = el('label', 'ls-section-label', 'Note');
        label.htmlFor = 'lsFbNote';
        const note = el('textarea', 'ls-textarea');
        note.id = 'lsFbNote';
        note.rows = 4;
        note.value = c.feedback_note || '';
        note.placeholder = 'What was wrong or right? e.g. "Invented a price", "Ignored the brand voice"';
        const save = el('button', 'ls-btn is-main', 'Save note');
        save.type = 'button';
        const status = el('span', 'ls-sub');
        if (c.feedback_at) status.textContent = 'Last saved ' + new Date(c.feedback_at).toLocaleString() + (c.feedback_by_email ? ' by ' + c.feedback_by_email : '');
        save.addEventListener('click', function () {
            save.disabled = true;
            saveFeedback(c, c.rating || 0, note.value, function (fresh) { status.textContent = 'Saved.'; c.feedback_note = fresh.feedback_note; })
                .catch(function (err) { status.textContent = 'Not saved: ' + err.message; })
                .finally(function () { save.disabled = false; });
        });
        const actions = el('div', 'ls-fb-row');
        actions.append(save, status);
        wrap.append(rate, label, note, actions,
            el('p', 'ls-sub', 'Use the Feedback filter to find answers marked good or bad. Feedback is deleted with the run after the keeping period.'));
        panel.appendChild(wrap);
    }

    // ── Playground: run the prompts again, as edited, and compare ─────
    function playgroundPanel(panel, c) {
        const wrap = el('div', 'ls-pg');
        const field = function (labelText, node, id) {
            const box = el('div', 'ls-pg-field');
            const label = el('label', 'ls-section-label', labelText);
            label.htmlFor = id;
            node.id = id;
            box.append(label, node);
            return box;
        };
        const system = el('textarea', 'ls-textarea ls-code-input');
        system.rows = 8;
        system.value = c.system_prompt || '';
        const user = el('textarea', 'ls-textarea ls-code-input');
        user.rows = 6;
        user.value = c.user_prompt || '';
        const model = el('input', 'ls-input');
        model.placeholder = c.model || 'the usual model';
        const temperature = el('input', 'ls-input');
        temperature.type = 'number'; temperature.min = '0'; temperature.max = '2'; temperature.step = '0.1';
        temperature.value = c.temperature ?? 0.7;
        const maxTokens = el('input', 'ls-input');
        maxTokens.type = 'number'; maxTokens.min = '1'; maxTokens.max = '16000';
        maxTokens.value = c.max_tokens ?? 1200;
        const json = el('input');
        json.type = 'checkbox';
        json.checked = c.kind === 'json';
        const jsonLabel = el('label', 'ls-check');
        jsonLabel.append(json, document.createTextNode(' Ask for a JSON answer'));
        const params = el('div', 'ls-pg-params');
        params.append(field('Model', model, 'lsPgModel'), field('Temperature', temperature, 'lsPgTemp'), field('Max tokens', maxTokens, 'lsPgMax'), jsonLabel);
        const run = el('button', 'ls-btn is-main', 'Run');
        run.type = 'button';
        run.prepend(icon('fas fa-play'));
        const reset = el('button', 'ls-btn', 'Reset to the original');
        reset.type = 'button';
        reset.addEventListener('click', function () {
            system.value = c.system_prompt || ''; user.value = c.user_prompt || ''; model.value = '';
            temperature.value = c.temperature ?? 0.7; maxTokens.value = c.max_tokens ?? 1200; json.checked = c.kind === 'json';
        });
        const bar = el('div', 'ls-fb-row');
        bar.append(run, reset, el('span', 'ls-sub', 'Runs on your account and counts as an AI call. Nothing changes in the app.'));
        const result = el('div', 'ls-pg-compare');
        const original = el('div', 'ls-pg-col');
        original.append(el('div', 'ls-section-label', 'Original answer'),
            message('AI', 'is-ai', c.output === null ? '' : (c.kind === 'json' ? pretty(c.output) : c.output), c.kind === 'json'));
        const fresh = el('div', 'ls-pg-col');
        fresh.append(el('div', 'ls-section-label', 'New answer'), el('p', 'ls-sub', 'Press Run to see it here.'));
        result.append(original, fresh);
        run.addEventListener('click', function () {
            run.disabled = true;
            fresh.textContent = '';
            fresh.append(el('div', 'ls-section-label', 'New answer'), el('p', 'ls-sub', 'Running...'));
            fetch('/api/admin/ai-calls/' + c.id + '/playground', {
                method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ system_prompt: system.value, user_prompt: user.value, model: model.value.trim(),
                                       temperature: temperature.value, max_tokens: maxTokens.value, json: json.checked }),
            }).then((r) => r.json()).then(function (r) {
                fresh.textContent = '';
                const head = el('div', 'ls-detail-head');
                head.append(el('span', 'ls-section-label', 'New answer'));
                if (r.success) {
                    const u = r.usage || {};
                    head.append(pill('Success', 'is-ok'), pill(seconds(r.latency_ms)),
                        pill(number(u.input_tokens) + ' → ' + number(u.output_tokens) + ' tokens'), pill(money(u.cost_usd)), pill(u.model || model.value || c.model || ''));
                    fresh.append(head, message('AI', 'is-ai', r.output, json.checked));
                } else {
                    head.append(pill('Error', 'is-error'), pill(seconds(r.latency_ms || 0)));
                    fresh.append(head, el('pre', 'ls-error-box', r.error || 'The run failed.'));
                }
                if (r.group_id) {
                    const open = el('button', 'ls-btn', 'Open this run\'s trace');
                    open.type = 'button';
                    open.addEventListener('click', function () { openDrawer(r.group_id, null, null); });
                    fresh.appendChild(open);
                }
            }).catch(function () {
                fresh.textContent = '';
                fresh.append(el('div', 'ls-section-label', 'New answer'), el('pre', 'ls-error-box', 'The run could not be sent. Please try again.'));
            }).finally(function () { run.disabled = false; });
        });
        wrap.append(field('System prompt', system, 'lsPgSystem'), field('User prompt', user, 'lsPgUser'), params, bar, result);
        panel.appendChild(wrap);
    }

    // ── Views ─────────────────────────────────────────────────────────
    function switchView(view) {
        state.view = view;
        document.querySelectorAll('.ls-tabs .ls-tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.view === view)));
        $('lsRuns').classList.toggle('d-none', view !== 'runs');
        $('lsTraces').classList.toggle('d-none', view !== 'traces');
        $('lsMonitor').classList.toggle('d-none', view !== 'monitor');
        refresh();
    }
    document.querySelectorAll('.ls-tabs .ls-tab').forEach(function (t) { t.addEventListener('click', function () { switchView(t.dataset.view); }); });
    function refresh() {
        renderChips();
        loadStats();
        if (state.view === 'runs') loadRuns();
        else if (state.view === 'traces') loadTraces();
    }
    // a filter change starts from the first page
    ['lsFilterApply', 'lsSearchForm', 'lsDays'].forEach(function (id) {
        $(id).addEventListener(id === 'lsSearchForm' ? 'submit' : id === 'lsDays' ? 'change' : 'click', function () {
            state.runsPage = 1;
            state.tracesPage = 1;
        }, true);
    });
    refresh();
})();
