// Performance (templates/performance.html): how published posts did over 7, 30
// or 90 days, each post scored at 48 hours against the user's usual
// (/api/analytics?detail=1, db.post_analytics), what works for the brand
// (/api/insights, services/insights_service.py) and the best times to post
// (/api/best-times). Vanilla JS like calendar.js - no jQuery or Bootstrap JS.
(function () {
    const kpis = document.getElementById('perfKpis');
    if (!kpis) return;
    const $ = (id) => document.getElementById(id);
    const tz = -new Date().getTimezoneOffset();  // minutes ahead of UTC
    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const DAYS_KEY = 'avir_performance_days';
    let days = 30;
    try {
        const saved = Number(localStorage.getItem(DAYS_KEY));
        if ([7, 30, 90].includes(saved)) days = saved;
    } catch (e) { /* storage blocked: 30 days */ }

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
    function number(n) { return Number(n || 0).toLocaleString(); }
    function getJSON(url) {
        return fetch(url, { credentials: 'same-origin' }).then((r) => r.json());
    }

    // ── Summary tiles ─────────────────────────────────────────────────
    function change(now, before) {
        if (!before) return null;
        const pct = Math.round(100 * (now - before) / before);
        return { text: (pct >= 0 ? '+' : '') + pct + '% vs previous ' + days + ' days', good: pct >= 0 };
    }
    function tile(label, value, sub, tone) {
        const box = el('div', 'perf-kpi');
        box.append(el('span', 'perf-kpi-label', label), el('strong', '', value));
        if (sub) box.appendChild(el('span', 'perf-kpi-sub' + (tone ? ' is-' + tone : ''), sub));
        return box;
    }
    function renderKpis(a) {
        kpis.textContent = '';
        const prev = a.previous || { posts: 0, totals: {}, engagement_rate: null };
        const reach = change(a.totals.reach, prev.totals.reach);
        const followers = Object.values(a.followers || {});
        const counted = followers.some((f) => f.change !== null);
        const gained = followers.reduce((sum, f) => sum + (f.change || 0), 0);
        kpis.append(
            tile('Reach', a.totals.reach ? number(a.totals.reach) : '–',
                 reach ? reach.text : (a.totals.reach ? 'Nothing to compare with yet' : 'Some platforms only share reach with business accounts'),
                 reach ? (reach.good ? 'good' : 'bad') : ''),
            tile('Engagement rate', a.engagement_rate === null ? '–' : a.engagement_rate + '%',
                 prev.engagement_rate === null ? 'Likes, comments, shares and saves per person reached' : 'Previous ' + days + ' days: ' + prev.engagement_rate + '%',
                 prev.engagement_rate === null || a.engagement_rate === null ? '' : (a.engagement_rate >= prev.engagement_rate ? 'good' : 'bad')),
            tile('New followers', counted ? (gained >= 0 ? '+' : '') + number(gained) : '–',
                 counted ? followers.length + ' account' + (followers.length === 1 ? '' : 's') : 'Counted once a day from now on', counted ? (gained >= 0 ? 'good' : 'bad') : ''),
            tile('Posts published', number(a.posts), 'Previous ' + days + ' days: ' + number(prev.posts), ''));
    }

    // ── What works for you ────────────────────────────────────────────
    function renderInsights(r) {
        const box = $('perfInsights');
        box.textContent = '';
        const found = (r && r.insights) || [];
        $('perfInsightsSub').textContent = r && r.posts ? 'Learned from ' + r.posts + ' scored post' + (r.posts === 1 ? '' : 's') : '';
        if (!found.length) {
            box.appendChild(el('p', 'perf-empty',
                'Nothing clear yet. Once about 6 of your posts on a platform have reached 48 hours, AVIR compares them '
                + 'and shows what does better for you: formats, openings, length and hashtags.'));
            return;
        }
        found.forEach(function (f) {
            const row = el('div', 'perf-insight');
            row.append(el('span', 'perf-lift' + (f.lift < 1 ? ' is-down' : ''), Number(f.lift).toFixed(1) + '×'));
            const text = el('div', 'perf-insight-text');
            text.append(el('strong', '', f.text),
                el('span', 'perf-sub', NAMES[f.platform] + ' · ' + f.posts + ' posts vs ' + f.others + ' others'));
            row.appendChild(text);
            box.appendChild(row);
        });
    }

    // ── By platform ───────────────────────────────────────────────────
    function renderPlatforms(a) {
        const box = $('perfPlatforms');
        box.textContent = '';
        const rows = Object.entries(a.platforms || {}).map(([platform, p]) => ({
            platform, posts: p.posts,
            rate: p.reach ? Math.round(1000 * p.engagement / p.reach) / 10 : null,
            perPost: p.posts ? Math.round(p.engagement / p.posts) : 0,
        }));
        if (!rows.length) {
            box.appendChild(el('p', 'perf-empty', 'No published posts in this period.'));
            return;
        }
        // one scale for every bar: the rate where all platforms have reach, else engagement per post
        const byRate = rows.every((r) => r.rate !== null);
        const top = Math.max(...rows.map((r) => (byRate ? r.rate : r.perPost)), 1);
        rows.sort((x, y) => (byRate ? y.rate - x.rate : y.perPost - x.perPost)).forEach(function (r) {
            const item = el('div', 'perf-platform');
            const head = el('div', 'perf-platform-head');
            const name = el('span', 'perf-platform-name');
            name.append(icon('fab ' + (ICONS[r.platform] || 'fa-share-nodes')), el('span', '', NAMES[r.platform] || r.platform));
            head.append(name, el('span', 'perf-sub',
                (byRate ? r.rate + '% engagement' : number(r.perPost) + ' engagements a post') + ' · ' + r.posts + ' post' + (r.posts === 1 ? '' : 's')));
            const bar = el('div', 'perf-bar');
            const fill = el('span');
            fill.style.width = Math.max(4, Math.round(100 * (byRate ? r.rate : r.perPost) / top)) + '%';
            bar.appendChild(fill);
            item.append(head, bar);
            box.appendChild(item);
        });
    }
    function renderBestTimes(r) {
        const box = $('perfBestTimes');
        box.textContent = '';
        Object.entries((r && r.platforms) || {}).filter(([p]) => p !== 'youtube').forEach(function ([platform, t]) {
            const line = el('p', 'perf-sub');
            line.append(el('strong', '', (NAMES[platform] || platform) + ': '), document.createTextNode(t.label));
            if (t.source !== 'your_posts') line.appendChild(el('span', 'perf-typical', ' (common starting point)'));
            box.appendChild(line);
        });
    }

    // ── Your posts ────────────────────────────────────────────────────
    function scoreCell(p) {
        const cell = el('td');
        if (p.vs_usual !== null && p.vs_usual !== undefined) {
            const tone = p.vs_usual >= 1.5 ? 'up' : p.vs_usual >= 0.8 ? 'even' : 'down';
            cell.appendChild(el('span', 'perf-badge is-' + tone, Number(p.vs_usual).toFixed(1) + '× your usual'));
        } else if (p.score !== null && p.score !== undefined && !p.score_final) {
            cell.appendChild(el('span', 'perf-sub', 'Score comes at 48 hours'));
        } else if (p.score_final) {
            const s = el('span', 'perf-sub', number(p.score) + ' engagements');
            s.title = 'Compared with your usual once 5 posts on this platform have a score';
            cell.appendChild(s);
        } else {
            const s = el('span', 'perf-sub', '–');
            s.title = 'Published before scores were kept';
            cell.appendChild(s);
        }
        return cell;
    }
    function renderPosts(a) {
        const body = $('perfPosts');
        body.textContent = '';
        const posts = (a.all_posts || []).slice().sort((x, y) => y.published_at.localeCompare(x.published_at));
        $('perfPostsEmpty').classList.toggle('d-none', posts.length > 0);
        body.closest('.perf-table-wrap').classList.toggle('d-none', !posts.length);
        posts.forEach(function (p) {
            const m = p.metrics || {};
            const tr = el('tr');
            const post = el('td');
            const title = el('div', 'perf-post');
            const details = el('a', 'perf-post-link', p.title || 'Post');  // the post's own page: results and comments
            details.href = '/performance/posts/' + p.id;
            title.append(details,
                el('span', 'perf-sub', new Date(p.published_at).toLocaleString(undefined,
                    { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })));
            if (p.error && !p.updated_at) title.appendChild(el('span', 'perf-error', p.error));
            post.appendChild(title);
            const platform = el('td');
            const platformName = el('span', 'perf-platform-name');
            platformName.append(icon('fab ' + (ICONS[p.platform] || 'fa-share-nodes')), el('span', '', NAMES[p.platform] || p.platform));
            platform.appendChild(platformName);
            const link = el('td', 'num');
            const open = el('a', 'perf-open', 'Details');
            open.href = '/performance/posts/' + p.id;
            open.setAttribute('aria-label', 'Details and comments: ' + (p.title || 'post'));
            link.appendChild(open);
            tr.append(post, platform, scoreCell(p),
                el('td', 'num', m.reach ? number(m.reach) : '–'),
                el('td', 'num', number(p.engagement)),
                el('td', 'num', number(m.comments)),
                el('td', 'num', number((m.saves || 0) + (m.shares || 0))),
                link);
            body.appendChild(tr);
        });
    }

    // ── Do more of what worked ────────────────────────────────────────
    // A brief for Studio Chat, handed over like the Calendar's ideas (app_v2.js useIdea)
    function startBrief(idea) {
        try {
            sessionStorage.removeItem('avir_active_conversation');
            sessionStorage.setItem('avir_pending_idea', JSON.stringify(idea));
        } catch (e) { /* storage blocked: Studio Chat opens without it */ }
        window.location.href = '/dashboard';
    }
    function renderWinners(a) {
        const box = $('perfWinners');
        box.textContent = '';
        const winners = (a.all_posts || []).filter((p) => p.vs_usual >= 2)
            .sort((x, y) => y.vs_usual - x.vs_usual).slice(0, 3);
        $('perfWinnersCard').classList.toggle('d-none', !winners.length);
        winners.forEach(function (p) {
            const name = NAMES[p.platform] || p.platform;
            const title = p.title || 'your post';
            const row = el('div', 'perf-winner');
            const text = el('div', 'perf-insight-text');
            text.append(el('strong', '', title),
                el('span', 'perf-sub', Number(p.vs_usual).toFixed(1) + '× your usual on ' + name));
            const actions = el('div', 'perf-winner-actions');
            const again = el('button', 'perf-btn is-main', 'Write another like this');
            again.type = 'button';
            again.addEventListener('click', function () {
                startBrief({ title: 'Like "' + title + '"', platform: p.platform, format: 'text',
                    prompt: 'Write a new ' + name + ' post in the same style, angle and format as our post "' + title
                        + '", which got ' + p.vs_usual + '× our usual engagement. Pick a fresh topic; don\'t repeat it.' });
            });
            actions.appendChild(again);
            ['linkedin', 'instagram', 'facebook'].filter((x) => x !== p.platform).forEach(function (other) {
                const adapt = el('button', 'perf-btn', 'Adapt for ' + NAMES[other]);
                adapt.type = 'button';
                adapt.addEventListener('click', function () {
                    startBrief({ title: '"' + title + '" for ' + NAMES[other], platform: other, format: 'text',
                        prompt: 'Adapt our ' + name + ' post "' + title + '" for ' + NAMES[other]
                            + '. It did ' + p.vs_usual + '× our usual there. Keep the idea, fit it to ' + NAMES[other] + '.' });
                });
                actions.appendChild(adapt);
            });
            if (p.permalink) {
                const boost = el('a', 'perf-open', 'Boost on ' + name);
                boost.href = p.permalink; boost.target = '_blank'; boost.rel = 'noopener noreferrer';
                boost.title = 'Opens the post on ' + name + ', where its Boost button is';
                actions.appendChild(boost);
            }
            row.append(text, actions);
            box.appendChild(row);
        });
    }

    // ── Loading ───────────────────────────────────────────────────────
    function load() {
        document.querySelectorAll('.perf-period button').forEach(function (b) {
            b.setAttribute('aria-pressed', String(Number(b.dataset.days) === days));
        });
        getJSON('/api/analytics?detail=1&days=' + days)
            .then(function (r) {
                const a = r && r.analytics;
                if (!a) throw new Error('no analytics');
                renderKpis(a);
                renderWinners(a);
                renderPlatforms(a);
                renderPosts(a);
            })
            .catch(function () {
                kpis.textContent = '';
                kpis.appendChild(el('p', 'perf-empty', 'Results are unavailable right now. Please try again in a moment.'));
            });
    }
    document.querySelectorAll('.perf-period button').forEach(function (b) {
        b.addEventListener('click', function () {
            days = Number(b.dataset.days);
            try { localStorage.setItem(DAYS_KEY, String(days)); } catch (e) { /* storage blocked */ }
            load();
        });
    });
    load();
    // Not tied to the period: learned from the last 180 days
    getJSON('/api/insights').then(renderInsights).catch(function () { renderInsights(null); });
    getJSON('/api/best-times?tz=' + tz).then(renderBestTimes).catch(function () { /* the panel just stays empty */ });
})();
