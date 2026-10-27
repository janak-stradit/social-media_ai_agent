// Studio Chat "Send for approval" and "Schedule": one dialog for a whole post -
// a section per platform with its caption, hashtags and media (one image, or a
// carousel of slides picked from this conversation's images or the library;
// LinkedIn carousels as a multi-image post or a document/PDF).
//   window.openSendForApproval(source)  one request and one email to the reviewer
//   window.openSchedulePost(source)     publishes by itself at the chosen time
// source: { key, story, conversationId, runId, platforms: [{platform, caption,
//   hashtags, images, media, slideTitles, on}], onSent(req) / onScheduled(post) }
// The server checks each platform's rules again (services/approval_bundle_service.py,
// services/schedule_service.py).
(function () {
    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const LIMITS = { instagram: [2, 10], linkedin: [2, 20], facebook: [2, 10] };
    const ORDER = ['linkedin', 'instagram', 'facebook', 'youtube'];
    const SCHEDULABLE = ['linkedin', 'instagram', 'facebook'];

    let state = null;   // { mode, source, sections: {platform: {...}}, images, reviewer, when, connections }
    let picker = null;  // { platform, multi, chosen: [], from: 'conversation' | 'library' }
    let library = null; // every image the user has made (loaded when first asked for)

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function toast(msg, type) {
        const icons = { success: 'fa-check-circle text-success', error: 'fa-exclamation-circle text-danger', warning: 'fa-exclamation-triangle text-warning', info: 'fa-info-circle text-info' };
        $('#toastBody').html(`<i class="fas ${icons[type] || icons.info} me-2"></i>${esc(msg)}`);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }

    const scheduling = () => state.mode === 'schedule';
    const submitLabel = () => scheduling() ? '<i class="fas fa-calendar-check me-1"></i>Schedule' : '<i class="fas fa-paper-plane me-1"></i>Send for approval';

    function ensureModal() {
        if (document.getElementById('sendApprovalModal')) return;
        $('body').append(`
            <div class="modal fade" id="sendApprovalModal" tabindex="-1" aria-labelledby="sendApprovalTitle" aria-hidden="true">
                <div class="modal-dialog modal-lg modal-dialog-scrollable modal-dialog-centered">
                    <div class="modal-content sa-modal">
                        <div class="modal-header">
                            <div>
                                <h5 class="modal-title" id="sendApprovalTitle"></h5>
                                <div class="sa-reviewer" id="saReviewer"></div>
                            </div>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body" id="saBody"></div>
                        <div class="modal-footer">
                            <div class="sa-footer-note" id="saFooterNote"></div>
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-sm btn-primary" id="saSubmit"></button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal fade" id="saPickerModal" tabindex="-1" aria-labelledby="saPickerTitle" aria-hidden="true">
                <div class="modal-dialog modal-lg modal-dialog-scrollable modal-dialog-centered">
                    <div class="modal-content sa-modal">
                        <div class="modal-header">
                            <h5 class="modal-title" id="saPickerTitle">Choose images</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <div class="sa-picker-source" role="tablist" aria-label="Where to pick from">
                                <button type="button" role="tab" data-picker-from="conversation">This conversation</button>
                                <button type="button" role="tab" data-picker-from="library">All my images</button>
                            </div>
                            <div class="sa-picker-hint" id="saPickerHint"></div><div class="sa-picker-grid" id="saPickerGrid"></div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-sm btn-primary" id="saPickerDone">Use these</button>
                        </div>
                    </div>
                </div>
            </div>`);
    }

    // ── When (Schedule) ──────────────────────────────────────────────────
    // <input type="datetime-local"> works in the user's own clock: "2026-10-13T10:00"
    function localInputValue(date) {
        const pad = n => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function defaultWhen() {
        const d = new Date();
        d.setDate(d.getDate() + 1);
        d.setHours(10, 0, 0, 0);
        return localInputValue(d);
    }

    function whenProblem() {
        if (!state.when) return 'Choose a date and time.';
        const when = new Date(state.when);
        if (isNaN(when)) return 'Choose a date and time.';
        if (when.getTime() < Date.now() + 60 * 1000) return 'Choose a time in the future.';
        return '';
    }

    // Schedule's "When": the best times for this post's platforms as chips
    // (/api/best-times), the date picker only when asked for
    const slotLabel = d => d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) + ' · '
        + d.toLocaleString(undefined, { hour: 'numeric', minute: '2-digit' });
    const whenText = value => slotLabel(new Date(value));
    function whenChoices() {
        const out = [];
        const best = state.best || {};
        ORDER.filter(p => state.sections[p] && best[p] && best[p].next_slot).forEach(p => {
            const value = localInputValue(new Date(best[p].next_slot));
            const same = out.find(c => c.value === value);
            if (same) same.hint.push(NAMES[p]); else out.push({ value, hint: [NAMES[p]] });
        });
        out.sort((a, b) => a.value.localeCompare(b.value));
        const picks = out.slice(0, 2).map(c => ({ value: c.value, note: 'Best for ' + c.hint.join(', ') }));
        const tomorrow = defaultWhen();
        if (!picks.some(c => c.value === tomorrow)) picks.push({ value: tomorrow, note: 'Tomorrow morning' });
        return picks;
    }

    function renderWhen() {
        let zone = '';
        try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* older browser: no name */ }
        if (scheduling() && blockedByConnections()) { $('#saReviewer').empty().addClass('d-none'); return; }
        $('#saReviewer').removeClass('d-none');
        const choices = whenChoices();
        const custom = state.customWhen || !choices.some(c => c.value === state.when);
        $('#saReviewer').html(`
            <div class="sa-when-head"><span class="sa-when-label">When</span><span class="sa-when-zone">your time${zone ? ' · ' + esc(zone.replace(/_/g, ' ')) : ''}</span></div>
            <div class="sa-when-chips" role="group" aria-label="When it goes out">
                ${choices.map(c => {
                    const on = !custom && c.value === state.when;
                    return `<button type="button" class="sa-when-chip${on ? ' on' : ''}" data-when="${esc(c.value)}" aria-pressed="${on}">
                        ${esc(whenText(c.value))}<small>${esc(c.note)}</small></button>`;
                }).join('')}
                <button type="button" class="sa-when-chip sa-when-custom${custom ? ' on' : ''}" data-when-custom aria-pressed="${custom}">
                    <i class="fas fa-calendar-day me-1"></i>${custom ? esc(whenText(state.when)) : 'Other date & time'}</button>
            </div>
            <div class="sa-when${custom ? '' : ' d-none'}">
                <label for="saWhen">Date and time</label>
                <input type="datetime-local" class="form-control form-control-sm" id="saWhen" value="${esc(state.when)}" min="${localInputValue(new Date())}">
            </div>`);
    }

    // Start the time at the best slot for the post's first platform
    // (/api/best-times: from their own results, or a common starting point)
    function suggestBestTime() {
        const tz = -new Date().getTimezoneOffset();
        $.getJSON('/api/best-times?tz=' + tz).done(function (r) {
            if (!state) return;
            state.best = r.platforms || {};
            const platform = Object.keys(state.sections).find(p => state.sections[p].on) || Object.keys(state.sections)[0];
            const best = state.best[platform];
            if (best && !state.whenTouched) state.when = localInputValue(new Date(best.next_slot));
            renderWhen();
            render();
        });
    }

    // ── Rendering ────────────────────────────────────────────────────────
    function problems(platform, sec) {
        const out = [];
        if (scheduling() && state.connections && !state.connections[platform]) out.push(`Connect your ${NAMES[platform]} account to schedule to it.`);
        if (sec.media !== 'none' && !sec.images.length) out.push('Pick an image.');
        if (platform === 'instagram' && (sec.media === 'none' || !sec.images.length)) out.push('Instagram posts need an image.');
        if (sec.media === 'carousel') {
            const [low, high] = LIMITS[platform] || [2, 10];
            if (sec.images.length < low || sec.images.length > high) out.push(`${NAMES[platform]} carousels need ${low}-${high} slides (now ${sec.images.length}).`);
            if (platform === 'instagram') {
                const ratios = sec.images.map(u => (state.dims[u] || {}).ratio).filter(Boolean);
                if (ratios.length > 1 && Math.max(...ratios) - Math.min(...ratios) > 0.02 * Math.max(...ratios)) {
                    out.push('Instagram carousels need every slide in the same shape.');
                }
            }
        }
        if (platform === 'instagram' && sec.hashtags.length > 30) out.push('Instagram allows at most 30 hashtags.');
        if (!sec.caption.trim() && !sec.images.length) out.push('Add a caption or an image.');
        if (scheduling() && window.PostPreview) {  // what is published is not cut short, so it has to fit
            const length = Array.from(window.PostPreview.postText(sec.caption, sec.hashtags)).length, limit = window.PostPreview.limitFor(platform);
            if (length > limit) out.push(`The caption and hashtags are ${(length - limit).toLocaleString()} characters over ${NAMES[platform]}'s limit of ${limit.toLocaleString()}.`);
        }
        return out;
    }

    function slidesHtml(platform, sec) {
        if (sec.media === 'none') return '';
        const tiles = sec.images.map((url, i) => `
            <div class="sa-slide">
                <img src="${esc(url)}" alt="${sec.media === 'carousel' ? 'Slide ' + (i + 1) : 'Post image'}">
                ${sec.media === 'carousel' ? `<span class="sa-slide-num">${i + 1}</span>
                <div class="sa-slide-tools">
                    <button type="button" data-move="-1" data-platform="${platform}" data-i="${i}" ${i === 0 ? 'disabled' : ''} title="Move left"><i class="fas fa-arrow-left"></i></button>
                    <button type="button" data-move="1" data-platform="${platform}" data-i="${i}" ${i === sec.images.length - 1 ? 'disabled' : ''} title="Move right"><i class="fas fa-arrow-right"></i></button>
                    <button type="button" data-remove="${i}" data-platform="${platform}" title="Remove"><i class="fas fa-xmark"></i></button>
                </div>` : ''}
            </div>`).join('');
        const add = `<button type="button" class="sa-slide sa-slide-add" data-pick="${platform}">
                <i class="fas ${sec.media === 'carousel' ? 'fa-plus' : 'fa-image'}"></i><span>${sec.media === 'carousel' ? 'Add slides' : (sec.images.length ? 'Change image' : 'Pick image')}</span></button>`;
        return `<div class="sa-slides">${tiles}${add}</div>`;
    }

    // Schedule: which account the post goes out from
    function connectionHtml(platform) {
        if (!scheduling() || !state.connections) return '';
        const account = state.connections[platform];
        return account
            ? `<span class="sa-conn"><i class="fas fa-link me-1"></i>Posts as ${esc(account)}</span>`
            : `<span class="sa-conn off"><i class="fas fa-link-slash me-1"></i>Not connected &middot; <a href="/settings" target="_blank" rel="noopener">Connect in Settings</a></span>`;
    }

    function sectionHtml(platform) {
        const sec = state.sections[platform];
        const issues = sec.on ? problems(platform, sec) : [];
        return `
            <section class="sa-section${sec.on ? '' : ' off'}">
                <label class="sa-section-head">
                    <input type="checkbox" class="form-check-input" data-include="${platform}" ${sec.on ? 'checked' : ''}>
                    <i class="fab ${ICONS[platform]} sa-icon-${platform}"></i><strong>${NAMES[platform]}</strong>
                    ${sec.on && !issues.length ? '<span class="sa-ok"><i class="fas fa-check"></i> Ready</span>' : ''}
                    ${scheduling() && state.connections && !state.connections[platform]
                        ? `<a class="sa-connect" href="/settings" target="_blank" rel="noopener" title="Opens Settings in a new tab - this post stays here">Connect ${NAMES[platform]}</a>` : ''}
                </label>
                ${sec.on ? `
                ${connectionHtml(platform)}
                ${sectionEditorHtml(platform, issues)}
                ` : ''}
            </section>`;
    }

    // A platform's media, caption and hashtags (and what still needs fixing)
    function sectionEditorHtml(platform, issues) {
        const sec = state.sections[platform];
        const mediaChoice = (value, label) => `<label class="sa-pill${sec.media === value ? ' on' : ''}">
            <input type="radio" name="saMedia_${platform}" value="${value}" ${sec.media === value ? 'checked' : ''} data-media="${platform}"> ${label}</label>`;
        return `
                <div class="sa-media-row">
                    ${platform === 'instagram' ? '' : mediaChoice('none', 'Text only')}
                    ${mediaChoice('single', 'One image')}
                    ${platform === 'youtube' ? '' : mediaChoice('carousel', 'Carousel')}
                </div>
                ${slidesHtml(platform, sec)}
                ${platform === 'linkedin' && sec.media === 'carousel' ? `
                <div class="sa-format">
                    <span>LinkedIn format:</span>
                    <label class="sa-pill${sec.format === 'multi_image' ? ' on' : ''}"><input type="radio" name="saFmt" value="multi_image" data-format ${sec.format === 'multi_image' ? 'checked' : ''}> Multi-image post</label>
                    <label class="sa-pill${sec.format === 'document' ? ' on' : ''}"><input type="radio" name="saFmt" value="document" data-format ${sec.format === 'document' ? 'checked' : ''}> Document carousel (PDF)</label>
                </div>
                <div class="sa-help">${sec.format === 'document'
                    ? `The slides become a PDF people swipe through (LinkedIn's most popular carousel).${scheduling() ? '' : ' The reviewer gets the PDF.'}`
                    : 'A post with several images - LinkedIn shows them as a grid that opens into a swipeable view.'}</div>` : ''}
                <label class="sa-label" for="saCaption_${platform}">Caption</label>
                <textarea class="form-control form-control-sm" id="saCaption_${platform}" rows="4" data-caption="${platform}">${esc(sec.caption)}</textarea>
                <label class="sa-label" for="saTags_${platform}">Hashtags</label>
                <input class="form-control form-control-sm" id="saTags_${platform}" data-tags="${platform}" value="${esc(sec.hashtags.join(' '))}" placeholder="#marketing #launch">
                ${issues.length ? `<div class="sa-issues">${issues.map(t => `<div><i class="fas fa-triangle-exclamation me-1"></i>${esc(t)}</div>`).join('')}</div>` : ''}`;
    }

    // Schedule: what the window shows depends on where things stand.
    // Nothing in the post can go out yet (no account connected) -> one
    // clear "connect" screen. Otherwise: problems said once (with Connect /
    // Leave it out), then one row per platform that opens into the editor.
    const connected = p => !!(state.connections && state.connections[p]);
    const blockedByConnections = () => !!state.connections && Object.keys(state.sections).length > 0
        && !Object.keys(state.sections).some(connected);

    function connectFirstHtml(platforms) {
        const names = platforms.map(p => NAMES[p]);
        const list = names.length > 1 ? names.slice(0, -1).join(', ') + ' or ' + names[names.length - 1] : names[0];
        return `
            <div class="sa-connect-first">
                <div class="sa-connect-icons" aria-hidden="true">${platforms.map(p => `<span class="sa-plat-tile sa-tile-${p}"><i class="fab ${ICONS[p]}"></i></span>`).join('')}</div>
                <h3>Connect ${esc(list)} to schedule this post</h3>
                <p>AVIR AI publishes it for you at the time you choose. Connecting takes about a minute and opens in a new tab, so this post stays here.</p>
                <div class="sa-connect-actions">
                    ${platforms.map(p => `<a class="sa-connect-btn" href="/settings" target="_blank" rel="noopener"><i class="fab ${ICONS[p]} me-2"></i>Connect ${NAMES[p]}</a>`).join('')}
                </div>
                <p class="sa-connect-wait"><i class="fas fa-rotate me-1"></i>This window updates by itself when you come back.</p>
            </div>`;
    }

    function scheduleBodyHtml(platforms) {
        if (blockedByConnections()) return connectFirstHtml(platforms);
        const missing = state.connections ? platforms.filter(p => !connected(p) && state.sections[p].on) : [];
        const notice = missing.map(p => `
            <div class="sa-notice">
                <span class="sa-plat-tile sa-tile-${p} sm" aria-hidden="true"><i class="fab ${ICONS[p]}"></i></span>
                <span><strong>${NAMES[p]}</strong> isn't connected yet.</span>
                <a class="sa-notice-btn" href="/settings" target="_blank" rel="noopener" title="Opens Settings in a new tab - this post stays here">Connect</a>
                <button type="button" class="sa-notice-skip" data-skip="${p}">Leave it out</button>
            </div>`).join('');
        const rows = platforms.map(p => {
            const sec = state.sections[p];
            const issues = sec.on ? problems(p, sec).filter(t => !t.startsWith('Connect your')) : [];
            const status = !state.connections ? ''
                : !connected(p) ? (sec.on ? '' : `<span class="sa-row-status off">not connected · <a href="/settings" target="_blank" rel="noopener">Connect</a></span>`)
                : issues.length ? `<span class="sa-row-status warn">${issues.length} to fix</span>`
                : `<span class="sa-row-status ok">${esc(state.connections[p])}</span>`;
            const thumb = sec.images[0] ? `<img src="${esc(sec.images[0])}" alt="" class="sa-row-thumb">`
                : `<span class="sa-row-thumb sa-plat-tile sa-tile-${p}" aria-hidden="true"><i class="fab ${ICONS[p]}"></i></span>`;
            const firstLine = (sec.caption || '').split('\n').find(l => l.trim()) || 'No caption';
            const open = sec.open || (sec.on && issues.length && sec.openedForIssues !== false);
            return `
                <li class="sa-row${sec.on ? '' : ' off'}${open ? ' open' : ''}">
                    <div class="sa-row-main">
                        <input type="checkbox" class="form-check-input" data-include="${p}" ${sec.on ? 'checked' : ''} aria-label="Include ${NAMES[p]}">
                        ${thumb}
                        <span class="sa-row-text-col">
                            <strong>${NAMES[p]} ${status}</strong>
                            <small>${esc(firstLine)}</small>
                        </span>
                        <button type="button" class="sa-row-edit" data-open-sec="${p}" aria-expanded="${open}">${open ? 'Done' : 'Edit'}</button>
                    </div>
                    ${open && sec.on ? `<div class="sa-row-editor">${sectionEditorHtml(p, issues)}</div>` : ''}
                </li>`;
        }).join('');
        return `${notice}<p class="sa-what">Post to</p><ul class="sa-rows">${rows}</ul>`;
    }

    function scheduleNote(on) {
        if (!on.length) return 'Tick at least one platform.';
        if (on.some(p => state.connections && !connected(p))) return 'Connect it, or leave it out, to schedule the rest.';
        const broken = on.find(p => problems(p, state.sections[p]).length);
        if (broken) return `${NAMES[broken]}: ${problems(broken, state.sections[broken])[0]}`;
        const when = whenProblem();
        if (when) return when;
        return 'Goes out by itself. Move or cancel it any time in the calendar.';
    }

    function render() {
        const platforms = ORDER.filter(p => state.sections[p]);
        if (scheduling()) {
            $('#saBody').html(platforms.length ? scheduleBodyHtml(platforms)
                : '<div class="text-muted small">This post has nothing that can be scheduled.</div>');
            const on = platforms.filter(p => state.sections[p].on);
            const blocked = !on.length || on.some(p => problems(p, state.sections[p]).length) || !!whenProblem() || !state.connections;
            const waiting = blockedByConnections();
            $('#saSubmit').toggleClass('d-none', waiting).prop('disabled', blocked)
                .html(`<i class="fas fa-calendar-check me-1"></i>Schedule${whenProblem() ? '' : ' for ' + esc(whenText(state.when))}`);
            $('#saFooterNote').text(!state.connections ? 'Checking your connected accounts…' : waiting ? '' : scheduleNote(on));
            if ($('#saReviewer').hasClass('d-none') !== waiting) renderWhen();
            // The clicked control was just redrawn: keep the keyboard in the dialog (Esc, Tab)
            const dialog = document.getElementById('sendApprovalModal');
            if (dialog && dialog.classList.contains('show') && (document.activeElement === document.body || !document.activeElement)) dialog.focus();
            return;
        }
        $('#saBody').html(platforms.length ? platforms.map(sectionHtml).join('')
            : '<div class="text-muted small">This post has nothing that can be scheduled.</div>');
        const on = platforms.filter(p => state.sections[p].on);
        const fixes = on.some(p => problems(p, state.sections[p]).length);
        let blocked, note;
        if (scheduling()) {
            const when = whenProblem();
            blocked = !on.length || fixes || !!when || !state.connections;
            note = !on.length ? 'Choose at least one platform.' : fixes ? 'Fix the notes above to schedule.' : when
                || `Publishes by itself on ${on.length} platform${on.length > 1 ? 's' : ''} at that time. You can move or cancel it on the calendar.`;
        } else {
            blocked = !state.reviewer || !on.length || fixes;
            note = !on.length ? 'Choose at least one platform.'
                : blocked && state.reviewer ? 'Fix the notes above to send.' : `${on.length} platform${on.length > 1 ? 's' : ''} in one email.`;
        }
        $('#saSubmit').prop('disabled', blocked);
        $('#saFooterNote').text(note);
    }

    function renderReviewer() {
        if (state.reviewer) {
            $('#saReviewer').html(`Goes to <strong>${esc(state.reviewer)}</strong> <button type="button" class="sa-link" id="saChangeReviewer">change</button>`);
        } else {
            $('#saReviewer').html(`<span class="text-danger">Who should approve it?</span>
                <span class="sa-reviewer-form"><input type="email" class="form-control form-control-sm" id="saReviewerInput" placeholder="reviewer@company.com" aria-label="Reviewer email">
                <button type="button" class="btn btn-sm btn-outline-primary" id="saSaveReviewer">Save</button></span>`);
        }
    }

    // Image shapes, for Instagram's "same shape" rule
    function measure(urls) {
        urls.filter(u => !state.dims[u]).forEach(u => {
            const img = new Image();
            img.onload = () => { state.dims[u] = { ratio: img.naturalWidth / img.naturalHeight }; render(); };
            img.src = u;
        });
    }

    // ── Picker: images from this conversation, or every image made so far ──
    function openPicker(platform) {
        const sec = state.sections[platform];
        picker = { platform, multi: sec.media === 'carousel', chosen: sec.media === 'carousel' ? sec.images.slice() : [],
                   from: state.images.length ? 'conversation' : 'library' };
        $('#saPickerTitle').text(picker.multi ? `Carousel slides for ${NAMES[platform]}` : `Image for ${NAMES[platform]}`);
        const [low, high] = LIMITS[platform] || [2, 10];
        $('#saPickerHint').text(picker.multi ? `Click images in the order they should appear (${low}-${high} slides).` : 'Click the image to use.');
        if (picker.from === 'library') loadLibrary();
        renderPicker();
        bootstrap.Modal.getOrCreateInstance(document.getElementById('saPickerModal')).show();
    }

    function loadLibrary() {
        if (library) return;
        library = [];
        $.get('/api/library/images?limit=200').done(r => {
            library = (r.images || []).map(img => ({ url: img.url, description: img.description }));
            if (picker) renderPicker();
        });
    }

    function renderPicker() {
        $('[data-picker-from]').each(function () {
            const on = $(this).data('picker-from') === picker.from;
            $(this).toggleClass('on', on).attr('aria-selected', on);
        });
        const images = picker.from === 'library' ? (library || []) : state.images;
        $('#saPickerGrid').html(images.length ? images.map(img => {
            const n = picker.chosen.indexOf(img.url);
            return `<button type="button" class="sa-pick${n >= 0 ? ' on' : ''}" data-url="${esc(img.url)}">
                <img src="${esc(img.url)}" alt="${esc(img.description || 'Image')}" loading="lazy">
                ${n >= 0 ? `<span class="sa-pick-num">${picker.multi ? n + 1 : '<i class="fas fa-check"></i>'}</span>` : ''}
            </button>`;
        }).join('') : `<div class="text-muted small">${picker.from === 'library'
            ? 'You have not created any images yet.'
            : 'No images in this conversation yet - create some first (for example with /carousel), or pick from all your images.'}</div>`);
    }

    function loadImages() {
        const own = [];
        Object.values(state.sections).forEach(sec => sec.images.forEach(u => { if (!own.includes(u)) own.push(u); }));
        state.images = own.map(u => ({ url: u }));
        if (!state.source.conversationId) return;
        $.get(`/api/conversations/${state.source.conversationId}/images`).done(r => {
            (r.images || []).forEach(img => { if (!state.images.some(i => i.url === img.url)) state.images.push(img); });
            if (picker) renderPicker();
        });
    }

    // ── Events ───────────────────────────────────────────────────────────
    function bindOnce() {
        if (bindOnce.done) return;
        bindOnce.done = true;
        const $doc = $(document);
        $doc.on('change', '[data-include]', function () { state.sections[$(this).data('include')].on = this.checked; render(); });
        $doc.on('change', '[data-media]', function () {
            const sec = state.sections[$(this).data('media')];
            sec.media = this.value;
            if (sec.media === 'single') sec.images = sec.images.slice(0, 1);
            if (sec.media === 'none') sec.images = [];
            if (sec.media !== 'none' && !sec.images.length && sec.lastImages.length) sec.images = sec.media === 'single' ? sec.lastImages.slice(0, 1) : sec.lastImages.slice();
            render();
        });
        $doc.on('change', '[data-format]', function () { state.sections.linkedin.format = this.value; render(); });
        $doc.on('input', '[data-caption]', function () { state.sections[$(this).data('caption')].caption = this.value; });
        $doc.on('change', '[data-caption]', render);
        $doc.on('change', '[data-tags]', function () {
            state.sections[$(this).data('tags')].hashtags = this.value.split(/[\s,]+/).filter(Boolean).map(t => t.startsWith('#') ? t : '#' + t);
            render();
        });
        $doc.on('click', '[data-move]', function () {
            const sec = state.sections[$(this).data('platform')], i = Number($(this).data('i')), j = i + Number($(this).data('move'));
            [sec.images[i], sec.images[j]] = [sec.images[j], sec.images[i]];
            render();
        });
        $doc.on('click', '[data-remove]', function () {
            state.sections[$(this).data('platform')].images.splice(Number($(this).data('remove')), 1);
            render();
        });
        $doc.on('click', '[data-pick]', function () { openPicker($(this).data('pick')); });
        $doc.on('click', '[data-picker-from]', function () {
            picker.from = $(this).data('picker-from');
            if (picker.from === 'library') loadLibrary();
            renderPicker();
        });
        $doc.on('click', '.sa-pick', function () {
            const url = $(this).data('url');
            const n = picker.chosen.indexOf(url);
            if (picker.multi) { if (n >= 0) picker.chosen.splice(n, 1); else picker.chosen.push(url); }
            else picker.chosen = [url];
            renderPicker();
        });
        $doc.on('click', '#saPickerDone', function () {
            const sec = state.sections[picker.platform];
            sec.images = picker.chosen.slice();
            sec.lastImages = sec.images.slice();
            measure(sec.images);
            bootstrap.Modal.getOrCreateInstance(document.getElementById('saPickerModal')).hide();
            render();
        });
        $doc.on('click', '#saChangeReviewer', function () { state.reviewer = null; renderReviewer(); render(); });
        $doc.on('click', '#saSaveReviewer', function () {
            const email = ($('#saReviewerInput').val() || '').trim();
            $.ajax({ url: '/api/approval-settings', type: 'PUT', contentType: 'application/json', data: JSON.stringify({ reviewer_email: email }) })
                .done(r => { state.reviewer = r.reviewer_email; renderReviewer(); render(); })
                .fail(xhr => toast((xhr.responseJSON || {}).error || 'Could not save the reviewer.', 'error'));
        });
        $doc.on('input change', '#saWhen', function () { state.when = this.value; state.whenTouched = true; render(); });
        $doc.on('click', '[data-when]', function () {
            state.when = $(this).data('when'); state.whenTouched = true; state.customWhen = false;
            renderWhen(); render();
        });
        $doc.on('click', '[data-when-custom]', function () {
            state.customWhen = true; renderWhen(); render();
            $('#saWhen').trigger('focus');
        });
        $doc.on('click', '[data-open-sec]', function () {
            const sec = state.sections[$(this).data('open-sec')];
            const open = $(this).attr('aria-expanded') === 'true';
            sec.open = !open;
            sec.openedForIssues = !open ? undefined : false;  // closing it keeps it closed even with notes
            render();
        });
        $doc.on('click', '[data-skip]', function () {
            const sec = state.sections[$(this).data('skip')];
            sec.on = false; sec.skipped = true;
            render();
        });
        // The image picker opens on top of the dialog. When it closes, the dialog
        // behind takes the keyboard back (Esc, Tab) and the page stays locked.
        $doc.on('hidden.bs.modal', '#saPickerModal', function () {
            const main = document.getElementById('sendApprovalModal');
            if (!main.classList.contains('show')) return;
            document.body.classList.add('modal-open');
            main.focus();
        });
        $doc.on('click', '#saSubmit', submit);
    }

    function chosenItems() {
        return ORDER.filter(p => state.sections[p] && state.sections[p].on).map(p => {
            const sec = state.sections[p];
            const item = { platform: p, caption: sec.caption, hashtags: sec.hashtags, media: sec.media, images: sec.images,
                           slide_titles: sec.images.map(u => sec.titles[u] || '') };
            if (p === 'linkedin' && sec.media === 'carousel') item.linkedin_format = sec.format;
            return item;
        });
    }

    function submit() {
        const items = chosenItems();
        const hide = () => bootstrap.Modal.getOrCreateInstance(document.getElementById('sendApprovalModal')).hide();
        const $btn = $('#saSubmit').prop('disabled', true);
        if (scheduling()) {
            const when = new Date(state.when);
            $btn.html('<i class="fas fa-spinner fa-spin me-1"></i>Scheduling...');
            $.ajax({
                url: '/api/social/schedule', type: 'POST', contentType: 'application/json',
                // the moment itself (UTC), so it means the same wherever the server is
                data: JSON.stringify({ scheduled_at: when.toISOString(), items: items, story: state.source.story || '',
                                       run_id: state.source.runId || null, conversation_id: state.source.conversationId || null }),
            }).done(r => {
                hide();
                toast(`Scheduled for ${when.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}. It is on your calendar.`, 'success');
                if (state.source.onScheduled) state.source.onScheduled(r.scheduled_post);
            }).fail(xhr => {
                toast((xhr.responseJSON || {}).error || 'Could not schedule the post.', 'error');
            }).always(() => render());
            return;
        }
        $btn.html('<i class="fas fa-spinner fa-spin me-1"></i>Sending...');
        $.ajax({
            url: '/api/approval-requests', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ pipeline_client_id: state.source.key, story: state.source.story || '', items: items }),
        }).done(r => {
            hide();
            toast(r.email && r.email.success ? `Sent to ${state.reviewer} for approval.` : 'Request saved, but the email could not be sent - the reviewer can open it from Approval Requests.',
                  r.email && r.email.success ? 'success' : 'warning');
            if (state.source.onSent) state.source.onSent(r.request);
        }).fail(xhr => {
            toast((xhr.responseJSON || {}).error || 'Could not send for approval.', 'error');
        }).always(() => $btn.html(submitLabel()) && render());
    }

    function open(source, mode) {
        ensureModal();
        bindOnce();
        const sections = {};
        (source.platforms || []).forEach(p => {
            if (!NAMES[p.platform] || (mode === 'schedule' && !SCHEDULABLE.includes(p.platform))) return;
            const images = (p.images || []).filter(Boolean);
            const titles = {};
            images.forEach((u, i) => { titles[u] = (p.slideTitles || [])[i] || ''; });
            sections[p.platform] = {
                on: p.on !== false, caption: p.caption || '', hashtags: p.hashtags || [], images: images, lastImages: images.slice(),
                media: p.media || (images.length > 1 ? 'carousel' : images.length ? 'single' : (p.platform === 'instagram' ? 'single' : 'none')),
                format: 'multi_image', titles: titles,
            };
        });
        state = { mode: mode, source, sections, images: [], reviewer: null, dims: {}, when: defaultWhen(), connections: null, best: null, customWhen: false };
        picker = null;
        $('#sendApprovalModal .sa-modal').toggleClass('sa-schedule', scheduling());
        $('#sendApprovalTitle').html(scheduling() ? '<i class="fas fa-calendar-plus me-2"></i>Schedule post' : '<i class="fas fa-paper-plane me-2"></i>Send for approval');
        $('#saSubmit').html(submitLabel());
        loadImages();
        measure(Object.values(sections).flatMap(s => s.images));
        if (scheduling()) {
            renderWhen();
            render();
            suggestBestTime();
            $.get('/api/social/connections').done(r => {
                state.connections = r.connections || {};
                // A platform that isn't connected starts unticked, so the rest can be scheduled straight away
                Object.keys(sections).forEach(p => { if (!state.connections[p]) sections[p].on = false; });
                if (!Object.values(sections).some(sec => sec.on)) Object.values(sections).forEach(sec => { sec.on = true; });  // none connected: show what to connect
                renderWhen();
                render();
            }).fail(() => { state.connections = {}; render(); });
            watchConnections();
        } else {
            render();
            $('#saReviewer').text('Loading reviewer...');
            $.get('/api/approval-settings').done(r => {
                state.reviewer = r.reviewer_email || r.default_reviewer_email || null;
                renderReviewer();
                render();
            });
        }
        bootstrap.Modal.getOrCreateInstance(document.getElementById('sendApprovalModal')).show();
    }

    // After "Connect" (in a new tab), coming back here picks up the new
    // connection and ticks that platform, without losing the post
    let watching = false;
    function watchConnections() {
        if (watching) return;
        watching = true;
        $(window).on('focus', function () {
            if (!state || !scheduling() || !state.connections) return;
            if (!$('#sendApprovalModal').hasClass('show')) return;
            $.get('/api/social/connections').done(r => {
                const now = r.connections || {};
                Object.keys(state.sections).forEach(p => { if (now[p] && !state.connections[p]) { state.sections[p].on = true; state.sections[p].skipped = false; } });
                state.connections = now;
                render();
            });
        });
    }

    window.openSendForApproval = function (source) { open(source, 'approval'); };
    window.openSchedulePost = function (source) { open(source, 'schedule'); };
})();
