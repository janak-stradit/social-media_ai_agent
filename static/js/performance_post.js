// One published post (templates/performance_post.html): its numbers, how its
// engagement grew fetch by fetch against the usual at 48 hours, why it likely
// did this well (the brand's own findings for its traits) and its comments,
// each opening in the Inbox to reply. Data: /api/analytics/posts/<id>
// (db.post_detail, insights_service.explain). Vanilla JS like performance.js.
(function () {
    const main = document.getElementById('postMain');
    if (!main) return;
    const $ = (id) => document.getElementById(id);
    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const KINDS = { question: 'Question', lead: 'Possible lead', praise: 'Praise', complaint: 'Complaint', other: 'Comment' };
    const SVG = 'http://www.w3.org/2000/svg';

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
    function svg(tag, attrs) {
        const node = document.createElementNS(SVG, tag);
        Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
        return node;
    }
    function number(n) { return Number(n || 0).toLocaleString(); }
    // A brief for Studio Chat, handed over like the Calendar's ideas (app_v2.js useIdea)
    function startBrief(idea) {
        try {
            sessionStorage.removeItem('avir_active_conversation');
            sessionStorage.setItem('avir_pending_idea', JSON.stringify(idea));
        } catch (e) { /* storage blocked: Studio Chat opens without it */ }
        window.location.href = '/dashboard';
    }
    function button(text, main, onClick) {
        const b = el('button', 'perf-btn' + (main ? ' is-main' : ''), text);
        b.type = 'button';
        b.addEventListener('click', onClick);
        return b;
    }

    function renderHead(p) {
        const name = NAMES[p.platform] || p.platform;
        document.title = (p.title || 'Post') + ' — AVIR AI';
        $('postTitle').textContent = p.title || 'Post';
        const meta = $('postMeta');
        meta.textContent = '';
        meta.append(icon('fab ' + (ICONS[p.platform] || 'fa-share-nodes')), document.createTextNode(' ' + name + ' · published '
            + new Date(p.published_at).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })));
        if (p.permalink) {
            const open = el('a', 'perf-open', 'Open on ' + name);
            open.href = p.permalink; open.target = '_blank'; open.rel = 'noopener noreferrer';
            meta.append(document.createTextNode(' · '), open);
        }
        const badges = $('postBadges');
        badges.textContent = '';
        if (p.vs_usual) {
            const tone = p.vs_usual >= 1.5 ? 'up' : p.vs_usual >= 0.8 ? 'even' : 'down';
            badges.appendChild(el('span', 'perf-badge is-' + tone, Number(p.vs_usual).toFixed(1) + '× your usual'));
        } else if (p.score !== null && !p.score_final) {
            badges.appendChild(el('span', 'perf-badge is-even', 'Score comes at ' + p.score_age_hours + ' hours'));
        }
        const title = p.title || 'this post';
        const actions = $('postActions');
        actions.textContent = '';
        actions.appendChild(button('Write another like this', true, function () {
            startBrief({ title: 'Like "' + title + '"', platform: p.platform, format: 'text',
                prompt: 'Write a new ' + name + ' post in the same style, angle and format as our post "' + title
                    + '"' + (p.vs_usual ? ', which got ' + p.vs_usual + '× our usual engagement' : '') + '. Pick a fresh topic; don\'t repeat it.' });
        }));
        ['linkedin', 'instagram', 'facebook'].filter((x) => x !== p.platform).forEach(function (other) {
            actions.appendChild(button('Adapt for ' + NAMES[other], false, function () {
                startBrief({ title: '"' + title + '" for ' + NAMES[other], platform: other, format: 'text',
                    prompt: 'Adapt our ' + name + ' post "' + title + '" for ' + NAMES[other] + '. Keep the idea, fit it to ' + NAMES[other] + '.'
                        + (p.item && p.item.caption ? '\n\nThe original post:\n' + p.item.caption : '') });
            }));
        });
    }

    function tile(label, value, sub) {
        const box = el('div', 'perf-kpi');
        box.append(el('span', 'perf-kpi-label', label), el('strong', '', value));
        if (sub) box.appendChild(el('span', 'perf-kpi-sub', sub));
        return box;
    }
    function renderStats(p) {
        const m = p.metrics || {};
        const box = $('postStats');
        box.textContent = '';
        box.append(
            tile('Reach', m.reach ? number(m.reach) : '–', m.reach ? '' : 'Not shared for this account'),
            tile('Likes', number(m.likes)),
            tile('Comments', number(m.comments)),
            tile('Shares', number(m.shares)),
            tile('Saves', m.saves === undefined ? '–' : number(m.saves)),
            tile('Engagement rate', m.reach ? Math.round(1000 * p.engagement / m.reach) / 10 + '%' : '–',
                 p.usual ? 'Usual at 48h: ' + number(Math.round(p.usual)) + ' engagements' : ''));
    }

    // Engagement at each fetch, the usual at 48 hours as a dashed line
    function renderCurve(p) {
        const fig = $('postCurve');
        fig.textContent = '';
        const points = p.snapshots || [];
        $('postCurveLegend').textContent = p.usual ? 'Dashed: your usual at ' + p.score_age_hours + ' hours' : '';
        if (points.length < 2) {
            fig.appendChild(el('p', 'perf-empty', points.length
                ? 'Only one fetch so far. The curve fills in as AVIR checks the post again (every 6 hours at first).'
                : 'No results kept for this post yet. Posts published before scores were kept have no curve.'));
            return;
        }
        const W = 640, H = 240, L = 44, R = 12, T = 14, B = 30;
        const maxAge = Math.max(p.score_age_hours, points[points.length - 1].age_hours);
        const maxY = Math.max(...points.map((s) => s.engagement), p.usual || 0, 1) * 1.1;
        const x = (age) => L + (W - L - R) * age / maxAge;
        const y = (v) => T + (H - T - B) * (1 - v / maxY);
        const last = points[points.length - 1];
        const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img',
            'aria-label': 'Engagement over time: ' + last.engagement + ' after ' + last.age_hours + ' hours'
                + (p.usual ? '; your usual post has ' + Math.round(p.usual) + ' at ' + p.score_age_hours + ' hours' : '') });
        [0, 0.5, 1].forEach(function (f) {
            const v = Math.round(maxY / 1.1 * f);
            chart.append(svg('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: '#e2e8f0' }));
            const t = svg('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end', fill: '#64748b', 'font-size': 11 });
            t.textContent = number(v);
            chart.append(t);
        });
        const ticks = [0, p.score_age_hours, maxAge].filter((v, i, a) => a.indexOf(v) === i);
        ticks.forEach(function (age) {
            const t = svg('text', { x: x(age), y: H - 8, 'text-anchor': age === 0 ? 'start' : age === maxAge ? 'end' : 'middle', fill: '#64748b', 'font-size': 11 });
            t.textContent = age < 72 ? age + 'h' : Math.round(age / 24) + 'd';
            chart.append(t);
        });
        chart.append(svg('line', { x1: x(p.score_age_hours), x2: x(p.score_age_hours), y1: T, y2: H - B, stroke: '#cbd5e1', 'stroke-dasharray': '2 3' }));
        if (p.usual) {
            chart.append(svg('line', { x1: L, x2: W - R, y1: y(p.usual), y2: y(p.usual), stroke: '#64748b', 'stroke-width': 2, 'stroke-dasharray': '5 4' }));
        }
        chart.append(svg('polyline', { fill: 'none', stroke: '#e85a1c', 'stroke-width': 3, 'stroke-linejoin': 'round',
            points: [{ age_hours: 0, engagement: 0 }].concat(points).map((s) => x(s.age_hours) + ',' + y(s.engagement)).join(' ') }));
        points.forEach(function (s) {
            const dot = svg('circle', { cx: x(s.age_hours), cy: y(s.engagement), r: 4, fill: '#e85a1c' });
            const tip = svg('title');
            tip.textContent = number(s.engagement) + ' engagements at ' + s.age_hours + ' hours';
            dot.append(tip);
            chart.append(dot);
        });
        fig.appendChild(chart);
    }

    function renderWhy(p) {
        const box = $('postWhy');
        box.textContent = '';
        if (!p.why.length) {
            box.appendChild(el('li', 'perf-empty', 'What was posted isn\'t available for this post.'));
            return;
        }
        p.why.forEach(function (w) {
            const li = el('li');
            const dot = el('span', 'post-why-dot' + (w.lift === null ? '' : w.lift >= 1 ? ' is-up' : ' is-down'));
            dot.setAttribute('aria-hidden', 'true');
            const text = el('span');
            text.append(el('strong', '', w.label), el('span', 'perf-sub', w.lift === null
                ? 'Nothing clear yet from your other posts'
                : 'Your ' + (NAMES[p.platform] || p.platform) + ' posts like this get ' + Number(w.lift).toFixed(1) + '× your others'));
            li.append(dot, text);
            box.appendChild(li);
        });
    }

    function renderComments(p) {
        const box = $('postComments');
        box.textContent = '';
        $('postCommentsTitle').textContent = 'Comments' + (p.comments.length ? ' · ' + p.comments.length : '');
        if (!p.comments.length) {
            box.appendChild(el('li', 'perf-empty', (p.metrics && p.metrics.comments)
                ? 'This post has comments, but AVIR couldn\'t read them yet. Reading comments needs the comment permissions on your connected account.'
                : 'No comments yet. New ones show up here and in your Inbox.'));
            return;
        }
        p.comments.forEach(function (c) {
            const li = el('li');
            const top = el('div', 'post-comment-top');
            top.append(el('strong', '', c.author || 'Someone'),
                el('span', 'perf-sub', new Date(c.commented_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })));
            if (c.kind) top.appendChild(el('span', 'inbox-kind is-' + c.kind, KINDS[c.kind] || c.kind));
            const state = el('span', 'post-comment-state is-' + c.status,
                c.status === 'replied' ? (c.replied_privately ? 'Replied privately' : 'Replied') : c.status === 'done' ? 'Done' : 'Needs reply');
            top.appendChild(state);
            li.append(top, el('p', '', c.text));
            if (c.status === 'replied' && c.reply) li.appendChild(el('p', 'post-comment-reply', 'You: ' + c.reply));
            const link = el('a', 'perf-open', c.status === 'new' ? 'Reply in Inbox' : 'Open in Inbox');
            link.href = '/inbox?comment=' + c.id;
            li.appendChild(link);
            box.appendChild(li);
        });
    }

    function renderText(p) {
        $('postCaption').textContent = p.item && p.item.caption ? p.item.caption : 'The text of this post isn\'t available.';
        $('postTags').textContent = p.item && p.item.hashtags && p.item.hashtags.length ? p.item.hashtags.join(' ') : '';
    }

    fetch('/api/analytics/posts/' + main.dataset.postId, { credentials: 'same-origin' })
        .then((r) => r.json().then((data) => ({ ok: r.ok, data: data })))
        .then(function (r) {
            if (!r.ok || !r.data.post) {
                $('postTitle').textContent = 'Post not found';
                $('postMeta').textContent = 'It may have been removed, or it belongs to another account.';
                return;
            }
            const p = r.data.post;
            renderHead(p);
            renderStats(p);
            renderCurve(p);
            renderWhy(p);
            renderComments(p);
            renderText(p);
        })
        .catch(function () { $('postTitle').textContent = 'Results are unavailable right now'; });
})();
