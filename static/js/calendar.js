// Content Calendar (templates/calendar.html): this week's goal on top, the
// month below. Data: /api/calendar (services/calendar_service.py). Vanilla JS
// like user_menu.js - the page loads neither jQuery nor Bootstrap's JS.
(function () {
    const grid = document.getElementById('calGrid');
    if (!grid) return;
    const $ = (id) => document.getElementById(id);
    const tz = -new Date().getTimezoneOffset();  // minutes ahead of UTC: days follow the user's own clock
    const RING = 2 * Math.PI * 27;
    const PLATFORM_ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const CONVERSATION_KEY = 'avir_active_conversation';  // Studio Chat reopens this conversation (app_v2.js)
    const PENDING_IDEA_KEY = 'avir_pending_idea';         // ...or starts a new post from this idea
    let shownMonth = null;  // "YYYY-MM"
    let goal = 3;

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
        const fill = $('calRingFill');
        fill.style.strokeDasharray = RING;
        fill.style.strokeDashoffset = RING * (1 - share);
        $('calRing').classList.toggle('is-met', p.goal_met);
        $('calRing').setAttribute('aria-label', p.done + ' of ' + p.goal + ' posts created this week');
        $('calGoalText').textContent = p.goal_met
            ? (p.done > p.goal ? 'Goal reached, and then some: ' + p.done + ' posts created this week.' : 'Goal reached. Nice work this week.')
            : (p.done === 0
                ? 'No posts yet this week. Pick a suggested day below to get started.'
                : p.remaining + ' more ' + (p.remaining === 1 ? 'post' : 'posts') + ' to reach your goal this week.');

        const week = $('calWeek');
        week.textContent = '';
        p.days.forEach(function (day) {
            const dot = el('span', 'cal-week-day' + (day.count ? ' has-post' : '') + (day.date === p.today ? ' is-today' : ''));
            dot.append(el('small', '', parseDay(day.date).toLocaleDateString(undefined, { weekday: 'narrow' })), el('b', '', day.count ? String(day.count) : ''));
            week.appendChild(dot);
        });

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

    function scheduledChip(item) {
        const chip = el('a', 'cal-chip is-scheduled');
        chip.href = '/settings';
        chip.title = (item.status === 'published' ? 'Published' : 'Scheduled') + ' at ' + item.time;
        chip.append(icon('fas ' + (item.status === 'published' ? 'fa-circle-check' : 'fa-clock')),
            el('span', '', item.time + ' · ' + (item.platforms.join(', ') || 'post')));
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

    function renderGrid(data) {
        grid.textContent = '';
        data.days.forEach(function (day) {
            const isToday = day.date === data.today;
            const cell = el('div', 'cal-day' + (day.in_month ? '' : ' is-outside') + (isToday ? ' is-today' : '')
                + (day.date < data.today ? ' is-past' : ''));
            const empty = !day.posts.length && !day.scheduled.length && !day.occasions.length && !day.suggestion;
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
            day.posts.forEach(function (post) { cell.appendChild(postChip(post)); });
            day.scheduled.forEach(function (item) { cell.appendChild(scheduledChip(item)); });
            if (day.suggestion) cell.appendChild(slotChip(day));
            grid.appendChild(cell);
        });
    }

    function render(data) {
        shownMonth = data.month;
        const parts = data.month.split('-').map(Number);
        $('calMonth').textContent = new Date(parts[0], parts[1] - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        renderGoal(data.progress);
        renderGrid(data);
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
        const parts = shownMonth.split('-').map(Number);
        const next = new Date(parts[0], parts[1] - 1 + by, 1);
        load(next.getFullYear() + '-' + String(next.getMonth() + 1).padStart(2, '0'));
    }
    $('calPrev').addEventListener('click', function () { if (shownMonth) shift(-1); });
    $('calNext').addEventListener('click', function () { if (shownMonth) shift(1); });
    $('calToday').addEventListener('click', function () { load(null); });

    load(null);
})();
