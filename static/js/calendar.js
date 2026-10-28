// Content Calendar (templates/calendar.html), a planner workspace: a slim strip
// with this week's goal and the last 30 days, alerts for posts that didn't
// publish, a "To plan" panel (open days, ideas, occasions) whose cards can be
// dragged onto a day, and the calendar (Month or Week, filtered by platform).
// Data: /api/calendar (services/calendar_service.py). Vanilla JS like
// user_menu.js - the page loads neither jQuery nor Bootstrap's JS.
(function () {
    const grid = document.getElementById('calGrid');
    if (!grid) return;
    const $ = (id) => document.getElementById(id);
    const tz = -new Date().getTimezoneOffset();  // minutes ahead of UTC: days follow the user's own clock
    const PLATFORM_ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const CONVERSATION_KEY = 'avir_active_conversation';  // Studio Chat reopens this conversation (app_v2.js)
    const PENDING_IDEA_KEY = 'avir_pending_idea';         // ...or starts a new post from this idea
    const PLANNED_DAY_KEY = 'avir_planned_day';           // ...planned for this day (approval_send.js offers it in Schedule)
    const VIEW_KEY = 'avir_calendar_view', FILTER_KEY = 'avir_calendar_platforms';
    const ALL_PLATFORMS = ['linkedin', 'instagram', 'facebook'];
    let shownMonth = null;  // "YYYY-MM"
    let goal = 3;
    let lastData = null;
    let view = 'month';      // month | week
    let weekStart = null;    // the Monday shown in Week view ("YYYY-MM-DD")
    let platforms = new Set(ALL_PLATFORMS);
    let draggedIdea = null;  // a "To plan" card being dragged onto a day
    try {
        view = localStorage.getItem(VIEW_KEY) === 'week' ? 'week' : 'month';
        const saved = JSON.parse(localStorage.getItem(FILTER_KEY) || 'null');
        if (Array.isArray(saved) && saved.length) platforms = new Set(saved.filter(p => ALL_PLATFORMS.includes(p)));
    } catch (e) { /* storage blocked: defaults */ }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function icon(classes) {
        const i = el('i', classes);
        i.setAttribute('aria-hidden', 'true');
        return i;
    }
    function toast(message) {
        const box = $('toast');
        if (!box) return;
        $('toastBody').textContent = message;
        box.classList.add('show');
        setTimeout(function () { box.classList.remove('show'); }, 3000);
    }
    function parseDay(iso) {
        const parts = iso.split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }
    function longDate(iso) {
        return parseDay(iso).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
    }

    // ── This week's goal ──────────────────────────────────────────────
    function renderGoal(p) {
        goal = p.goal;
        $('calDone').textContent = p.done;
        $('calGoalOf').textContent = p.goal;
        $('calGoalValue').textContent = p.goal;
        $('calGoalDown').disabled = p.goal <= 1;
        $('calGoalUp').disabled = p.goal >= 7;
        const share = Math.min(1, p.done / p.goal);
        $('calProgressFill').style.width = Math.round(share * 100) + '%';
        $('calProgress').classList.toggle('is-met', p.goal_met);
        $('calProgress').setAttribute('aria-label', p.done + ' of ' + p.goal + ' posts created this week');
        $('calGoalText').textContent = p.goal_met
            ? (p.done > p.goal ? 'Goal reached, and then some: ' + p.done + ' posts created this week.' : 'Goal reached. Nice work this week.')
            : (p.done === 0
                ? 'No posts yet this week. Drag an idea onto a day, or use an open day in To plan.'
                : p.remaining + ' more ' + (p.remaining === 1 ? 'post' : 'posts') + ' to reach your goal this week.');

        const streak = $('calStreak');
        streak.classList.toggle('d-none', p.streak_weeks < 2);
        streak.querySelector('span').textContent = p.streak_weeks + ' weeks in a row';
    }

    function setGoal(next) {
        if (next < 1 || next > 7) return;
        $('calGoalDown').disabled = $('calGoalUp').disabled = true;
        fetch('/api/me/goal?tz=' + tz, {
            method: 'PUT', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weekly_post_goal: next }),
        })
            .then((r) => r.json())
            .then(function (r) {
                if (!r.success) throw new Error(r.error);
                load(shownMonth);  // the suggested days follow the goal
            })
            .catch(function (err) {
                toast(err.message || 'Could not save your goal.');
                load(shownMonth);
            });
    }
    $('calGoalDown').addEventListener('click', function () { setGoal(goal - 1); });
    $('calGoalUp').addEventListener('click', function () { setGoal(goal + 1); });

    // ── The month ─────────────────────────────────────────────────────
    // A post shows when it is for a platform that's switched on (or has no platform yet)
    function shown(list) {
        const on = (list || []).filter(p => ALL_PLATFORMS.includes(p));
        return !on.length || on.some(p => platforms.has(p));
    }

    function postChip(post) {
        const chip = el('button', 'cal-chip is-post');
        chip.type = 'button';
        chip.title = post.title;
        chip.append(icon(post.has_video ? 'fas fa-video' : (post.has_image ? 'fas fa-image' : 'fas fa-align-left')), el('span', '', post.title || 'Post'));
        chip.addEventListener('click', function () {
            try {
                if (post.conversation_id) sessionStorage.setItem(CONVERSATION_KEY, String(post.conversation_id));
            } catch (e) { /* storage blocked: Studio Chat just opens */ }
            window.location.href = '/dashboard';
        });
        return chip;
    }

    // ── Scheduled posts: open, move, cancel, retry ────────────────────
    const STATUS_TEXT = { pending: 'Scheduled', publishing: 'Publishing', published: 'Published', partial: 'Partly published', failed: 'Not published' };
    const STATUS_ICONS = { pending: 'fa-clock', publishing: 'fa-spinner', published: 'fa-circle-check', partial: 'fa-circle-exclamation', failed: 'fa-triangle-exclamation' };
    const PLATFORM_NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    let dragged = null;  // the scheduled post being dragged to another day

    function api(method, url, body) {
        return fetch(url, { method: method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                            body: body ? JSON.stringify(body) : undefined })
            .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
            .then(function (r) {
                if (!r.ok || r.data.success === false) throw new Error(r.data.error || 'Something went wrong. Please try again.');
                return r.data;
            });
    }

    function localInputValue(date) {
        const pad = (n) => String(n).padStart(2, '0');
        return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }
    function timeText(date) {
        return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    function movePost(item, when) {
        return api('PATCH', '/api/social/scheduled/' + item.id, { scheduled_at: when.toISOString() })
            .then(function () {
                toast('Moved to ' + when.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) + ' at ' + timeText(when) + '.');
                load(shownMonth);
            })
            .catch(function (err) { toast(err.message); });
    }

    // Dropped on a day: the same time of day, on that day
    function moveToDay(item, dayIso) {
        const old = new Date(item.scheduled_at), day = parseDay(dayIso);
        const when = new Date(day.getFullYear(), day.getMonth(), day.getDate(), old.getHours(), old.getMinutes());
        if (when.getTime() === old.getTime()) return;
        movePost(item, when);
    }

    function openScheduled(item) {
        const dialog = $('calDialog'), body = $('calDialogBody');
        const when = new Date(item.scheduled_at);
        body.textContent = '';

        const pill = el('span', 'cal-pill is-' + item.status);
        pill.append(icon('fas ' + (STATUS_ICONS[item.status] || 'fa-clock')), document.createTextNode(STATUS_TEXT[item.status] || 'Scheduled'));
        const title = el('h3', '', when.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) + ' at ' + timeText(when));
        title.id = 'calDialogTitle';
        body.append(pill, title);

        const post = el('div', 'cal-dialog-post');
        if (item.thumb) {
            const img = el('img');
            img.src = item.thumb;
            img.alt = '';
            post.appendChild(img);
        }
        const text = el('div');
        text.append(el('p', 'cal-dialog-caption', item.title),
            el('small', '', item.media === 'carousel' ? 'Carousel' : item.media === 'image' ? 'With an image' : 'Text post'));
        post.appendChild(text);
        body.appendChild(post);

        const list = el('ul', 'cal-dialog-platforms');
        item.platforms.forEach(function (platform) {
            const result = (item.results || {})[platform];
            const row = el('li');
            row.append(icon('fab ' + (PLATFORM_ICONS[platform] || 'fa-share-nodes')), el('strong', '', PLATFORM_NAMES[platform] || platform));
            if (result && result.ok) {
                row.appendChild(el('span', 'is-ok', 'Published' + (result.account ? ' to ' + result.account : '')));
                if (result.permalink) {
                    const link = el('a', '', 'View post');
                    link.href = result.permalink;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    row.appendChild(link);
                }
            } else if (result) {
                row.appendChild(el('span', 'is-error', result.error || 'Could not be published.'));
            } else {
                row.appendChild(el('span', '', item.status === 'pending' ? 'Waiting' : item.status === 'publishing' ? 'Publishing...' : 'Not sent'));
            }
            list.appendChild(row);
        });
        body.appendChild(list);

        const actions = el('div', 'cal-dialog-actions');
        const done = function (message) {
            return function () { dialog.close(); toast(message); load(shownMonth); };
        };
        const fail = function (err) { toast(err.message); };
        if (item.movable) {
            const row = el('div', 'cal-dialog-move');
            const label = el('label', '', 'Move to');
            const input = el('input');
            input.type = 'datetime-local';
            input.id = 'calMoveInput';
            input.value = localInputValue(when);
            input.min = localInputValue(new Date());
            label.htmlFor = input.id;
            const move = el('button', 'cal-btn is-primary', 'Move');
            move.type = 'button';
            move.addEventListener('click', function () {
                const next = new Date(input.value);
                if (!input.value || isNaN(next)) { toast('Choose a date and time.'); return; }
                dialog.close();
                movePost(item, next);
            });
            row.append(label, input, move);
            const cancel = el('button', 'cal-btn is-danger', 'Cancel this post');
            cancel.type = 'button';
            cancel.addEventListener('click', function () {
                if (cancel.dataset.sure !== '1') {  // a second click confirms
                    cancel.dataset.sure = '1';
                    cancel.textContent = 'Click again to cancel it';
                    return;
                }
                api('POST', '/api/social/scheduled/' + item.id + '/cancel').then(done('The post was cancelled.')).catch(fail);
            });
            actions.append(row, cancel);
        } else if (item.status === 'failed' || item.status === 'partial' || item.status === 'publishing') {
            const retry = el('button', 'cal-btn is-primary', item.status === 'partial' ? 'Send the rest now' : 'Try again now');
            retry.type = 'button';
            retry.addEventListener('click', function () {
                api('POST', '/api/social/scheduled/' + item.id + '/retry').then(done('Trying again - it goes out within a minute.')).catch(fail);
            });
            actions.append(retry, el('small', '', item.status === 'publishing'
                ? 'This post started publishing and did not finish. Check your accounts before sending it again.'
                : 'Platforms that were already published are not sent again.'));
        }
        if (item.conversation_id) {
            const studio = el('button', 'cal-btn', 'Open in Studio Chat');
            studio.type = 'button';
            studio.addEventListener('click', function () {
                try { sessionStorage.setItem(CONVERSATION_KEY, String(item.conversation_id)); } catch (e) { /* storage blocked */ }
                window.location.href = '/dashboard';
            });
            actions.appendChild(studio);
        }
        body.appendChild(actions);
        if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    }

    function scheduledChip(item) {
        const chip = el('button', 'cal-chip is-scheduled is-' + item.status + (item.status === 'failed' || item.status === 'partial' ? ' is-failed' : ''));
        chip.type = 'button';
        chip.title = (STATUS_TEXT[item.status] || 'Scheduled') + ' at ' + item.time + ': ' + item.title
            + (item.movable ? ' (drag to another day to move it, or click for more)' : '');
        chip.append(icon('fas ' + (item.autopilot ? 'fa-robot' : (STATUS_ICONS[item.status] || 'fa-clock'))), el('span', '', item.time + ' · ' + item.title));
        if (item.autopilot) {
            chip.classList.add('is-autopilot');
            chip.title = 'Autopilot · ' + chip.title;
        }
        chip.addEventListener('click', function () { openScheduled(item); });
        if (item.movable) {
            chip.draggable = true;
            chip.addEventListener('dragstart', function (e) {
                dragged = item;
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', String(item.id));
                grid.classList.add('is-dragging');
            });
            chip.addEventListener('dragend', function () {
                dragged = null;
                grid.classList.remove('is-dragging');
                grid.querySelectorAll('.is-drop').forEach(function (cell) { cell.classList.remove('is-drop'); });
            });
        }
        return chip;
    }

    function slotChip(day) {
        const idea = day.suggestion.idea;
        const chip = el('button', 'cal-chip is-slot');
        chip.type = 'button';
        chip.title = idea ? 'Suggested for ' + longDate(day.date) + ': ' + idea.title : 'A good day to post. Click to start one.';
        chip.append(icon(idea && PLATFORM_ICONS[idea.platform] ? 'fab ' + PLATFORM_ICONS[idea.platform] : 'fas fa-plus'),
            el('span', '', idea ? idea.title : 'Create a post'));
        chip.addEventListener('click', function () {
            try {
                sessionStorage.removeItem(CONVERSATION_KEY);
                if (idea) sessionStorage.setItem(PENDING_IDEA_KEY, JSON.stringify(idea));
            } catch (e) { /* storage blocked: Studio Chat opens without the idea */ }
            window.location.href = '/dashboard';
        });
        return chip;
    }

    function startIdea(idea, dayIso) {
        try {
            sessionStorage.removeItem(CONVERSATION_KEY);
            sessionStorage.setItem(PENDING_IDEA_KEY, JSON.stringify(idea));
            if (dayIso) sessionStorage.setItem(PLANNED_DAY_KEY, dayIso); else sessionStorage.removeItem(PLANNED_DAY_KEY);
        } catch (e) { /* storage blocked: Studio Chat opens without the idea */ }
        window.location.href = '/dashboard';
    }

    function visibleDays(data) {
        if (view !== 'week') return data.days;
        const start = weekStart || mondayOf(data.today);
        const end = isoDay(new Date(parseDay(start).getTime() + 7 * 864e5));
        return data.days.filter(d => d.date >= start && d.date < end);
    }

    function renderGrid(data) {
        grid.textContent = '';
        grid.classList.toggle('is-week', view === 'week');
        visibleDays(data).forEach(function (day) {
            const isToday = day.date === data.today;
            const cell = el('div', 'cal-day' + (day.in_month ? '' : ' is-outside') + (isToday ? ' is-today' : '')
                + (day.date < data.today ? ' is-past' : ''));
            const empty = !day.posts.length && !day.scheduled.length && !day.occasions.length && !day.suggestion && !day.autopilot;
            if (empty) cell.classList.add('is-empty');

            const head = el('div', 'cal-day-head');
            const number = el('span', 'cal-day-num', String(parseDay(day.date).getDate()));
            number.title = longDate(day.date);
            head.append(number, el('span', 'cal-day-name', parseDay(day.date).toLocaleDateString(undefined, { weekday: 'short' })));
            if (isToday) head.appendChild(el('span', 'cal-day-today', 'Today'));
            cell.appendChild(head);

            day.occasions.forEach(function (occasion) {
                const tag = el('div', 'cal-occasion');
                tag.title = occasion.name;
                tag.append(icon('fas fa-star'), el('span', '', occasion.name));
                cell.appendChild(tag);
            });
            day.posts.filter(p => shown(p.platforms)).forEach(function (post) { cell.appendChild(postChip(post)); });
            day.scheduled.filter(i => shown(i.platforms)).forEach(function (item) { cell.appendChild(scheduledChip(item)); });
            if (day.autopilot) {  // prepared by Autopilot, waiting for the user's approval
                const ap = el('a', 'cal-chip is-autopilot-waiting');
                ap.href = '/approve/' + day.autopilot.approval_request_id;
                ap.title = 'Autopilot prepared "' + (day.autopilot.idea_title || 'a post') + '". Approve it to schedule it.';
                ap.append(icon('fas fa-robot'), el('span', '', 'To approve: ' + (day.autopilot.idea_title || 'Autopilot post')));
                cell.appendChild(ap);
            }
            if (day.suggestion) cell.appendChild(slotChip(day));
            if (day.date >= data.today) {  // a scheduled post or a To plan card can be dropped on today or a later day
                cell.addEventListener('dragover', function (e) {
                    if (!dragged && !draggedIdea) return;
                    e.preventDefault();
                    e.dataTransfer.dropEffect = dragged ? 'move' : 'copy';
                    cell.classList.add('is-drop');
                });
                cell.addEventListener('dragleave', function () { cell.classList.remove('is-drop'); });
                cell.addEventListener('drop', function (e) {
                    e.preventDefault();
                    cell.classList.remove('is-drop');
                    if (dragged) moveToDay(dragged, day.date);
                    else if (draggedIdea) startIdea(draggedIdea, day.date);
                });
            }
            grid.appendChild(cell);
        });
    }

    function isoDay(date) {
        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
    }
    function mondayOf(iso) {
        const d = parseDay(iso);
        d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
        return isoDay(d);
    }
    function title(data) {
        if (view === 'week') {
            const start = parseDay(weekStart || mondayOf(data.today));
            const end = new Date(start.getTime() + 6 * 864e5);
            const sameMonth = start.getMonth() === end.getMonth();
            return start.toLocaleDateString(undefined, sameMonth ? { day: 'numeric' } : { day: 'numeric', month: 'short' })
                + ' - ' + end.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
        }
        const parts = data.month.split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    }

    // ── To plan: open days this week, ideas, occasions ───────────────
    function planCard(text, sub, iconClass, idea, dayIso) {
        const card = el('button', 'cal-plan-card');
        card.type = 'button';
        const label = el('span', 'cal-plan-text');
        label.append(el('strong', '', text), el('small', '', sub));
        card.append(icon(iconClass), label);
        if (idea) {
            card.title = 'Click to start it in Studio Chat, or drag it onto a day';
            card.draggable = true;
            card.addEventListener('dragstart', function (e) {
                draggedIdea = idea;
                e.dataTransfer.effectAllowed = 'copy';
                e.dataTransfer.setData('text/plain', idea.title || 'idea');
                grid.classList.add('is-dragging');
            });
            card.addEventListener('dragend', function () {
                draggedIdea = null;
                grid.classList.remove('is-dragging');
                grid.querySelectorAll('.is-drop').forEach(function (cell) { cell.classList.remove('is-drop'); });
            });
            card.addEventListener('click', function () { startIdea(idea, dayIso || null); });
        }
        return card;
    }
    const IDEA_GROUPS = { dates: 'Upcoming date', trending: 'Trending', playbook: 'Post type that works' };
    function renderPlan(data) {
        const plan = data.plan || { ideas: [], occasions: [] };
        const weekEnd = isoDay(new Date(parseDay(mondayOf(data.today)).getTime() + 7 * 864e5));
        const open = data.days.filter(d => d.suggestion && d.date >= data.today && d.date < weekEnd);
        const openList = $('calPlanOpen');
        openList.textContent = '';
        open.forEach(function (day) {
            const idea = day.suggestion.idea || { title: '', prompt: '' };
            openList.appendChild(planCard(parseDay(day.date).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' }),
                day.suggestion.idea ? day.suggestion.idea.title : 'A good day to post', 'fas fa-calendar-plus', idea, day.date));
        });
        if (!open.length) openList.appendChild(el('p', 'cal-plan-empty', data.progress.goal_met ? 'Your goal for this week is met.' : 'No open days left this week.'));

        const ideas = $('calPlanIdeas');
        ideas.textContent = '';
        plan.ideas.forEach(function (idea) {
            ideas.appendChild(planCard(idea.title, (IDEA_GROUPS[idea.group] || 'Idea') + (idea.platform ? ' · ' + (PLATFORM_NAMES[idea.platform] || idea.platform) : ''),
                idea.platform && PLATFORM_ICONS[idea.platform] ? 'fab ' + PLATFORM_ICONS[idea.platform] : 'fas fa-lightbulb', idea));
        });
        if (!plan.ideas.length) {
            ideas.appendChild(el('p', 'cal-plan-empty', data.has_brand_profile
                ? 'Open Ideas for you in Studio Chat to get ideas here.' : 'Add your website on your brand profile to get ideas here.'));
        }

        const occasions = $('calPlanOccasions');
        occasions.textContent = '';
        plan.occasions.forEach(function (o) {
            const days = Math.round((parseDay(o.date) - parseDay(data.today)) / 864e5);
            const when = days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : 'In ' + days + ' days · ' + parseDay(o.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
            occasions.appendChild(planCard(o.name, when + ' · ' + o.region, 'fas fa-star',
                { title: o.name + ' post', prompt: 'Create a post for ' + o.name + ' (' + o.region + ').', occasion: o.name }));
        });
        if (!plan.occasions.length) occasions.appendChild(el('p', 'cal-plan-empty', 'No occasions in your markets in the next 45 days.'));
    }

    // ── Posts that didn't publish ─────────────────────────────────────
    function renderAlerts(data) {
        const box = $('calAlerts');
        box.textContent = '';
        const failed = [];
        data.days.forEach(d => d.scheduled.forEach(item => { if (item.status === 'failed' || item.status === 'partial') failed.push(item); }));
        failed.slice(0, 3).forEach(function (item) {
            const when = new Date(item.scheduled_at);
            const alert = el('div', 'cal-alert');
            alert.setAttribute('role', 'alert');
            const reasons = Object.values(item.results || {}).filter(r => r && !r.ok && r.error).map(r => r.error);
            const text = el('span', 'cal-alert-text');
            text.append(el('strong', '', item.status === 'partial' ? 'Partly published: ' : "Didn't publish: "),
                document.createTextNode('"' + item.title + '", ' + when.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
                    + (reasons.length ? '. ' + reasons[0] : '.')));
            const retry = el('button', 'cal-btn is-primary', item.status === 'partial' ? 'Send the rest' : 'Retry');
            retry.type = 'button';
            retry.addEventListener('click', function () {
                retry.disabled = true;
                api('POST', '/api/social/scheduled/' + item.id + '/retry')
                    .then(function () { toast('Trying again - it goes out within a minute.'); load(shownMonth); })
                    .catch(function (err) { retry.disabled = false; toast(err.message); });
            });
            const details = el('button', 'cal-btn', 'Details');
            details.type = 'button';
            details.addEventListener('click', function () { openScheduled(item); });
            alert.append(icon('fas fa-triangle-exclamation'), text, details, retry);
            box.appendChild(alert);
        });
    }

    function render(data) {
        lastData = data;
        shownMonth = data.month;
        $('calMonth').textContent = title(data);
        renderGoal(data.progress);
        renderGrid(data);
        renderPlan(data);
        renderAlerts(data);
        $('calNote').textContent = data.has_brand_profile
            ? 'Suggested days come from your weekly goal; their ideas come from your "Ideas for you" in Studio Chat.'
            : 'Add your website in My Brand Configuration to get ideas on your suggested days.';
    }

    function load(month) {
        grid.classList.add('is-loading');
        fetch('/api/calendar?tz=' + tz + (month ? '&month=' + month : ''), { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (data) {
                if (!data.success) throw new Error(data.error);
                render(data);
            })
            .catch(function () { toast('Could not load your calendar. Please try again.'); })
            .finally(function () { grid.classList.remove('is-loading'); });
    }

    function shift(by) {
        if (view === 'week') {
            // a week belongs to the month of its Monday, whose grid holds it whole
            weekStart = isoDay(new Date(parseDay(weekStart || mondayOf(lastData.today)).getTime() + by * 7 * 864e5));
            const month = weekStart.slice(0, 7);
            if (month === shownMonth) render(lastData); else load(month);
            return;
        }
        const parts = shownMonth.split('-').map(Number);
        const next = new Date(parts[0], parts[1] - 1 + by, 1);
        load(next.getFullYear() + '-' + String(next.getMonth() + 1).padStart(2, '0'));
    }
    $('calPrev').addEventListener('click', function () { if (shownMonth) shift(-1); });
    $('calNext').addEventListener('click', function () { if (shownMonth) shift(1); });
    $('calToday').addEventListener('click', function () { weekStart = null; load(null); });

    // Month / Week
    function setView(next) {
        view = next;
        try { localStorage.setItem(VIEW_KEY, view); } catch (e) { /* storage blocked */ }
        document.querySelectorAll('[data-cal-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.calView === view)));
        if (!lastData) return;
        if (view === 'week' && !weekStart) {
            // this week when this month is shown, else the month's first week
            weekStart = lastData.month === lastData.today.slice(0, 7) ? mondayOf(lastData.today) : mondayOf(lastData.month + '-01');
        }
        render(lastData);
    }
    document.querySelectorAll('[data-cal-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.calView)));
    document.querySelectorAll('[data-cal-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.calView === view)));

    // Platform filters
    function syncFilters() {
        document.querySelectorAll('[data-cal-platform]').forEach(b => b.setAttribute('aria-pressed', String(platforms.has(b.dataset.calPlatform))));
    }
    document.querySelectorAll('[data-cal-platform]').forEach(function (b) {
        b.addEventListener('click', function () {
            const p = b.dataset.calPlatform;
            if (platforms.has(p)) { if (platforms.size > 1) platforms.delete(p); else return; } else platforms.add(p);
            try { localStorage.setItem(FILTER_KEY, JSON.stringify(Array.from(platforms))); } catch (e) { /* storage blocked */ }
            syncFilters();
            if (lastData) renderGrid(lastData);
        });
    });
    syncFilters();

    // Results, opened from the strip
    $('calResultsToggle').addEventListener('click', function () {
        const open = $('calResults').classList.toggle('d-none') === false;
        this.setAttribute('aria-expanded', String(open));
        this.textContent = open ? 'Hide results' : 'See results';
    });

    // Plan my week: bring the open days into view
    $('calPlanWeek').addEventListener('click', function () {
        const group = $('calPlanOpenGroup');
        group.scrollIntoView({ behavior: 'smooth', block: 'center' });
        group.classList.remove('is-flash');
        void group.offsetWidth;  // restart the highlight
        group.classList.add('is-flash');
        if (!$('calPlanOpen').querySelector('.cal-plan-card')) toast('Nothing open this week. Drag an idea onto a later day instead.');
    });

    // ── How published posts did ───────────────────────────────────────
    function number(n) { return Number(n || 0).toLocaleString(); }
    function loadResults() {
        fetch('/api/analytics?days=30', { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                const a = r && r.analytics;
                $('calResultsSummary').textContent = a && a.posts
                    ? 'Last 30 days: ' + number(a.posts) + ' post' + (a.posts === 1 ? '' : 's') + ' published'
                      + (a.engagement_rate === null || a.engagement_rate === undefined ? '' : ' · ' + a.engagement_rate + '% engagement')
                    : 'Nothing published through AVIR AI in the last 30 days';
                const kpis = $('calKpis');
                const top = $('calTopPosts');
                kpis.textContent = '';
                top.textContent = '';
                if (!a || !a.posts) {
                    top.appendChild(el('p', 'cal-results-empty',
                        'Nothing published through AVIR AI yet. Once you schedule or publish posts, their likes, comments and reach show up here.'));
                    return;
                }
                [['Posts published', a.posts], ['Likes', a.totals.likes], ['Comments', a.totals.comments],
                 ['Shares', a.totals.shares], ['Reach', a.totals.reach],
                 ['Engagement rate', a.engagement_rate === null ? '–' : a.engagement_rate + '%']].forEach(function (k) {
                    const tile = el('div', 'cal-kpi');
                    tile.append(el('strong', '', typeof k[1] === 'number' ? number(k[1]) : k[1]), el('span', '', k[0]));
                    kpis.appendChild(tile);
                });
                top.appendChild(el('h3', '', 'Top posts'));
                a.top_posts.forEach(function (p) {
                    const row = el(p.permalink ? 'a' : 'div', 'cal-top-post');
                    if (p.permalink) { row.href = p.permalink; row.target = '_blank'; row.rel = 'noopener noreferrer'; }
                    const name = el('span', 'cal-top-title');
                    name.append(icon('fab ' + (PLATFORM_ICONS[p.platform] || 'fa-share-nodes')), el('span', '', p.title || 'Post'));
                    const m = p.metrics || {};
                    const stats = el('span', 'cal-top-stats',
                        `${number(m.likes)} likes · ${number(m.comments)} comments` + (m.reach ? ` · ${number(m.reach)} reached` : ''));
                    row.append(name, stats);
                    if (p.error && !p.updated_at) row.title = p.error;
                    top.appendChild(row);
                });
                top.appendChild(el('p', 'cal-results-note', 'Numbers update every few hours for 30 days after a post goes out. Some platforms only share reach with business accounts.'));
            })
            .catch(function () { $('calResultsSummary').textContent = 'Results unavailable right now'; });
    }

    // ── Best times to post ────────────────────────────────────────────
    function loadBestTimes() {
        fetch('/api/best-times?tz=' + tz, { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                const box = $('calBestTimes');
                box.textContent = '';
                const platforms = Object.entries((r && r.platforms) || {});
                if (!platforms.length) return;
                box.appendChild(el('h3', '', 'Best times to post'));
                const list = el('div', 'cal-best-list');
                platforms.forEach(function ([platform, best]) {
                    const row = el('div', 'cal-best-item');
                    const name = el('span', 'cal-top-title');
                    name.append(icon('fab ' + (PLATFORM_ICONS[platform] || 'fa-share-nodes')), el('span', '', PLATFORM_NAMES[platform] || platform));
                    const when = el('span', 'cal-best-when', best.label);
                    const why = el('small', '', best.source === 'your_posts'
                        ? `From your ${best.posts} published posts`
                        : 'A common starting point - your own best times appear after about 8 posts');
                    const text = el('span', 'cal-best-text');
                    text.append(when, why);
                    row.append(name, text);
                    list.appendChild(row);
                });
                box.appendChild(list);
            })
            .catch(function () { /* the list stays empty */ });
    }

    // Autopilot's state in the strip (/api/autopilot, services/autopilot_service.py)
    function loadAutopilot() {
        fetch('/api/autopilot', { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                const a = r && r.autopilot;
                if (!a) return;
                const link = $('calAutopilot');
                const waiting = (a.needs_ok || []).length;
                link.classList.toggle('is-on', a.enabled && !a.paused_reason);
                link.querySelector('span').textContent = !a.enabled ? 'Autopilot: Off · Turn on'
                    : a.paused_reason ? 'Autopilot: Paused'
                        : 'Autopilot: On' + (waiting ? ' · ' + waiting + ' to approve' : '');
                link.href = '/autopilot';
            })
            .catch(function () { /* the link still opens the settings */ });
    }

    load(null);
    loadResults();
    loadBestTimes();
    loadAutopilot();
})();
