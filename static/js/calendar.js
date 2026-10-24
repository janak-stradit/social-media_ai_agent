// Content Calendar (templates/calendar.html): this week's goal on top, the
// month below. Data: /api/calendar (services/calendar_service.py). Vanilla JS
// like user_menu.js - the page loads neither jQuery nor Bootstrap's JS.
(function () {
    const grid = document.getElementById('calGrid');
    if (!grid) return;
    const $ = (id) => document.getElementById(id);
    const tz = -new Date().getTimezoneOffset();  // minutes ahead of UTC: days follow the user's own clock
    const RING = 2 * Math.PI * 27;
    const PLATFORM_ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube', twitter: 'fa-x-twitter', threads: 'fa-threads' };
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

    // ── Scheduled posts: open, move, cancel, retry ────────────────────
    const STATUS_TEXT = { pending: 'Scheduled', publishing: 'Publishing', published: 'Published', partial: 'Partly published', failed: 'Not published' };
    const STATUS_ICONS = { pending: 'fa-clock', publishing: 'fa-spinner', published: 'fa-circle-check', partial: 'fa-circle-exclamation', failed: 'fa-triangle-exclamation' };
    const PLATFORM_NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube', twitter: 'X', threads: 'Threads' };
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
        const chip = el('button', 'cal-chip is-scheduled is-' + item.status);
        chip.type = 'button';
        chip.title = (STATUS_TEXT[item.status] || 'Scheduled') + ' at ' + item.time + ': ' + item.title
            + (item.movable ? ' (drag to another day to move it, or click for more)' : '');
        chip.append(icon('fas ' + (STATUS_ICONS[item.status] || 'fa-clock')), el('span', '', item.time + ' · ' + item.title));
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
            if (day.date >= data.today) {  // a scheduled post can be dropped on today or a later day
                cell.addEventListener('dragover', function (e) {
                    if (!dragged) return;
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    cell.classList.add('is-drop');
                });
                cell.addEventListener('dragleave', function () { cell.classList.remove('is-drop'); });
                cell.addEventListener('drop', function (e) {
                    e.preventDefault();
                    cell.classList.remove('is-drop');
                    if (dragged) moveToDay(dragged, day.date);
                });
            }
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

    // ── How published posts did ───────────────────────────────────────
    function number(n) { return Number(n || 0).toLocaleString(); }
    function loadResults() {
        fetch('/api/analytics?days=30', { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                const a = r && r.analytics;
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
            .catch(function () { /* the section stays empty */ });
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

    load(null);
    loadResults();
    loadBestTimes();
})();
