// In-app guide: the "Get started" checklist, a first-visit tour per page, and
// the "?" tips. Loaded by templates/partials/guide_assets.html on Studio Chat,
// Brand Profile, Approval Requests (list + review) and Settings.
// Server side: services/guide_service.py (GET /api/guide, POST /api/guide/state).
// Each checklist step is ticked from what the user has really done; tours seen
// are stored on the account, so they don't repeat on another device. Tours
// use Driver.js; without it (offline) everything else still works.
(function () {
    const state = { data: null, open: false, justCompleted: false };
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    const PAGE = path === '/dashboard' ? 'studio'
        : path === '/brand-profile' ? 'brand'
        : path === '/approve' ? 'approvals'
        : /^\/approve\/\d+$/.test(path) ? 'review'
        : path === '/settings' ? 'settings' : null;

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function post(body) {
        return $.ajax({ url: '/api/guide/state', type: 'POST', contentType: 'application/json', data: JSON.stringify(body) })
            .done(r => { if (r && r.steps) { state.data = r; render(); } });
    }

    // ── Tours: one per page ──────────────────────────────────────────────
    const TOURS = {
        studio: [
            { popover: { title: 'Welcome to Studio Chat', description: 'This is where your posts are made. Here\'s a one-minute look at how it works.' } },
            { element: '#storyInput', popover: { title: '1. Describe your post', description: 'Say what you want to post in a sentence or two, for example <em>"Announce our Diwali offer"</em>. Press Enter to send.' } },
            { element: '#tourPlatforms', popover: { title: '2. Choose where it goes', description: 'Pick one or more platforms. Each one gets its own caption, written in that platform\'s style.' } },
            { element: '#tourOutputs', popover: { title: '3. Choose what to make', description: 'A caption is always written. Tick <strong>Image</strong> to get a picture too. The number of images left today shows here.' } },
            { element: '#attachBtn', popover: { title: 'Image commands', description: 'Attach a product photo, then type <strong>/</strong> to turn it into an ad, a studio shot, or a <strong>/carousel</strong> of slides.' } },
            { element: '#ideaFeedBar', popover: { title: 'Not sure what to post?', description: '<strong>Ideas for you</strong> suggests posts from your industry\'s news and upcoming festivals. Click one to load it.' } },
            { element: '#generateBtn', popover: { title: '4. Generate', description: 'Your post appears here in the chat. Type a change like <em>"make it shorter"</em> to refine it, or click <strong>Edit</strong> to change the text yourself. Then <strong>Send for approval</strong>.' } },
            { element: '#newChatBtn', popover: { title: 'Conversations', description: 'Each conversation remembers its posts and images. Start a new one for a new topic; older ones are listed below.' } },
            { element: '#guideChecklistBtn', popover: { title: 'Your checklist', description: 'These steps take you from your first post to publishing. Each one ticks itself off as you do it.' } },
            { element: '#helpCenterBtn', popover: { title: 'Help, any time', description: 'The Help Center has a short guide for every feature. You can take this tour again from the menu under your name.' } },
        ],
        brand: [
            { popover: { title: 'Your brand profile', description: 'Everything AVIR AI writes and designs follows this page. It was filled in from your website: take a minute to check it.' } },
            { element: '#bpEmptyState', popover: { title: 'Start with your website', description: 'Enter your website address and AVIR AI reads it to build your profile: what you sell, how you sound, your colours and logo.' } },
            { element: '#bpOverview', popover: { title: 'Who you are', description: 'Your company, industry and audience. Correct anything that\'s wrong: posts are written for this audience.' } },
            { element: '#bpVoice', popover: { title: 'How you sound', description: 'Your tone of voice and the themes you talk about. Captions copy this style.' } },
            { element: '#bpColors', popover: { title: 'How you look', description: 'Your colours and fonts are used as accents in images. Your real logo file is placed on images as it is, never redrawn.' } },
            { element: '#bpRules', popover: { title: 'Do\'s and don\'ts', description: 'Things to always or never say. Add your own, for example <em>"never mention prices"</em>.' } },
            { element: '#bpCompliance', popover: { title: 'Industry and markets', description: 'These decide which advertising rules your captions are checked against, and which festivals appear in Ideas for you.' } },
            { element: '#bpRescanBtn', popover: { title: 'Website changed?', description: '<strong>Re-analyze</strong> reads your site again and fetches your logo afresh, for example after a rebrand.' } },
            { popover: { title: 'Save your changes', description: 'When you change anything, a bar appears at the bottom with <strong>Save all</strong>: it saves every section at once (or press Ctrl+S). Edits apply to the next post you generate.' } },
        ],
        approvals: [
            { popover: { title: 'Approval requests', description: 'Every post sent for review is listed here: the ones you sent, and the ones sent to you to review.' } },
            { element: '#approvalStatusTabs', popover: { title: 'Filter by status', description: '<strong>Pending</strong> are waiting for a decision. <strong>Partly approved</strong> means some platforms were approved and changes were asked on others.' } },
            { element: '#approvalListBody', popover: { title: 'Open a request', description: 'Click one to preview the post on each platform, see the reviewer\'s comments, or record your decision.' } },
            { element: '#helpCenterBtn', popover: { title: 'Sending a post', description: 'To send one, go to Studio Chat and click <strong>Send for approval</strong> on any post. The Help Center explains each step.' } },
        ],
        review: [
            { element: '.preview-platform-tabs', popover: { title: 'One tab per platform', description: 'Each platform has its own version of the post. The dot shows its decision: grey to review, green approved, amber changes requested.' } },
            { element: '#approvalPreviewArea', popover: { title: 'Preview', description: 'This is how the post will look. For a carousel, use the arrows to go through the slides.' } },
            { element: '#apBundleDecision, .ap-decision-actions', popover: { title: 'Decide', description: 'Approve each platform, or ask for changes with a comment. <strong>Approve all</strong> does everything in one click.' } },
        ],
        settings: [
            { popover: { title: 'Social accounts', description: 'Connect your accounts here once. After that, approved posts can be published from AVIR AI.' } },
            { element: '.card-facebook', popover: { title: 'Connect an account', description: 'Click <strong>Configure</strong> and enter the Page ID and its access token, then <strong>Verify API</strong> to check the connection works.' } },
            { element: '.card-instagram', popover: { title: 'Instagram', description: 'Needs an Instagram Business or Creator account linked to a Facebook Page. With your Page connected, one click connects Instagram.' } },
            { element: '#setup-guides-tab', popover: { title: 'Setup guides', description: 'Step-by-step instructions for getting the IDs and tokens each platform asks for.' } },
            { element: '#scheduled-posts-tab', popover: { title: 'Scheduled posts', description: 'Posts waiting to go out, and what has already been published.' } },
            { element: '#notifications-tab', popover: { title: 'Emails', description: 'Choose which emails you get: weekly ideas, festival reminders and the monthly recap.' } },
        ],
    };

    const visible = sel => { const el = document.querySelector(sel); return el && el.offsetParent !== null ? el : null; };

    function startTour(name) {
        const lib = window.driver && window.driver.js && window.driver.js.driver;
        // Studio Chat: the platform / output steps point into the composer's Options panel
        if (name === 'studio' && typeof window.openComposerOptions === 'function') window.openComposerOptions();
        const steps = (TOURS[name] || []).filter(s => !s.element || visible(s.element));
        if (!lib || steps.length < 2) return false;
        setOpen(false);
        closeTip();
        lib({
            showProgress: true, allowClose: true, overlayOpacity: 0.55, stagePadding: 6, stageRadius: 10, smoothScroll: true,
            popoverClass: 'avir-tour', nextBtnText: 'Next', prevBtnText: 'Back', doneBtnText: 'Done',
            progressText: '{{current}} of {{total}}',
            steps: steps,
            onDestroyed: function () {
                // Finished or skipped: either way don't show it again uninvited
                post({ tour_seen: name });
                if (name === 'studio' && state.data && !state.data.complete && !state.data.dismissed) setOpen(true);
            },
        }).drive();
        return true;
    }

    // ── Checklist (header pill + panel) ──────────────────────────────────
    function ensureChecklistUi() {
        if (document.getElementById('guideChecklist')) return;
        const help = document.getElementById('helpCenterBtn');
        if (!help) return;
        help.insertAdjacentHTML('beforebegin', `
            <div class="gc" id="guideChecklist" hidden>
                <button type="button" class="gc-trigger" id="guideChecklistBtn" aria-haspopup="true" aria-expanded="false" aria-controls="guideChecklistPanel">
                    <span class="gc-ring" id="gcRing" aria-hidden="true"></span>
                    <span class="gc-trigger-text">Get started <strong id="gcCount"></strong></span>
                </button>
                <div class="gc-panel" id="guideChecklistPanel" role="dialog" aria-label="Get started" hidden></div>
            </div>`);
    }

    function stepHtml(step, isNext) {
        return `
            <li class="gc-step${step.done ? ' done' : ''}${isNext ? ' next' : ''}">
                <span class="gc-check" aria-hidden="true">${step.done ? '<i class="fas fa-check"></i>' : ''}</span>
                <div class="gc-step-body">
                    <div class="gc-step-title">${esc(step.title)}${step.done ? '<span class="visually-hidden"> (done)</span>' : ''}</div>
                    ${isNext ? `<div class="gc-step-why">${esc(step.why)}</div>
                    <div class="gc-step-actions">
                        <button type="button" class="gc-action" data-guide-action="${esc(step.action)}">${esc(step.action_label)}<i class="fas fa-arrow-right ms-1"></i></button>
                        <a class="gc-learn" href="${esc(step.help)}">Learn more</a>
                    </div>` : ''}
                </div>
            </li>`;
    }

    function render() {
        const d = state.data;
        const root = document.getElementById('guideChecklist');
        if (!d || !root) return;
        root.hidden = d.dismissed || (d.complete && !state.justCompleted);
        const pct = Math.round(d.done / d.total * 100);
        document.getElementById('gcCount').textContent = `${d.done}/${d.total}`;
        document.getElementById('gcRing').style.background = `conic-gradient(var(--brand-strong) ${pct}%, #e5e7eb 0)`;
        const next = d.steps.find(s => !s.done);
        document.getElementById('guideChecklistPanel').innerHTML = d.complete ? `
            <div class="gc-head"><strong>You're all set</strong></div>
            <div class="gc-done"><div class="gc-done-icon"><i class="fas fa-check"></i></div>
                <p>You've done every step. The <a href="/help">Help Center</a> has a guide for each feature whenever you need it.</p>
                <button type="button" class="gc-action" data-guide-dismiss>Close</button></div>` : `
            <div class="gc-head">
                <strong>Get started</strong>
                <span>${d.done} of ${d.total} done</span>
            </div>
            <div class="gc-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
            <ol class="gc-steps">${d.steps.map(s => stepHtml(s, s === next)).join('')}</ol>
            <div class="gc-foot">
                ${TOURS[PAGE] ? '<button type="button" class="gc-link" data-guide-tour><i class="fas fa-route me-1"></i>Tour this page</button>' : '<span></span>'}
                <button type="button" class="gc-link" data-guide-dismiss>Hide this</button>
            </div>`;
    }

    function setOpen(open) {
        state.open = open;
        const panel = document.getElementById('guideChecklistPanel'), btn = document.getElementById('guideChecklistBtn');
        if (!panel) return;
        panel.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
    }

    function load() {
        return $.get('/api/guide').done(r => {
            const before = state.data;
            if (before && !before.complete && r.complete) { state.justCompleted = true; setOpen(true); }
            state.data = r;
            render();
        });
    }

    // What a step's button does. In-page actions only exist in Studio Chat:
    // from another page, go there and do it on arrival (?do=...).
    function runAction(action) {
        setOpen(false);
        if (action && action.startsWith('/')) { window.location.href = action; return; }
        const name = (action || '').replace('action:', '');
        if (PAGE !== 'studio') { window.location.href = `/dashboard?do=${encodeURIComponent(name)}`; return; }
        const $box = $('#storyInput');
        if (name === 'composer') {
            $box.trigger('focus');
        } else if (name === 'carousel') {
            $box.val('/carousel').trigger('input').trigger('focus');  // opens the "/" menu on /carousel
        } else if (name === 'ideas') {
            if ($('#welcomeHero').hasClass('d-none')) $('#newChatBtn').trigger('click');  // ideas are on the start screen
            setTimeout(() => {
                const bar = document.getElementById('ideaFeedBar');
                if (bar) bar.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 250);
        }
    }

    // ── "?" tips next to things that need a word of explanation ──────────
    // target: where the "?" goes (appended inside it). Applied again whenever
    // the page adds content (chat replies, cards loaded later).
    const TIPS = {
        studio: [
            { target: '#imageQuotaBadge', text: 'Every new or edited image counts, including each slide of a carousel. The count resets at midnight UTC.', link: '/help/credits-and-limits' },
            { target: '.compliance-card .post-box-title', text: 'Your caption was checked against the advertising rules for your industry and markets. "Action needed" items are worth fixing before you post. Guidance only, not legal advice.', link: '/help/brand-profile' },
        ],
        brand: [
            { target: '#bpCompliance > h6', text: 'Your industry and markets decide which advertising rules captions are checked against, and which festivals show up in Ideas for you.', link: '/help/brand-profile' },
        ],
        approvals: [
            { target: '.ap-list-card > h6', text: 'Pending: waiting for a decision. Accepted: every platform approved. Partly approved: some platforms approved, changes asked on others. Rejected: changes asked on everything.', link: '/help/approvals' },
        ],
        review: [
            { target: '.preview-platform-tabs', text: 'Each tab is one platform\'s version. The dot shows its decision: grey to review, green approved, amber changes requested.', link: '/help/approvals' },
        ],
        settings: [
            { target: '.card-instagram h6', text: 'Needs an Instagram Business or Creator account linked to a Facebook Page. Personal accounts can\'t be published to by any app.', link: '/help/social-accounts' },
            { target: '.card-linkedin h6', text: 'Enter your Author URN (urn:li:person:... for a profile, urn:li:organization:... for a company page) and an access token that is allowed to post. Text, images, multi-image posts and document carousels can be published.', link: '/help/social-accounts' },
        ],
    };
    let tipSeq = 0;

    function applyTips() {
        (TIPS[PAGE] || []).forEach((tip, i) => {
            document.querySelectorAll(tip.target).forEach(el => {
                if (el.querySelector(':scope > .tip-btn')) return;
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'tip-btn';
                btn.dataset.tip = String(i);
                btn.setAttribute('aria-label', 'What is this?');
                btn.setAttribute('aria-expanded', 'false');
                btn.innerHTML = '<i class="fas fa-circle-question" aria-hidden="true"></i>';
                el.appendChild(btn);
            });
        });
    }

    function closeTip() {
        document.querySelectorAll('.tip-pop').forEach(p => p.remove());
        document.querySelectorAll('.tip-btn[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    }

    function openTip(btn) {
        const tip = (TIPS[PAGE] || [])[Number(btn.dataset.tip)];
        if (!tip) return;
        const wasOpen = btn.getAttribute('aria-expanded') === 'true';
        closeTip();
        if (wasOpen) return;
        const pop = document.createElement('div');
        pop.className = 'tip-pop';
        pop.id = `tipPop${++tipSeq}`;
        pop.setAttribute('role', 'tooltip');
        pop.innerHTML = `${esc(tip.text)}${tip.link ? ` <a href="${esc(tip.link)}">Learn more</a>` : ''}`;
        document.body.appendChild(pop);
        // The page is zoomed (style.css: html { zoom }), so a position we set and a
        // position we measure are in different scales: place the bubble at a known
        // spot, see where it lands, and convert.
        pop.style.left = '100px';
        pop.style.top = '100px';
        const probe = pop.getBoundingClientRect();
        const scale = probe.left / 100 || 1;
        const r = btn.getBoundingClientRect(), w = probe.width, h = probe.height;
        const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
        const below = r.bottom + 8 + h < window.innerHeight;
        pop.style.left = `${left / scale}px`;
        pop.style.top = `${(below ? r.bottom + 8 : Math.max(8, r.top - h - 8)) / scale}px`;
        btn.setAttribute('aria-expanded', 'true');
        btn.setAttribute('aria-describedby', pop.id);
    }

    // ── Wiring ───────────────────────────────────────────────────────────
    $(function () {
        ensureChecklistUi();

        // "?" tips
        applyTips();
        let tipTimer = null;
        new MutationObserver(() => { clearTimeout(tipTimer); tipTimer = setTimeout(applyTips, 300); })
            .observe(document.body, { childList: true, subtree: true });
        $(document).on('click', '.tip-btn', function (e) { e.preventDefault(); e.stopPropagation(); openTip(this); });
        $(document).on('click', function (e) { if (!e.target.closest('.tip-pop, .tip-btn')) closeTip(); });
        window.addEventListener('scroll', closeTip, true);

        // The menu's "Take the tour again" replays this page's tour
        if (TOURS[PAGE]) $('#umTakeTour').attr('href', `${path}?tour=${PAGE}`);

        if (!document.getElementById('guideChecklist')) return;
        $(document).on('click', '#guideChecklistBtn', () => setOpen(!state.open));
        $(document).on('click', '[data-guide-action]', function () { runAction($(this).data('guide-action')); });
        $(document).on('click', '[data-guide-tour]', () => startTour(PAGE));
        $(document).on('click', '[data-guide-dismiss]', function () {
            state.justCompleted = false;
            setOpen(false);
            post({ dismiss_checklist: true });
        });
        $(document).on('click', function (e) {
            if (state.open && !e.target.closest('#guideChecklist')) setOpen(false);
        });
        $(document).on('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (state.open) setOpen(false);
            closeTip();
        });

        // An idea picked in the Ideas panel (the server can't see that by itself)
        $(document).on('click', '.idea-card:not(.is-loading)', function () {
            const step = state.data && state.data.steps.find(s => s.key === 'idea');
            if (step && !step.done) post({ event: 'idea_used' });
        });

        // Refresh after anything that can complete a step
        let refreshTimer = null;
        $(document).ajaxComplete(function (_e, _xhr, settings) {
            const url = settings.url || '';
            if (/\/api\/(generate|presets\/run|approval-requests|social\/|refine|brand-profile)/.test(url)
                    && (settings.type || 'GET').toUpperCase() !== 'GET') {
                clearTimeout(refreshTimer);
                refreshTimer = setTimeout(load, 1200);
            }
        });

        const params = new URLSearchParams(window.location.search);
        const forced = PAGE && params.get('tour') === PAGE;
        const todo = PAGE === 'studio' ? params.get('do') : null;
        if (params.has('tour') || params.has('do')) {
            params.delete('tour');
            params.delete('do');
            history.replaceState(null, '', path + (params.toString() ? '?' + params : '') + window.location.hash);
        }
        load().done(function (r) {
            if (todo) { setTimeout(() => runAction('action:' + todo), 600); return; }  // a checklist button on another page
            // First visit to this page: show its tour once (not when arriving from an idea link in an email).
            // Pages that load their content first (the review page) get a moment to fill in.
            const firstVisit = PAGE && TOURS[PAGE] && !(r.tours_seen || []).includes(PAGE) && !params.get('idea');
            if (forced || firstVisit) setTimeout(() => startTour(PAGE), forced ? 500 : (PAGE === 'review' ? 2000 : 1300));
        });
    });
})();
