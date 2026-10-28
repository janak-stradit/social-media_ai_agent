// Settings -> Autopilot (templates/settings.html #autopilot-pane; /api/autopilot,
// services/autopilot_service.py). Off by default. Turning it on asks first;
// in Review first mode nothing is published until the user approves it.
$(function () {
    if (!$('#autopilot-pane').length) return;
    const tz = -new Date().getTimezoneOffset();  // the user's clock: posts go out at their local best times
    const STATUS = {
        preparing: 'Preparing', awaiting_approval: 'Waiting for your approval', scheduled: 'Scheduled',
        rejected: 'Not approved', expired: "Wasn't approved in time", cancelled: 'Withdrawn', failed: "Couldn't be prepared",
    };
    let state = null;
    let saving = false;

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(message, type) {
        if (typeof window.showToast === 'function') { window.showToast(message, type); return; }
        $('#toastBody').text(message);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }
    function dayText(iso) {
        const parts = iso.split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    }

    function render() {
        const s = state;
        if (window.setSettingsBadge) {
            window.setSettingsBadge('autopilot-tab', !s.enabled ? 'Off' : (s.paused_reason || s.problems.length) ? 'Paused' : 'On',
                !s.enabled ? '' : (s.paused_reason || s.problems.length) ? 'warn' : 'ok');
        }
        $('#apEnabled').prop('checked', s.enabled);
        $('#apEnabledLabel').text(s.enabled ? 'On' : 'Off');
        $('#apPlatforms').html(s.platform_choices.map(p => `
            <label class="${p.available ? '' : 'is-off'}" title="${p.available ? (p.connected ? '' : 'Not connected yet') : 'Needs an image'}">
                <input type="checkbox" class="form-check-input" value="${esc(p.id)}" ${s.platforms.includes(p.id) ? 'checked' : ''} ${p.available ? '' : 'disabled'}>
                ${esc(p.name)}${p.available && !p.connected ? ' <small>(not connected)</small>' : ''}
            </label>`).join(''));
        $('#apDays').val(String(s.days_ahead));
        if ($('#apLanguage option[value="' + s.language + '"]').length) $('#apLanguage').val(s.language);

        const $status = $('#apStatus').removeClass('is-on is-problem');
        if (s.enabled && s.paused_reason) {
            $status.addClass('is-problem').html(`<i class="fas fa-circle-pause me-1"></i><strong>Paused:</strong> ${esc(s.paused_reason)} Fix it, then turn Autopilot off and on again.`);
        } else if (s.enabled && s.problems.length) {
            $status.addClass('is-problem').html(`<i class="fas fa-triangle-exclamation me-1"></i>${esc(s.problems[0])}`);
        } else if (s.enabled) {
            const waiting = s.posts.filter(p => p.status === 'awaiting_approval').length;
            $status.addClass('is-on').html('<i class="fas fa-circle-check me-1"></i>Autopilot is on. It checks every hour and sends each post to you for approval.'
                + (waiting ? ` <a href="/approve">${waiting} waiting for your approval</a>.` : ''));
        } else {
            $status.text('Autopilot is off. Turn it on to have posts prepared for your open days.');
        }
        $('#apRunNow').prop('disabled', !s.enabled || !!s.paused_reason || s.problems.length > 0);
        $('#apLastRun').text(s.last_run_at ? 'Last checked ' + new Date(s.last_run_at).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '');

        $('#apPosts').html(s.posts.length ? s.posts.map(p => `
            <div class="ap-post">
                <span class="ap-post-day">${esc(dayText(p.slot_date))}</span>
                <span class="ap-post-title" title="${esc(p.error || p.idea_title || '')}">${esc(p.idea_title || 'Post')}</span>
                <span class="ap-pill is-${esc(p.status)}">${esc(STATUS[p.status] || p.status)}</span>
                ${p.approval_request_id && p.status === 'awaiting_approval' ? `<a class="btn btn-sm btn-primary" href="/approve/${p.approval_request_id}">Review</a>` : ''}
                ${p.status === 'scheduled' ? '<a class="btn btn-sm btn-outline-secondary" href="/calendar">On the calendar</a>' : ''}
            </div>`).join('') : '<p class="text-muted small mb-0">Nothing prepared yet.</p>');
    }

    function load() {
        return $.getJSON('/api/autopilot').done(r => { state = r.autopilot; render(); })
            .fail(() => $('#apStatus').text('Could not load Autopilot. Please try again.'));
    }

    function save(fields, message) {
        if (saving) return $.Deferred().reject().promise();
        saving = true;
        return $.ajax({ url: '/api/autopilot', type: 'PUT', contentType: 'application/json', data: JSON.stringify({ ...fields, tz_minutes: tz }) })
            .done(r => { state = r.autopilot; render(); if (message) toast(message, 'success'); })
            .fail(xhr => { toast((xhr.responseJSON || {}).error || 'Could not save Autopilot.', 'error'); render(); })
            .always(() => { saving = false; });
    }

    function chosenPlatforms() {
        return $('#apPlatforms input:checked').map(function () { return this.value; }).get();
    }

    $('#apEnabled').on('change', function () {
        if (!state) return;
        if (this.checked) {
            const names = chosenPlatforms().map(p => (state.platform_choices.find(c => c.id === p) || {}).name).join(' and ');
            if (!names) { this.checked = false; toast('Choose at least one platform first.', 'info'); return; }
            const ok = window.confirm(`Turn on Autopilot for ${names}?\n\nIt will prepare posts for your calendar's open days and send each one to you for approval. Nothing is published until you approve it.`);
            if (!ok) { this.checked = false; return; }
            save({ enabled: true, platforms: chosenPlatforms(), days_ahead: Number($('#apDays').val()), language: $('#apLanguage').val() },
                'Autopilot is on. Your first posts are prepared within the hour.');
        } else {
            save({ enabled: false }, 'Autopilot is off. Posts waiting for approval were withdrawn.');
        }
    });
    $('#apPlatforms').on('change', 'input', function () {
        if (state && state.enabled && !chosenPlatforms().length) { this.checked = true; toast('Keep at least one platform, or turn Autopilot off.', 'info'); return; }
        save({ platforms: chosenPlatforms() });
    });
    $('#apDays').on('change', function () { save({ days_ahead: Number(this.value) }); });
    $('#apLanguage').on('change', function () { save({ language: this.value }); });
    $('#apRunNow').on('click', function () {
        const $btn = $(this).prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span>Preparing...');
        $.post('/api/autopilot/run')
            .done(r => {
                state = r.autopilot;
                toast(r.prepared ? `Prepared ${r.prepared} post${r.prepared === 1 ? '' : 's'} - review ${r.prepared === 1 ? 'it' : 'them'} below.`
                    : 'Nothing new to prepare: your open days already have posts, or none are coming up.', r.prepared ? 'success' : 'info');
            })
            .fail(xhr => toast((xhr.responseJSON || {}).error || 'Could not prepare posts.', 'error'))
            .always(() => { $btn.html('<i class="fas fa-wand-magic-sparkles me-1"></i>Prepare now'); render(); });
    });

    // The language choices: English plus the brand country's (services/language_service.py)
    $.getJSON('/api/languages').done(r => {
        $('#apLanguage').html((r.languages || []).map(l => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join(''));
        if (state) render();
    });
    load();
    $('#autopilot-tab').on('shown.bs.tab', load);
});
