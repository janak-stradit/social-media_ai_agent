// Autopilot page (templates/autopilot.html, services/autopilot_service.py).
// Until Autopilot is set up: the set-up form. Then the dashboard: status and
// Pause, what needs the user's OK, the plan for this week and next, and spend.
// Nothing is posted without the user's OK; a post not approved in time is held.
(function () {
    'use strict';

    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook' };
    const KIND_NAMES = { tips: 'Tip', offers: 'Product / offer', news: 'Industry news', festival: 'Festival' };
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const STATUS = {
        planned: { label: 'Planned', cls: 'planned' },
        drafting: { label: 'Being written', cls: 'planned' },
        needs_ok: { label: 'Needs your OK', cls: 'needs' },
        held: { label: 'Not approved in time', cls: 'held' },
        approved: { label: 'Scheduled', cls: 'approved' },
        posted: { label: 'Posted', cls: 'posted' },
        failed: { label: "Couldn't post", cls: 'held' },
        skipped: { label: 'Skipped', cls: 'skipped' },
    };
    const tz = -new Date().getTimezoneOffset();

    let data = null;        // the last /api/autopilot answer
    let editing = false;    // the set-up form is open over the dashboard
    let weekOffset = 0;     // 0 = this week, 1 = next week
    let languages = [{ code: 'en', name: 'English' }];
    let openPostId = null;

    // ── Helpers ──────────────────────────────────────────────────────────
    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) {
        const icons = { success: 'fa-check-circle text-success', error: 'fa-exclamation-circle text-danger', info: 'fa-info-circle text-info' };
        $('#toastBody').html(`<i class="fas ${icons[type] || icons.info} me-2"></i>${esc(msg)}`);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }
    const when = iso => new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
    const time = iso => new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const money = n => '$' + (Math.round(n * 100) / 100).toFixed(2);
    function api(method, url, body) {
        return $.ajax({ url, type: method, contentType: 'application/json', data: body ? JSON.stringify(body) : undefined });
    }
    function failed(xhr, fallback) {
        toast((xhr.responseJSON || {}).error || fallback, 'error');
    }

    // ── Load ─────────────────────────────────────────────────────────────
    function load() {
        $.getJSON('/api/autopilot?tz=' + tz).done(r => { data = r; render(); })
            .fail(xhr => $('#apMain').html(`<p class="ap-loading">${esc((xhr.responseJSON || {}).error || 'Autopilot could not be loaded.')}</p>`));
        $.getJSON('/api/languages?detected=').done(r => { languages = r.languages || languages; });
    }
    function render() {
        $('#apMain').attr('aria-busy', 'false');
        const s = data.settings;
        if (!s.configured || editing || (!s.enabled && !data.posts.length)) renderSetup();
        else renderDashboard();
    }

    // ── Set-up ───────────────────────────────────────────────────────────
    function renderSetup() {
        const s = data.settings;
        const firstTime = !s.configured;
        const perWeek = s.posts_per_week || 3;
        const mix = Object.keys(s.mix || {}).length ? s.mix : defaultMix(perWeek);
        const platforms = s.platforms.length ? s.platforms : ['linkedin'];
        const price = data.images.price || 0;
        $('#apMain').html(`
            <section class="ap-setup">
                <div class="ap-setup-head">
                    <h1>${firstTime ? 'Set up Autopilot' : 'Autopilot settings'}</h1>
                    <p>${firstTime ? 'Autopilot plans your posts, writes them two days ahead and asks for your OK. Nothing goes out until you approve it.'
                        : 'Changes apply to posts that haven’t been written yet.'}</p>
                </div>
                <form id="apSetup" novalidate>
                    <fieldset class="ap-card">
                        <legend><span class="ap-step">1</span>How often</legend>
                        <div class="ap-row">
                            <span class="ap-label" id="apPerWeekLabel">Posts a week</span>
                            <div class="ap-stepper" role="group" aria-labelledby="apPerWeekLabel">
                                <button type="button" data-step="-1" aria-label="Fewer posts">−</button>
                                <output id="apPerWeek">${perWeek}</output>
                                <button type="button" data-step="1" aria-label="More posts">+</button>
                            </div>
                        </div>
                        <div class="ap-row">
                            <span class="ap-label">Post to</span>
                            <div class="ap-chips">
                                ${Object.keys(NAMES).map(p => `<label class="ap-chip"><input type="checkbox" name="platform" value="${p}" ${platforms.includes(p) ? 'checked' : ''}><span><i class="fab ${ICONS[p]} me-1"></i>${NAMES[p]}</span></label>`).join('')}
                            </div>
                        </div>
                        <div class="ap-row">
                            <span class="ap-label">Days off</span>
                            <div class="ap-chips">
                                ${DAYS.map((d, i) => `<label class="ap-chip"><input type="checkbox" name="dayoff" value="${i}" ${(s.days_off || [6]).includes(i) ? 'checked' : ''}><span>${d}</span></label>`).join('')}
                            </div>
                        </div>
                        <p class="ap-hint">Each post goes out at the best time for its platform.</p>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">2</span>What about</legend>
                        <p class="ap-hint">Posts a week of each kind. Written from your brand profile.</p>
                        ${['tips', 'offers', 'news'].map(k => `
                        <div class="ap-row">
                            <span class="ap-label">${{ tips: 'Tips &amp; how-tos', offers: 'Your products &amp; offers', news: 'Industry news' }[k]}</span>
                            <div class="ap-stepper" role="group" aria-label="${k}">
                                <button type="button" data-mix="${k}" data-step="-1" aria-label="Fewer">−</button>
                                <output data-mix-value="${k}">${mix[k] || 0}</output>
                                <button type="button" data-mix="${k}" data-step="1" aria-label="More">+</button>
                            </div>
                        </div>`).join('')}
                        <label class="ap-check"><input type="checkbox" id="apFestivals" ${s.festivals !== false ? 'checked' : ''}> Festivals &amp; occasions as they come up (Diwali, Eid, Christmas…)</label>
                        <label class="ap-field">Talk about <small>(comma-separated)</small>
                            <input type="text" id="apTopics" value="${esc((s.topics || []).join(', '))}" placeholder="e.g. SIP investing, retirement planning"></label>
                        <label class="ap-field">Never post about
                            <input type="text" id="apAvoid" value="${esc((s.avoid || []).join(', '))}" placeholder="e.g. politics, competitor names"></label>
                        <label class="ap-field">Language
                            <select id="apLanguage">${languages.map(l => `<option value="${esc(l.code)}" ${l.code === (s.language || 'en') ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select></label>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">3</span>How much control</legend>
                        <label class="ap-mode on"><input type="radio" name="mode" value="approve" checked>
                            <span><strong>I approve every post</strong><small>It plans and writes. Nothing goes out until you press Approve. Not approved by the deadline? It’s held, never posted.</small></span></label>
                        <label class="ap-mode off"><input type="radio" name="mode" value="quiet" disabled>
                            <span><strong>Post unless I stop it</strong><small>Coming later: unlocks after 10 approvals without edits.</small></span></label>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">4</span>Images &amp; budget</legend>
                        <label class="ap-check"><input type="checkbox" id="apImages" ${s.images !== false ? 'checked' : ''}> Make an image for each post (your logo is placed on it)</label>
                        <div class="ap-row">
                            <label class="ap-label" for="apCap">At most this many images a month</label>
                            <input type="number" id="apCap" min="0" max="200" value="${s.image_cap_month ?? 12}" class="ap-number">
                        </div>
                        <p class="ap-hint" id="apCapHint"></p>
                    </fieldset>

                    <div class="ap-setup-actions">
                        ${firstTime ? '' : '<button type="button" class="ap-btn" id="apCancelSetup">Cancel</button>'}
                        <button type="submit" class="ap-btn primary">${firstTime || !s.enabled ? 'Turn on Autopilot' : 'Save changes'}</button>
                    </div>
                    <p class="ap-error" id="apSetupError" role="alert"></p>
                </form>
            </section>`);
        updateCapHint(price);
    }
    function defaultMix(n) {
        const mix = { tips: 0, offers: 0, news: 0 };
        const order = ['tips', 'offers', 'tips', 'news', 'offers', 'tips', 'news'];
        for (let i = 0; i < n; i++) mix[order[i % order.length]] += 1;
        return mix;
    }
    function updateCapHint(price) {
        const cap = Math.max(0, parseInt($('#apCap').val(), 10) || 0);
        const perMonth = Math.round((parseInt($('#apPerWeek').text(), 10) || 0) * 4.3);
        const images = $('#apImages').is(':checked') ? Math.min(cap, perMonth) : 0;
        $('#apCapHint').text(images
            ? `About ${perMonth} posts a month, so up to ${images} images${price ? ' (about ' + money(images * price) + ')' : ''}. After that, posts go out as text only.`
            : 'Posts go out as text only.');
    }
    $(document).on('click', '#apSetup [data-step]', function () {
        const step = Number($(this).data('step'));
        const kind = $(this).data('mix');
        const $out = kind ? $(`[data-mix-value="${kind}"]`) : $('#apPerWeek');
        const value = Math.max(kind ? 0 : 1, Math.min(7, (parseInt($out.text(), 10) || 0) + step));
        $out.text(value);
        if (!kind) {  // a new total: an even split, so the mix always adds up
            const mix = defaultMix(value);
            Object.keys(mix).forEach(k => $(`[data-mix-value="${k}"]`).text(mix[k]));
        }
        updateCapHint(data.images.price || 0);
    });
    $(document).on('input change', '#apCap, #apImages', () => updateCapHint(data.images.price || 0));
    $(document).on('click', '#apCancelSetup', () => { editing = false; render(); });
    $(document).on('submit', '#apSetup', function (e) {
        e.preventDefault();
        const body = {
            enabled: true,
            posts_per_week: parseInt($('#apPerWeek').text(), 10),
            platforms: $('input[name=platform]:checked').map((_, el) => el.value).get(),
            days_off: $('input[name=dayoff]:checked').map((_, el) => Number(el.value)).get(),
            mix: { tips: +$('[data-mix-value=tips]').text(), offers: +$('[data-mix-value=offers]').text(), news: +$('[data-mix-value=news]').text() },
            festivals: $('#apFestivals').is(':checked'),
            topics: $('#apTopics').val(), avoid: $('#apAvoid').val(), language: $('#apLanguage').val(),
            images: $('#apImages').is(':checked'), image_cap_month: parseInt($('#apCap').val(), 10) || 0,
            tz_minutes: tz,
        };
        const $btn = $(this).find('[type=submit]').prop('disabled', true);
        api('PUT', '/api/autopilot/settings', body).done(r => {
            data = r; editing = false;
            toast(r.planned ? `Autopilot is on. ${r.planned} posts planned for the next two weeks.` : 'Saved.', 'success');
            render();
        }).fail(xhr => {
            $('#apSetupError').text((xhr.responseJSON || {}).error || 'Could not save.');
            $btn.prop('disabled', false);
        });
    });

    // ── Dashboard ─────────────────────────────────────────────────────────
    function renderDashboard() {
        const s = data.settings;
        const on = s.enabled && !s.paused;
        const next = data.next;
        const held = data.needs_ok.filter(p => p.status === 'held');
        const waiting = data.needs_ok.filter(p => p.status === 'needs_ok');
        const pct = data.week.goal ? Math.min(100, Math.round(data.week.done / data.week.goal * 100)) : 0;
        const imgPct = data.images.cap ? Math.min(100, Math.round(data.images.used / data.images.cap * 100)) : 0;
        $('#apMain').html(`
            <section class="ap-status ${on ? 'is-on' : 'is-off'}" aria-label="Autopilot status">
                <span class="ap-dot" aria-hidden="true"></span>
                <div class="ap-status-text">
                    <h1>${on ? 'Autopilot is on' : s.enabled ? 'Autopilot is paused' : 'Autopilot is off'}</h1>
                    <p>${on ? (next ? `Next post: ${esc(when(next.slot_at))} on ${NAMES[next.platform]}` : 'Planning your next posts')
                            + ` · ${s.posts_per_week} posts a week · you approve each one`
                        : 'Nothing will be written or posted until you resume. Posts you already approved stay on the calendar.'}</p>
                </div>
                <button type="button" class="ap-btn" id="apEditSettings"><i class="fas fa-sliders me-1" aria-hidden="true"></i>Settings</button>
                ${on ? '<button type="button" class="ap-btn dark" data-switch="pause">Pause</button>'
                     : '<button type="button" class="ap-btn primary" data-switch="resume">Resume</button>'}
            </section>

            ${held.length ? `<section class="ap-alert" role="status">
                <div><strong>${held.length === 1 ? 'A post wasn’t approved in time, so it didn’t go out' : held.length + ' posts weren’t approved in time, so they didn’t go out'}</strong>
                <span>You can still post ${held.length === 1 ? 'it' : 'them'}: open ${held.length === 1 ? 'it' : 'one'} below.</span></div></section>` : ''}

            <div class="ap-grid">
                <div class="ap-col">
                    <section class="ap-card ap-needs" aria-labelledby="apNeedsTitle">
                        <div class="ap-card-head">
                            <h2 id="apNeedsTitle">Needs your OK ${data.needs_ok.length ? `<span class="ap-count">${data.needs_ok.length}</span>` : ''}</h2>
                            <span class="ap-hint">Posts are written 2 days ahead. Not approved in time? Held, never posted.</span>
                        </div>
                        ${data.needs_ok.length ? data.needs_ok.map(needsCard).join('')
                            : `<p class="ap-empty"><i class="fas fa-check-circle me-2" aria-hidden="true"></i>Nothing needs you right now.${waiting.length ? '' : ' The next post will show up here when it’s written.'}</p>`}
                    </section>

                    <section class="ap-card" aria-labelledby="apPlanTitle">
                        <div class="ap-card-head">
                            <h2 id="apPlanTitle">The plan</h2>
                            <div class="ap-tabs" role="tablist" aria-label="Week">
                                <button type="button" role="tab" data-week="0" aria-selected="${weekOffset === 0}">This week</button>
                                <button type="button" role="tab" data-week="1" aria-selected="${weekOffset === 1}">Next week</button>
                            </div>
                        </div>
                        <div class="ap-week">${weekColumns()}</div>
                        <p class="ap-hint">Click a post to see why it was chosen. Approved posts are also on your <a href="/calendar">Content Calendar</a>.</p>
                    </section>
                </div>

                <aside class="ap-col ap-side">
                    <section class="ap-card">
                        <h2>This week</h2>
                        <p class="ap-big">${data.week.done} <span>of ${data.week.goal} posts out</span></p>
                        <div class="ap-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
                        <p class="ap-hint">${data.week.planned} planned this week</p>
                    </section>
                    <section class="ap-card">
                        <h2>Images this month</h2>
                        <p class="ap-big">${data.images.used} <span>of ${data.images.cap}</span></p>
                        <div class="ap-bar dark" aria-hidden="true"><span style="width:${imgPct}%"></span></div>
                        <p class="ap-hint">${data.images.price && data.images.used ? 'About ' + money(data.images.used * data.images.price) + ' so far. ' : ''}At the limit, posts go out as text only.</p>
                    </section>
                    <section class="ap-card">
                        <h2>Last 30 days</h2>
                        <div class="ap-stats">
                            <div><strong>${data.last30.posted}</strong><span>posted</span></div>
                            <div><strong>${data.last30.approved}</strong><span>approved</span></div>
                            <div><strong>${data.last30.skipped}</strong><span>skipped or missed</span></div>
                        </div>
                    </section>
                    ${s.enabled ? '<button type="button" class="ap-link" data-switch="turn-off">Turn Autopilot off</button>' : ''}
                </aside>
            </div>`);
    }

    function needsCard(p) {
        const late = p.status === 'held';
        const issues = (p.checks || []).filter(c => !c.ok);
        return `
            <article class="ap-needs-card${late ? ' is-held' : ''}">
                ${p.image_url ? `<img src="${esc(p.image_url)}" alt="" class="ap-thumb">` : `<span class="ap-thumb ap-thumb-text" aria-hidden="true"><i class="fab ${ICONS[p.platform]}"></i></span>`}
                <div class="ap-needs-text">
                    <p class="ap-meta"><strong class="ap-p-${p.platform}">${NAMES[p.platform]}</strong> · ${esc(when(p.slot_at))}
                        · ${late ? '<span class="ap-late">Wasn’t approved in time</span>' : `<span class="ap-due">OK needed by ${esc(when(p.approve_by))}</span>`}</p>
                    <p class="ap-caption">${esc(p.caption || '')}</p>
                    <p class="ap-tags">
                        <span class="ap-tag why">Why: ${esc(p.reason || KIND_NAMES[p.kind])}</span>
                        ${issues.length ? `<span class="ap-tag warn">${issues.length} thing${issues.length > 1 ? 's' : ''} to check</span>` : '<span class="ap-tag ok">Checks passed</span>'}
                    </p>
                </div>
                <div class="ap-needs-actions">
                    <button type="button" class="ap-btn primary" data-open="${p.id}">${issues.length || late ? 'Review' : 'Review &amp; approve'}</button>
                    <button type="button" class="ap-link" data-act="skip" data-id="${p.id}">Skip</button>
                </div>
            </article>`;
    }

    function weekColumns() {
        const start = new Date(data.week.start);
        start.setDate(start.getDate() + weekOffset * 7);
        const today = new Date().toDateString();
        let html = '';
        for (let i = 0; i < 7; i++) {
            const day = new Date(start); day.setDate(start.getDate() + i);
            const posts = data.posts.filter(p => new Date(p.slot_at).toDateString() === day.toDateString());
            const off = (data.settings.days_off || []).includes((day.getDay() + 6) % 7);
            html += `<div class="ap-day${day.toDateString() === today ? ' is-today' : ''}">
                <p class="ap-day-name">${DAYS[i]} ${day.getDate()}${day.toDateString() === today ? ' · today' : ''}</p>
                ${posts.map(p => `<button type="button" class="ap-slot ${STATUS[p.status].cls}" data-open="${p.id}">
                    <span class="ap-slot-time">${esc(time(p.slot_at))} · ${NAMES[p.platform]}</span>
                    <span class="ap-slot-title">${esc(p.topic && p.status !== 'planned' ? p.topic : KIND_NAMES[p.kind] + (p.kind === 'festival' && p.topic ? ': ' + p.topic : ''))}</span>
                    <span class="ap-slot-state">${STATUS[p.status].label}</span></button>`).join('')}
                ${posts.length ? '' : `<p class="ap-rest">${off ? 'Day off' : '—'}</p>`}
            </div>`;
        }
        return html;
    }

    $(document).on('click', '#apEditSettings', () => { editing = true; render(); });
    $(document).on('click', '[data-week]', function () { weekOffset = Number($(this).data('week')); renderDashboard(); });
    $(document).on('click', '[data-switch]', function () {
        const action = $(this).data('switch');
        if (action === 'turn-off' && !window.confirm('Turn Autopilot off? Planned posts stop; posts you approved stay on the calendar.')) return;
        api('POST', '/api/autopilot/' + action).done(r => {
            data = r; render();
            toast({ pause: 'Autopilot paused.', resume: 'Autopilot is on again.', 'turn-off': 'Autopilot is off.' }[action], 'success');
        }).fail(xhr => failed(xhr, 'That didn’t work. Please try again.'));
    });

    // ── One post ──────────────────────────────────────────────────────────
    function postById(id) { return data.posts.find(p => p.id === Number(id)); }
    function openPost(id) {
        const p = postById(id);
        if (!p) return;
        openPostId = p.id;
        const editable = p.status === 'needs_ok' || p.status === 'held';
        $('#apPostTitle').text(`${NAMES[p.platform]} · ${when(p.slot_at)}`);
        $('#apPostSub').text(p.status === 'needs_ok' ? `Needs your OK by ${when(p.approve_by)}` : STATUS[p.status].label);
        const checks = (p.checks || []).map(c => `<div class="ap-check-item ${c.ok ? 'ok' : 'warn'}"><strong>${esc(c.label)}</strong><span>${esc(c.note)}</span></div>`).join('');
        $('#apPostBody').html(p.status === 'planned' || p.status === 'drafting' ? `
            <div class="ap-why"><strong>Planned</strong><p>${esc(KIND_NAMES[p.kind])}${p.kind === 'festival' && p.topic ? ': ' + esc(p.topic) : ''}. ${esc(p.reason || '')}.</p>
            <p>It will be written on ${esc(when(new Date(new Date(p.slot_at).getTime() - 48 * 3600 * 1000).toISOString()))} and you’ll be asked for your OK.</p></div>` : `
            <div class="ap-post${p.image_url ? '' : ' no-img'}">
                ${p.image_url ? `<img src="${esc(p.image_url)}" alt="The post's image" class="ap-post-img">` : ''}
                <div class="ap-post-text">
                    <label class="ap-field">Caption
                        <textarea id="apCaption" rows="8" ${editable ? '' : 'readonly'}>${esc(p.caption || '')}</textarea></label>
                    <label class="ap-field">Hashtags
                        <input type="text" id="apHashtags" value="${esc((p.hashtags || []).join(' '))}" ${editable ? '' : 'readonly'}></label>
                    <div class="ap-why"><strong>Why this post</strong><p>${esc(p.reason || KIND_NAMES[p.kind])}.</p></div>
                    ${checks ? `<div class="ap-checks">${checks}</div>` : ''}
                    ${p.error ? `<p class="ap-error">${esc(p.error)}</p>` : ''}
                </div>
            </div>`);
        const footer = [];
        if (p.status === 'planned') footer.push('<button type="button" class="btn btn-primary" data-act="write-now">Write it now</button>');
        if (editable) {
            footer.push('<button type="button" class="ap-link" data-act="skip">Skip this one</button>');
            footer.push(`<button type="button" class="btn btn-primary" data-act="approve">${p.status === 'held' ? 'Post it now' : 'Approve for ' + esc(when(p.slot_at))}</button>`);
        }
        if (!footer.length) footer.push('<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>');
        $('#apPostFooter').html(footer.join(''));
        bootstrap.Modal.getOrCreateInstance(document.getElementById('apPostModal')).show();
    }
    $(document).on('click', '[data-open]', function () { openPost($(this).data('open')); });

    $(document).on('click', '[data-act]', function () {
        const act = $(this).data('act');
        const id = $(this).data('id') || openPostId;
        const body = {};
        if (act === 'approve' && $('#apCaption').length) {
            body.caption = $('#apCaption').val();
            body.hashtags = ($('#apHashtags').val() || '').split(/[\s,]+/).filter(Boolean);
        }
        const $btn = $(this).prop('disabled', true);
        if (act === 'write-now') $btn.html('<i class="fas fa-spinner fa-spin me-1"></i>Writing…');
        api('POST', `/api/autopilot/posts/${id}/${act}`, body).done(() => {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('apPostModal')).hide();
            toast({ approve: 'Approved. It’s on your calendar.', skip: 'Skipped.', 'write-now': 'Written. Have a look.' }[act], 'success');
            $.getJSON('/api/autopilot?tz=' + tz).done(r => { data = r; render(); if (act === 'write-now') openPost(id); });
        }).fail(xhr => { failed(xhr, 'That didn’t work. Please try again.'); $btn.prop('disabled', false); });
    });

    $(load);
})();
