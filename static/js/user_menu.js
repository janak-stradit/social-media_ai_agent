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
