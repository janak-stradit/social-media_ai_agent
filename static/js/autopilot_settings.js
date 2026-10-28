// Settings -> Autopilot (templates/settings.html #autopilot-pane): its status
// and a link to the /autopilot page, where it is set up and run.
$(function () {
    if (!$('#autopilot-pane').length) return;

    function load() {
        $.getJSON('/api/autopilot').done(function (r) {
            const a = r.autopilot;
            const waiting = (a.needs_ok || []).length;
            const paused = a.enabled && (a.paused_reason || (a.problems || []).length);
            $('#aptCard').toggleClass('is-on', a.enabled && !paused).toggleClass('is-paused', !!paused);
            $('#aptTitle').text(!a.enabled ? 'Autopilot is off' : paused ? 'Autopilot is paused' : 'Autopilot is on');
            $('#aptSub').text(!a.enabled ? 'Set it up in a minute: how often, where and what about.'
                : paused ? (a.paused_reason || a.problems[0])
                    : a.posts_per_week + ' posts a week' + (waiting ? ' · ' + waiting + ' waiting for approval' : ''));
            $('#aptOpen').html('<i class="fas fa-plane-departure me-1"></i>' + (a.enabled ? 'Open Autopilot' : 'Set up Autopilot'));
            if (window.setSettingsBadge) {
                window.setSettingsBadge('autopilot-tab', !a.enabled ? 'Off' : paused ? 'Paused' : 'On', !a.enabled ? '' : paused ? 'warn' : 'ok');
            }
        }).fail(function () { $('#aptTitle').text('Autopilot'); $('#aptSub').text('Could not load its status.'); });
    }
    load();
    $('#autopilot-tab').on('shown.bs.tab', load);
});
