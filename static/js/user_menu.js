// Shared header user menu (templates/partials/user_menu.html) - one menu,
// identical on every signed-in page. Vanilla JS on purpose: some pages load
// neither jQuery nor Bootstrap's JS. Owns open/close, identity + credit
// fields, role-based links and sign-out. On the Studio Chat page app_v2.js
// also updates some of the same IDs with the same values, which is harmless.
(function () {
    const root = document.getElementById('userMenu');
    if (!root) return;

    const trigger = document.getElementById('userMenuDropdown');
    const panel = document.getElementById('userMenuPanel');
    const $ = (id) => document.getElementById(id);
    const setText = (id, value) => { const el = $(id); if (el) el.textContent = value; };
    const toggle = (id, show) => { const el = $(id); if (el) el.classList.toggle('d-none', !show); };

    const PLAN_LABELS = {
        individual: 'Individual',
        small: 'Small Business',
        medium: 'Medium Business',
        enterprise: 'Enterprise',
    };

    // ── Open / close ──────────────────────────────────────────────────
    function openMenu() {
        panel.hidden = false;
        root.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
    }

    function closeMenu() {
        panel.hidden = true;
        root.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
    }

    trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.hidden ? openMenu() : closeMenu();
    });
    document.addEventListener('click', function (e) {
        if (!root.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !panel.hidden) {
            closeMenu();
            trigger.focus();
        }
    });

    // ── Identity + role-based links ───────────────────────────────────
    fetch('/api/auth/me', { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : null))
        .then(function (res) {
            const user = res && res.user;
            if (!user) return;
            const initials = user.initials || (user.name || 'U').charAt(0).toUpperCase();
            const plan = user.is_admin ? 'Admin' : (PLAN_LABELS[user.account_type] || 'Member');

            setText('headerUserAvatar', initials);
            setText('umAvatarLg', initials);
            setText('headerUserLabel', user.name || 'User');
            setText('umName', user.name || 'User');
            setText('headerUserEmail', user.email || '');
            setText('headerUserPlan', plan);
            setText('umPlanBadge', plan);

            const selfServe = ['individual', 'small', 'medium'].includes(user.account_type);
            const enterpriseAccess = user.account_type === 'enterprise' || user.is_admin;
            toggle('dropBrandProfileLi', selfServe);
            toggle('dropBrandConfigLi', enterpriseAccess);
            toggle('dropAdminPortalLi', !!user.is_admin);
            // Analysis Dashboard stays listed for everyone (non-Enterprise
            // accounts land on /upgrade-required) but is tagged as such.
            toggle('umAnalysisTag', !enterpriseAccess);
        })
        .catch(function () { /* menu keeps its placeholders */ });

    // ── Usage & credits ───────────────────────────────────────────────
    fetch('/api/metrics/usage', { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : null))
        .then(function (r) {
            if (!r || !r.success) return;
            const remaining = Number(r.remaining_credits || 0);
            const limit = Number(r.credit_limit || 10);
            setText('dropHeaderCreditsRemaining', '$' + remaining.toFixed(2));
            setText('dropHeaderCreditLimit', '$' + limit.toFixed(2));
            setText('dropHeaderTokens', Number(r.total_tokens || 0).toLocaleString());
            setText('dropHeaderCost', '$' + Number(r.total_cost_usd || 0).toFixed(4));

            const pct = limit > 0 ? Math.max(0, Math.min(100, (remaining / limit) * 100)) : 0;
            const bar = $('umCreditBar');
            if (bar) {
                bar.style.width = pct + '%';
                bar.parentElement.classList.toggle('low', remaining > 0 && remaining < 2);
                bar.parentElement.classList.toggle('empty', remaining <= 0);
            }
        })
        .catch(function () { /* leave placeholders */ });

    // ── Header bell: timely nudges ─────────────────────────────────────
    // Each item opens Studio Chat with an idea loaded (its url) and is
    // marked read on click. Opening the panel does not mark anything read.
    (function () {
        const bell = $('notifBell');
        if (!bell) return;
        const btn = $('notifBellBtn');
        const bellPanel = $('notifBellPanel');
        const list = $('notifBellList');
        const count = $('notifBellCount');
        const readAll = $('notifBellReadAll');
        const ICONS = { occasion: 'fa-calendar-day', trend: 'fa-arrow-trend-up', inactive: 'fa-lightbulb' };

        function ago(iso) {
            const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
            if (mins < 60) return mins <= 1 ? 'Just now' : mins + ' min ago';
            if (mins < 1440) return Math.round(mins / 60) + ' h ago';
            const days = Math.round(mins / 1440);
            return days === 1 ? 'Yesterday' : days + ' days ago';
        }

        function setUnread(n) {
            count.hidden = !n;
            count.textContent = n > 9 ? '9+' : String(n);
            btn.setAttribute('aria-label', n ? 'Notifications, ' + n + ' unread' : 'Notifications');
            readAll.hidden = !n;
        }

        function render(items) {
            list.textContent = '';
            if (!items.length) {
                const empty = document.createElement('div');
                empty.className = 'nb-empty';
                empty.textContent = 'Nothing yet. Ideas for upcoming dates and trending topics will show up here.';
                list.appendChild(empty);
                return;
            }
            items.forEach(function (item) {
                const row = document.createElement('a');
                row.className = 'nb-item' + (item.read ? '' : ' unread');
                row.href = item.url || '/dashboard';
                const icon = document.createElement('span');
                icon.className = 'nb-icon';
                icon.innerHTML = '<i class="fas ' + (ICONS[item.kind] || 'fa-bell') + '" aria-hidden="true"></i>';
                const text = document.createElement('span');
                text.className = 'nb-text';
                const title = document.createElement('strong');
                title.textContent = item.title;
                const body = document.createElement('span');
                body.textContent = item.body || '';
                const when = document.createElement('small');
                when.textContent = ago(item.created_at);
                text.append(title, body, when);
                row.append(icon, text);
                row.addEventListener('click', function () {
                    if (item.read) return;
                    // keepalive: the page is about to navigate to the idea
                    fetch('/api/me/bell/read', {
                        method: 'POST', credentials: 'same-origin', keepalive: true,
                        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [item.id] }),
                    }).catch(function () { /* it stays unread */ });
                });
                list.appendChild(row);
            });
        }

        function load() {
            fetch('/api/me/bell', { credentials: 'same-origin' })
                .then((r) => (r.ok ? r.json() : null))
                .then(function (r) {
                    if (!r || !r.success) return;
                    setUnread(r.unread || 0);
                    render(r.items || []);
                })
                .catch(function () { /* the bell stays empty */ });
        }

        function closeBell() {
            bellPanel.hidden = true;
            bell.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        }

        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (!bellPanel.hidden) return closeBell();
            closeMenu();
            bellPanel.hidden = false;
            bell.classList.add('open');
            btn.setAttribute('aria-expanded', 'true');
            load();
        });
        trigger.addEventListener('click', closeBell);  // only one of the two panels open at a time
        document.addEventListener('click', function (e) {
            if (!bell.contains(e.target)) closeBell();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !bellPanel.hidden) {
                closeBell();
                btn.focus();
            }
        });
        readAll.addEventListener('click', function () {
            fetch('/api/me/bell/read', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' }, body: '{}',
            }).then(load).catch(function () { /* leave as is */ });
        });

        load();
    })();

    // ── Actions that need a modal only some pages have ────────────────
    // Studio Chat (index.html) has both modals and app_v2.js opens them;
    // settings.html has the AI Models one. Anywhere else, go to Studio Chat
    // and let app_v2.js open the modal from the URL hash.
    function modalOrRedirect(modalId, hash) {
        if (document.getElementById(modalId)) return; // page's own handler opens it
        window.location.href = '/dashboard#' + hash;
    }

    const extendBtn = $('dropRequestCreditBtn');
    if (extendBtn) {
        extendBtn.addEventListener('click', function () {
            closeMenu();
            modalOrRedirect('creditRequestModal', 'request-credit');
        });
    }

    const modelsBtn = $('dropHeaderModelInfoBtn');
    if (modelsBtn) {
        modelsBtn.addEventListener('click', function () {
            closeMenu();
            modalOrRedirect('modelArchitectureModal', 'ai-models');
        });
    }

    // ── Sign out ──────────────────────────────────────────────────────
    const logoutBtn = $('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function () {
            fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
                .catch(function () { /* redirect regardless */ })
                .finally(function () { window.location.href = '/login'; });
        });
    }
})();
