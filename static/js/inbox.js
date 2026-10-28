// Inbox (templates/inbox.html): comments on published posts, each with a
// suggested reply in the brand's voice (POST /api/inbox/<id>/suggest, one small
// AI call, made when a comment without one is opened). The person edits it and
// approves it; only then is it posted (POST /api/inbox/<id>/reply).
// Data: /api/inbox (services/comment_service.py). Vanilla JS like calendar.js.
(function () {
    const list = document.getElementById('inboxList');
    if (!list) return;
    const $ = (id) => document.getElementById(id);
    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook' };
    const KINDS = { question: 'Question', lead: 'Possible lead', praise: 'Praise', complaint: 'Complaint', other: 'Comment' };
    let status = 'new';
    let comments = [];
    let openId = null;

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
    function ago(iso) {
        const minutes = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
        if (minutes < 60) return minutes + ' min';
        if (minutes < 60 * 24) return Math.round(minutes / 60) + ' h';
        return Math.round(minutes / 1440) + ' d';
    }
    function post(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        }).then((r) => r.json().then((data) => ({ ok: r.ok, data: data })));
    }
    function replace(updated) {
        comments = comments.map((c) => (c.id === updated.id ? updated : c));
    }

    // ── The list ──────────────────────────────────────────────────────
    function renderList() {
        list.textContent = '';
        if (!comments.length) {
            list.appendChild(el('li', 'inbox-empty', status === 'new'
                ? 'Nothing waiting for a reply. New comments on your published posts show up here.'
                : 'Nothing here yet.'));
            return;
        }
        comments.forEach(function (c) {
            const li = el('li');
            const row = el('button', 'inbox-row-btn' + (c.id === openId ? ' is-open' : ''));
            row.type = 'button';
            if (c.id === openId) row.setAttribute('aria-current', 'true');
            const avatar = el('span', 'inbox-avatar', (c.author || '?').replace(/^@/, '').charAt(0).toUpperCase());
            avatar.setAttribute('aria-hidden', 'true');
            const body = el('span', 'inbox-row-body');
            const top = el('span', 'inbox-row-top');
            top.append(el('strong', '', c.author || 'Someone'), el('span', 'perf-sub', ago(c.commented_at)));
            const meta = el('span', 'inbox-row-meta');
            if (c.kind) meta.appendChild(el('span', 'inbox-kind is-' + c.kind, KINDS[c.kind] || c.kind));
            meta.append(icon('fab ' + (ICONS[c.platform] || 'fa-share-nodes')), el('span', '', NAMES[c.platform] || c.platform));
            body.append(top, el('span', 'inbox-row-text', c.text), meta);
            row.append(avatar, body);
            row.addEventListener('click', function () { open(c.id); });
            li.appendChild(row);
            list.appendChild(li);
        });
    }
    function renderCounts(counts) {
        document.querySelectorAll('[data-count]').forEach(function (span) {
            const n = counts[span.dataset.count] || 0;
            span.textContent = n ? '· ' + n : '';
        });
    }
    function load() {
        document.querySelectorAll('.inbox-filters button').forEach(function (b) {
            b.setAttribute('aria-pressed', String(b.dataset.status === status));
        });
        fetch('/api/inbox?status=' + status, { credentials: 'same-origin' })
            .then((r) => r.json())
            .then(function (r) {
                comments = r.comments || [];
                renderCounts(r.counts || {});
                if (!comments.some((c) => c.id === openId)) openId = null;
                renderList();
                // on wide screens the newest is shown, without spending an AI call until asked
                if (openId) renderDetail(); else if (comments.length && window.innerWidth > 900) open(comments[0].id, true);
                else clearDetail();
            })
            .catch(function () {
                list.textContent = '';
                list.appendChild(el('li', 'inbox-empty', 'The inbox is unavailable right now. Please try again in a moment.'));
            });
    }

    // ── One comment ───────────────────────────────────────────────────
    function clearDetail() {
        const box = $('inboxDetail');
        box.textContent = '';
        box.appendChild(el('p', 'perf-empty', 'Pick a comment to see it and its suggested reply.'));
    }
    function open(id, shownForYou) {
        openId = id;
        renderList();
        renderDetail();
        const c = comments.find((x) => x.id === id);
        if (c && c.status === 'new' && !c.draft && !shownForYou) suggest(c);  // one small AI call, when the person opens it
    }
    function suggest(c) {
        c._loading = true;
        renderDetail();
        post('/api/inbox/' + c.id + '/suggest').then(function (r) {
            if (r.ok && r.data.comment) { replace(r.data.comment); renderList(); }
            else { c._error = (r.data && r.data.error) || 'Couldn\'t suggest a reply right now.'; }
        }).catch(function () { c._error = 'Couldn\'t suggest a reply right now.'; })
            .finally(function () { c._loading = false; if (openId === c.id) renderDetail(); });
    }
    function renderDetail() {
        const c = comments.find((x) => x.id === openId);
        const box = $('inboxDetail');
        if (!c) { clearDetail(); return; }
        box.textContent = '';

        const context = el(c.post_permalink ? 'a' : 'div', 'inbox-post');
        if (c.post_permalink) { context.href = c.post_permalink; context.target = '_blank'; context.rel = 'noopener noreferrer'; }
        context.append(icon('fab ' + (ICONS[c.platform] || 'fa-share-nodes')),
            el('span', '', (c.post_title || 'Your post') + ' · ' + (NAMES[c.platform] || c.platform)));
        const who = el('h2', '', c.author || 'Someone');
        who.appendChild(el('span', 'perf-sub', ' · ' + ago(c.commented_at) + ' ago'));
        box.append(context, who, el('p', 'inbox-comment', c.text));

        if (c.status === 'replied') {
            const done = el('div', 'inbox-replied');
            done.append(el('strong', '', c.replied_privately ? 'Your private message' : 'Your reply'), el('p', '', c.reply || ''));
            box.appendChild(done);
            return;
        }

        const card = el('div', 'inbox-reply');
        const head = el('div', 'inbox-reply-head');
        head.append(el('label', '', 'Suggested reply'));
        head.querySelector('label').htmlFor = 'inboxReplyText';
        if (c.kind) head.appendChild(el('span', 'inbox-kind is-' + c.kind, KINDS[c.kind] || c.kind));
        card.appendChild(head);
        if (c._loading) {
            card.appendChild(el('p', 'perf-sub', 'Writing a reply in your brand voice...'));
        } else {
            const text = el('textarea', 'inbox-reply-text');
            text.id = 'inboxReplyText';
            text.rows = 4;
            text.value = c.draft || '';
            text.placeholder = 'Write your reply';
            card.appendChild(text);
            if (c.note) card.appendChild(el('p', 'inbox-note', c.note));
            const error = c._error || c.reply_error;
            if (error && error !== c.note) card.appendChild(el('p', 'inbox-error', error));  // not the note said twice
        }
        box.appendChild(card);

        const actions = el('div', 'inbox-actions');
        function send(button, isPrivate) {
            const value = $('inboxReplyText').value;
            button.disabled = true;
            post('/api/inbox/' + c.id + '/reply', { text: value, private: isPrivate }).then(function (r) {
                if (r.ok) {
                    toast(isPrivate ? 'Private message sent to ' + (c.author || 'them') + '.' : 'Reply posted on ' + (NAMES[c.platform] || c.platform) + '.');
                    load();
                } else { c.draft = value; c._error = (r.data && r.data.error) || 'The reply wasn\'t sent.'; renderDetail(); }
            }).catch(function () { c._error = 'The reply wasn\'t sent. Please try again.'; renderDetail(); });
        }
        const approve = el('button', 'btn btn-primary', 'Approve and post reply');
        approve.type = 'button';
        approve.disabled = !!c._loading;
        approve.addEventListener('click', function () { send(approve, false); });
        actions.appendChild(approve);
        // Meta's private replies: one per comment, within 7 days of it (comment_service.can_reply_privately)
        const days = (Date.now() - new Date(c.commented_at).getTime()) / 86400000;
        if ((c.platform === 'facebook' || c.platform === 'instagram') && days < 7) {
            const dm = el('button', 'btn btn-outline-primary', 'Send as private message');
            dm.type = 'button';
            dm.disabled = !!c._loading;
            dm.title = 'Sends the reply to ' + (c.author || 'them') + ' as a direct message instead of under the post';
            dm.addEventListener('click', function () { send(dm, true); });
            actions.appendChild(dm);
        }
        const another = el('button', 'btn btn-outline-secondary', c.draft ? 'Suggest another' : 'Suggest a reply');
        another.type = 'button';
        another.disabled = !!c._loading;
        another.addEventListener('click', function () { c._error = null; suggest(c); });
        const later = el('button', 'btn btn-link inbox-done', c.status === 'done' ? 'Move back to Needs reply' : 'Mark as done');
        later.type = 'button';
        later.addEventListener('click', function () {
            post('/api/inbox/' + c.id + '/status', { status: c.status === 'done' ? 'new' : 'done' }).then(function (r) {
                if (r.ok) { toast(c.status === 'done' ? 'Moved back.' : 'Marked as done.'); load(); }
            });
        });
        actions.append(another, later);
        box.appendChild(actions);
    }

    document.querySelectorAll('.inbox-filters button').forEach(function (b) {
        b.addEventListener('click', function () { status = b.dataset.status; openId = null; load(); });
    });
    load();
})();
