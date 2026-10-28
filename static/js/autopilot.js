// Autopilot page (templates/autopilot.html; /api/autopilot, services/autopilot_service.py).
// Until Autopilot is set up: the set-up form. Then the dashboard: status and
// Pause, posts waiting for approval (reviewed on the usual /approve page), the
// plan for this week and next, and spend. Nothing is published until a post is
// approved; one not approved before its time expires.
(function () {
    'use strict';

    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook' };
    const KIND_NAMES = { tips: 'Tip', offers: 'Product / offer', news: 'Industry news', festival: 'Festival' };
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const STATUS = {
        planned: { label: 'Planned', cls: 'planned' },
        preparing: { label: 'Being written', cls: 'planned' },
        awaiting_approval: { label: 'Needs approval', cls: 'needs' },
        scheduled: { label: 'Scheduled', cls: 'approved' },
        posted: { label: 'Posted', cls: 'posted' },
        expired: { label: 'Not approved in time', cls: 'held' },
        failed: { label: "Couldn't be prepared", cls: 'held' },
        rejected: { label: 'Skipped', cls: 'skipped' },
        cancelled: { label: 'Withdrawn', cls: 'skipped' },
    };
    const tz = -new Date().getTimezoneOffset();

    let data = null;        // /api/autopilot -> autopilot
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
    const platformNames = p => (p.platforms || []).map(x => NAMES[x] || x).join(' + ');
    const titleOf = p => p.idea_title || KIND_NAMES[p.kind] || 'Post';
    function api(method, url, body) {
        return $.ajax({ url, type: method, contentType: 'application/json', data: body ? JSON.stringify(body) : undefined });
    }
    function failed(xhr, fallback) { toast((xhr.responseJSON || {}).error || fallback, 'error'); }
    function save(body, message) {
        return api('PUT', '/api/autopilot', { ...body, tz_minutes: tz }).done(r => {
            data = r.autopilot; editing = false; render();
            if (message) toast(message, 'success');
        });
    }

    // ── Load ─────────────────────────────────────────────────────────────
    function load() {
        return $.getJSON('/api/autopilot').done(r => { data = r.autopilot; render(); })
            .fail(xhr => $('#apMain').html(`<p class="ap-loading">${esc((xhr.responseJSON || {}).error || 'Autopilot could not be loaded.')}</p>`));
    }
    function render() {
        $('#apMain').attr('aria-busy', 'false');
        if (!data.enabled || editing) renderSetup(); else renderDashboard();
    }

    // ── Set-up ───────────────────────────────────────────────────────────
    function renderSetup() {
        const s = data;
        const firstTime = !s.enabled;
        const perWeek = s.posts_per_week || 3;
        const mix = Object.keys(s.mix || {}).length ? s.mix : defaultMix(perWeek);
        const platforms = s.platforms.length ? s.platforms : ['linkedin'];
        $('#apMain').html(`
            <section class="ap-setup">
                <div class="ap-setup-head">
                    <h1>${firstTime ? 'Set up Autopilot' : 'Autopilot settings'}</h1>
                    <p>${firstTime ? 'Autopilot plans your posts, writes each one a couple of days ahead and sends it to you for approval. Nothing is published until it’s approved.'
                        : 'Changes apply to the days that haven’t been written yet.'}</p>
                </div>
                ${s.enabled && s.problems.length ? `<p class="ap-alert">${esc(s.problems[0])}</p>` : ''}
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
                            <div class="ap-chips" id="apPlatforms">${platformChips(platforms, s.images)}</div>
                        </div>
                        <div class="ap-row">
                            <span class="ap-label">Days off</span>
                            <div class="ap-chips">
                                ${DAYS.map((d, i) => `<label class="ap-chip"><input type="checkbox" name="dayoff" value="${i}" ${(s.days_off || []).includes(i) ? 'checked' : ''}><span>${d}</span></label>`).join('')}
                            </div>
                        </div>
                        <div class="ap-row">
                            <label class="ap-label" for="apDaysAhead">Write each post</label>
                            <select id="apDaysAhead" class="ap-select">
                                ${[1, 2, 3, 5, 7].map(n => `<option value="${n}" ${n === s.days_ahead ? 'selected' : ''}>${n} day${n > 1 ? 's' : ''} ahead</option>`).join('')}
                            </select>
                        </div>
                        <p class="ap-hint">Posts go out at your best posting time.</p>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">2</span>What about</legend>
                        <p class="ap-hint">Posts a week of each kind, written from your brand profile.</p>
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
                        <label class="ap-field">Talk about <small>(comma-separated; else ideas trending for you)</small>
                            <input type="text" id="apTopics" value="${esc((s.topics || []).join(', '))}" placeholder="e.g. SIP investing, retirement planning"></label>
                        <label class="ap-field">Never post about
                            <input type="text" id="apAvoid" value="${esc((s.avoid || []).join(', '))}" placeholder="e.g. politics, competitor names"></label>
                        <label class="ap-field">Language
                            <select id="apLanguage">${languages.map(l => `<option value="${esc(l.code)}" ${l.code === (s.language || 'en') ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select></label>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">3</span>How much control</legend>
                        <label class="ap-mode on"><input type="radio" name="mode" value="approve" checked>
                            <span><strong>Every post needs approval</strong><small>Each post is sent to you (or your reviewer) to approve. Not approved before its time? It isn’t published.</small></span></label>
                        <label class="ap-mode off"><input type="radio" name="mode" value="quiet" disabled>
                            <span><strong>Post unless I stop it</strong><small>Coming later.</small></span></label>
                    </fieldset>

                    <fieldset class="ap-card">
                        <legend><span class="ap-step">4</span>Images &amp; budget</legend>
                        <label class="ap-check"><input type="checkbox" id="apImages" ${s.images ? 'checked' : ''}> Make an image for each post (with your logo). Needed for Instagram.</label>
                        <div class="ap-row">
                            <label class="ap-label" for="apCap">At most this many images a month</label>
                            <input type="number" id="apCap" min="0" max="200" value="${s.image_cap_month ?? 12}" class="ap-number">
                        </div>
                        <p class="ap-hint" id="apCapHint"></p>
                    </fieldset>

                    <div class="ap-setup-actions">
                        ${firstTime ? '' : '<button type="button" class="ap-btn" id="apCancelSetup">Cancel</button>'}
                        <button type="submit" class="ap-btn primary">${firstTime ? 'Turn on Autopilot' : 'Save changes'}</button>
                    </div>
                    <p class="ap-error" id="apSetupError" role="alert"></p>
                </form>
            </section>`);
        updateCapHint();
    }
    function platformChips(chosen, images) {
        return data.platform_choices.map(p => {
            const off = p.id === 'instagram' && !images;
            return `<label class="ap-chip${off ? ' is-off' : ''}" title="${off ? 'Instagram needs an image: turn on images below' : (p.connected ? '' : 'Not connected yet')}">
                <input type="checkbox" name="platform" value="${p.id}" ${chosen.includes(p.id) && !off ? 'checked' : ''} ${off ? 'disabled' : ''}>
                <span><i class="fab ${ICONS[p.id]} me-1"></i>${NAMES[p.id]}${p.connected ? '' : ' <small>(not connected)</small>'}</span></label>`;
        }).join('');
    }
    function defaultMix(n) {
        const mix = { tips: 0, offers: 0, news: 0 };
        const order = ['tips', 'offers', 'tips', 'news', 'offers', 'tips', 'news'];
        for (let i = 0; i < n; i++) mix[order[i % order.length]] += 1;
        return mix;
    }
    function updateCapHint() {
        const price = (data.image_use || {}).price || 0;
        const cap = Math.max(0, parseInt($('#apCap').val(), 10) || 0);
        const perMonth = Math.round((parseInt($('#apPerWeek').text(), 10) || 0) * 4.3);
        const images = $('#apImages').is(':checked') ? Math.min(cap, perMonth) : 0;
        $('#apCap').prop('disabled', !$('#apImages').is(':checked'));
        $('#apCapHint').text(images
            ? `About ${perMonth} posts a month, so up to ${images} images${price ? ' (about ' + money(images * price) + ')' : ''}. After that, posts are text only.`
            : 'Text-only posts use the least credit.');
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
        updateCapHint();
    });
    $(document).on('change', '#apImages', function () {
        const chosen = $('input[name=platform]:checked').map((_, el) => el.value).get();
        $('#apPlatforms').html(platformChips(chosen, this.checked));
        updateCapHint();
    });
    $(document).on('input change', '#apCap', updateCapHint);
    $(document).on('click', '#apCancelSetup', () => { editing = false; render(); });
    $(document).on('submit', '#apSetup', function (e) {
        e.preventDefault();
        const body = {
            enabled: true,
            posts_per_week: parseInt($('#apPerWeek').text(), 10),
            platforms: $('input[name=platform]:checked').map((_, el) => el.value).get(),
            days_off: $('input[name=dayoff]:checked').map((_, el) => Number(el.value)).get(),
            days_ahead: Number($('#apDaysAhead').val()),
            mix: { tips: +$('[data-mix-value=tips]').text(), offers: +$('[data-mix-value=offers]').text(), news: +$('[data-mix-value=news]').text() },
            festivals: $('#apFestivals').is(':checked'),
            topics: $('#apTopics').val(), avoid: $('#apAvoid').val(), language: $('#apLanguage').val(),
            images: $('#apImages').is(':checked'), image_cap_month: parseInt($('#apCap').val(), 10) || 0,
        };
        const firstTime = !data.enabled;
        const $btn = $(this).find('[type=submit]').prop('disabled', true);
        save(body, firstTime ? 'Autopilot is on. Your first posts are written within the hour.' : 'Saved.')
            .fail(xhr => {
                $('#apSetupError').text((xhr.responseJSON || {}).error || 'Could not save.');
                $btn.prop('disabled', false);
            });
    });

    // ── Dashboard ─────────────────────────────────────────────────────────
    function renderDashboard() {
        const s = data;
        const paused = s.paused_reason || (s.problems || [])[0];
        const on = !paused;
        const next = s.next;
        const pct = s.week.goal ? Math.min(100, Math.round(s.week.done / s.week.goal * 100)) : 0;
        const use = s.image_use;
        const imgPct = s.images && use.cap ? Math.min(100, Math.round(use.used / use.cap * 100)) : 0;
        $('#apMain').html(`
            <section class="ap-status ${on ? 'is-on' : 'is-off'}" aria-label="Autopilot status">
                <span class="ap-dot" aria-hidden="true"></span>
                <div class="ap-status-text">
                    <h1>${on ? 'Autopilot is on' : 'Autopilot is paused'}</h1>
                    <p>${on ? (next ? `Next post: ${esc(when(next.slot_at))} on ${esc(platformNames(next))}` : 'Planning your next posts')
                            + ` · ${s.posts_per_week} post${s.posts_per_week > 1 ? 's' : ''} a week · each needs approval`
                        : esc(paused) + ' Posts already approved stay on the calendar.'}</p>
                </div>
                <button type="button" class="ap-btn" id="apEditSettings"><i class="fas fa-sliders me-1" aria-hidden="true"></i>Settings</button>
                ${on ? '<button type="button" class="ap-btn" id="apRunNow"><i class="fas fa-wand-magic-sparkles me-1" aria-hidden="true"></i>Prepare now</button><button type="button" class="ap-btn dark" data-pause="1">Pause</button>'
                     : '<button type="button" class="ap-btn primary" data-pause="0">Resume</button>'}
            </section>

            <div class="ap-grid">
                <div class="ap-col">
                    <section class="ap-card ap-needs" aria-labelledby="apNeedsTitle">
                        <div class="ap-card-head">
                            <h2 id="apNeedsTitle">Needs approval ${s.needs_ok.length ? `<span class="ap-count">${s.needs_ok.length}</span>` : ''}</h2>
                            <span class="ap-hint">Written ${s.days_ahead} day${s.days_ahead > 1 ? 's' : ''} ahead. Not approved before its time? It isn’t published.</span>
                        </div>
                        ${s.needs_ok.length ? s.needs_ok.map(needsCard).join('')
                            : '<p class="ap-empty"><i class="fas fa-check-circle me-2" aria-hidden="true"></i>Nothing needs you right now. The next post shows up here when it’s written.</p>'}
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
                        <p class="ap-big">${s.week.done} <span>of ${s.week.goal} posts out</span></p>
                        <div class="ap-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
                        <p class="ap-hint">${s.week.planned} planned this week</p>
                    </section>
                    <section class="ap-card">
                        <h2>Images this month</h2>
                        <p class="ap-big">${use.used} <span>of ${s.images ? use.cap : 0}</span></p>
                        <div class="ap-bar dark" aria-hidden="true"><span style="width:${imgPct}%"></span></div>
                        <p class="ap-hint">${use.price && use.used ? 'About ' + money(use.used * use.price) + ' so far. ' : ''}${s.images ? 'At the limit, posts are text only.' : 'Images are off: posts are text only.'}</p>
                    </section>
                    <section class="ap-card">
                        <h2>Last 30 days</h2>
                        <div class="ap-stats">
                            <div><strong>${s.last30.posted}</strong><span>posted</span></div>
                            <div><strong>${s.last30.approved}</strong><span>approved</span></div>
                            <div><strong>${s.last30.skipped}</strong><span>skipped or missed</span></div>
                        </div>
                    </section>
                    <button type="button" class="ap-link" id="apTurnOff">Turn Autopilot off</button>
                </aside>
            </div>`);
    }
    function needsCard(p) {
        return `
            <article class="ap-needs-card">
                ${p.image_url ? `<img src="${esc(p.image_url)}" alt="" class="ap-thumb">` : `<span class="ap-thumb ap-thumb-text" aria-hidden="true"><i class="fab ${ICONS[(p.platforms || [])[0]] || 'fa-linkedin'}"></i></span>`}
                <div class="ap-needs-text">
                    <p class="ap-meta"><strong>${esc(platformNames(p))}</strong> · ${esc(when(p.slot_at))} · <span class="ap-due">approve before then</span></p>
                    <p class="ap-caption">${esc(p.caption || titleOf(p))}</p>
                    <p class="ap-tags"><span class="ap-tag why">Why: ${esc(p.reason || KIND_NAMES[p.kind] || 'on your plan')}</span></p>
                </div>
                <div class="ap-needs-actions">
                    <a class="ap-btn primary" href="/approve/${p.approval_request_id}">Review &amp; approve</a>
                    <button type="button" class="ap-link" data-skip="${p.id}">Skip</button>
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
            const posts = data.plan.filter(p => new Date(p.slot_at).toDateString() === day.toDateString());
            const off = (data.days_off || []).includes(i);
            html += `<div class="ap-day${day.toDateString() === today ? ' is-today' : ''}">
                <p class="ap-day-name">${DAYS[i]} ${day.getDate()}${day.toDateString() === today ? ' · today' : ''}</p>
                ${posts.map(p => `<button type="button" class="ap-slot ${(STATUS[p.status] || STATUS.planned).cls}" data-open="${p.id}">
                    <span class="ap-slot-time">${esc(time(p.slot_at))} · ${esc(platformNames(p))}</span>
                    <span class="ap-slot-title">${esc(p.status === 'planned' ? (KIND_NAMES[p.kind] || 'Post') + (p.kind === 'festival' && p.idea_title ? ': ' + p.idea_title : '') : titleOf(p))}</span>
                    <span class="ap-slot-state">${(STATUS[p.status] || { label: p.status }).label}</span></button>`).join('')}
                ${posts.length ? '' : `<p class="ap-rest">${off ? 'Day off' : '—'}</p>`}
            </div>`;
        }
        return html;
    }

    $(document).on('click', '#apEditSettings', () => { editing = true; render(); });
    $(document).on('click', '[data-week]', function () { weekOffset = Number($(this).data('week')); renderDashboard(); });
    $(document).on('click', '[data-pause]', function () {
        const pause = $(this).data('pause') === 1;
        save({ paused: pause }, pause ? 'Autopilot paused.' : 'Autopilot is on again.').fail(xhr => failed(xhr, 'That didn’t work. Please try again.'));
    });
    $(document).on('click', '#apTurnOff', function () {
        if (!window.confirm('Turn Autopilot off? Posts waiting for approval are withdrawn; posts already approved stay on the calendar.')) return;
        save({ enabled: false }, 'Autopilot is off.').fail(xhr => failed(xhr, 'That didn’t work. Please try again.'));
    });
    $(document).on('click', '#apRunNow', function () {
        const $btn = $(this).prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Preparing…');
        $.post('/api/autopilot/run').done(r => {
            data = r.autopilot; render();
            toast(r.prepared ? `Prepared ${r.prepared} post${r.prepared === 1 ? '' : 's'} for approval.`
                : 'Nothing new to prepare yet: posts are written ' + data.days_ahead + ' day(s) ahead.', r.prepared ? 'success' : 'info');
        }).fail(xhr => { failed(xhr, 'Could not prepare posts.'); $btn.prop('disabled', false); });
    });

    // ── One post ──────────────────────────────────────────────────────────
    function openPost(id) {
        const p = data.plan.find(x => x.id === Number(id));
        if (!p) return;
        openPostId = p.id;
        const st = STATUS[p.status] || { label: p.status };
        $('#apPostTitle').text(`${platformNames(p)} · ${when(p.slot_at)}`);
        $('#apPostSub').text(st.label);
        const writeOn = new Date(new Date(p.slot_at).getTime() - data.days_ahead * 24 * 3600 * 1000);
        $('#apPostBody').html(`
            <div class="ap-post${p.image_url ? '' : ' no-img'}">
                ${p.image_url ? `<img src="${esc(p.image_url)}" alt="The post's image" class="ap-post-img">` : ''}
                <div class="ap-post-text">
                    ${p.caption ? `<p class="ap-post-caption">${esc(p.caption)}</p>` : ''}
                    <div class="ap-why"><strong>Why this post</strong><p>${esc(KIND_NAMES[p.kind] || 'Post')}${p.kind === 'festival' && p.idea_title ? ': ' + esc(p.idea_title) : ''}. ${esc(p.reason || '')}.</p>
                    ${p.status === 'planned' ? `<p>It will be written around ${esc(when(writeOn.toISOString()))} and sent to you for approval.</p>` : ''}</div>
                    ${p.error && p.status !== 'rejected' ? `<p class="ap-error">${esc(p.error)}</p>` : ''}
                </div>
            </div>`);
        const footer = [];
        if (p.status === 'planned' || p.status === 'awaiting_approval') footer.push(`<button type="button" class="ap-link" data-skip="${p.id}">Skip this one</button>`);
        if (p.status === 'awaiting_approval' || (p.status === 'expired' && p.approval_request_id)) {
            footer.push(`<a class="btn btn-primary" href="/approve/${p.approval_request_id}">${p.status === 'expired' ? 'Open it to post it now' : 'Review &amp; approve'}</a>`);
        }
        if (p.status === 'scheduled' || p.status === 'posted') footer.push('<a class="btn btn-outline-secondary" href="/calendar">On the calendar</a>');
        if (!footer.length || p.status === 'planned') footer.push('<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>');
        $('#apPostFooter').html(footer.join(''));
        bootstrap.Modal.getOrCreateInstance(document.getElementById('apPostModal')).show();
    }
    $(document).on('click', '[data-open]', function () { openPost($(this).data('open')); });
    $(document).on('click', '[data-skip]', function () {
        const id = $(this).data('skip') || openPostId;
        const $btn = $(this).prop('disabled', true);
        api('POST', `/api/autopilot/posts/${id}/skip`).done(r => {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('apPostModal')).hide();
            data = r.autopilot; render(); toast('Skipped.', 'success');
        }).fail(xhr => { failed(xhr, 'That didn’t work. Please try again.'); $btn.prop('disabled', false); });
    });

    $.getJSON('/api/languages').done(r => { languages = r.languages || languages; if (data && (editing || !data.enabled)) render(); });
    $(load);
})();
