$(document).ready(function () {
    let currentSocialAccounts = [];

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Load social accounts and scheduled posts on page load
    loadUserSocialAccounts();
    loadScheduledPosts();

    // ── One-click connect (services/social_oauth_service.py) ───────────
    // A "Connect with ..." button is shown for each platform whose app is set
    // up; entering the details by hand stays available next to it. After the
    // platform sends the user back, the outcome is read once from the server.
    const OAUTH_NAMES = { facebook: 'Facebook', linkedin: 'LinkedIn', instagram: 'Instagram' };
    let oauthPending = null;  // { id, provider } while the user chooses between several accounts

    function showOAuthResult(ok, message) {
        if (!message) return;
        $('#oauthBanner').removeClass('d-none is-ok is-error').addClass(ok ? 'is-ok' : 'is-error')
            .html(`<i class="fas ${ok ? 'fa-circle-check' : 'fa-circle-exclamation'} me-2"></i><span></span>
                   <button type="button" class="oauth-banner-close" aria-label="Dismiss"><i class="fas fa-xmark"></i></button>`)
            .find('span').text(message);
    }
    $(document).on('click', '.oauth-banner-close', function () { $('#oauthBanner').addClass('d-none'); });

    function openOAuthChooser(result) {
        oauthPending = { id: result.choose, provider: result.provider };
        const name = OAUTH_NAMES[result.provider] || 'this platform';
        $('#oauthChooseHint').text(result.provider === 'facebook'
            ? 'You manage more than one Facebook Page. Its linked Instagram account is connected with it.'
            : `Post as yourself or as a company page you manage on ${name}.`);
        $('#oauthChooseList').html(result.options.map((o, i) => `
            <label class="oauth-option">
                <input type="radio" name="oauthChoice" value="${escapeHtml(o.id)}" ${i === 0 ? 'checked' : ''}>
                <span><strong>${escapeHtml(o.name)}</strong><small>${escapeHtml(o.detail || '')}</small></span>
            </label>`).join(''));
        $('#oauthChooseBtn').prop('disabled', false);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('oauthChooseModal')).show();
    }

    $('#oauthChooseBtn').on('click', function () {
        const choice = $('input[name="oauthChoice"]:checked').val();
        if (!oauthPending || !choice) return;
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: `/api/social/oauth/pending/${encodeURIComponent(oauthPending.id)}`, type: 'POST', contentType: 'application/json',
                 data: JSON.stringify({ choice: choice }) })
            .done(function (r) {
                bootstrap.Modal.getOrCreateInstance(document.getElementById('oauthChooseModal')).hide();
                oauthPending = null;
                showOAuthResult(true, r.message);
                loadUserSocialAccounts();
            })
            .fail(function (xhr) {
                bootstrap.Modal.getOrCreateInstance(document.getElementById('oauthChooseModal')).hide();
                showOAuthResult(false, (xhr.responseJSON || {}).error || 'Could not connect that account. Please click Connect again.');
            })
            .always(function () { $btn.prop('disabled', false); });
    });

    let oauthProviders = {};  // {facebook, linkedin, youtube: true when one-click connect is set up}
    let oauthRedirectUris = null;  // admins only: the return addresses to register
    function setupOAuth() {
        $.getJSON('/api/social/oauth/providers').done(function (r) {
            oauthProviders = r.providers || {};
            oauthRedirectUris = r.redirect_uris || null;
            renderSocialCards();
        });
        $.getJSON('/api/social/oauth/result').done(function (r) {
            const result = r.result;
            if (!result) return;
            if (result.choose) openOAuthChooser(result);
            else showOAuthResult(result.ok, result.message);
        });
    }
    setupOAuth();

    // ── Social account cards: one per network, one Connect button ────
    // Connected: "Connected as ..." with Disconnect. Not connected: the
    // one-click sign-in when it's set up (services/social_oauth_service.py),
    // otherwise the access-token form, which also lives under Advanced.
    const NETWORKS = [
        { key: 'facebook', name: 'Facebook', icon: 'fab fa-facebook-f', cls: 'bg-fb', what: 'Post to your Facebook Page.',
          oauth: 'facebook', start: '/connect/facebook' },
        // Instagram signs in on its own (Instagram Login) when that is set up, else through its Facebook Page
        { key: 'instagram', name: 'Instagram', icon: 'fab fa-instagram', cls: 'bg-ig', what: 'Post to your Instagram business or creator account.',
          oauth: 'facebook', start: '/connect/facebook', via: 'Facebook' },
        { key: 'linkedin', name: 'LinkedIn', icon: 'fab fa-linkedin-in', cls: 'bg-li', what: 'Post to your profile or a company page you manage.',
          oauth: 'linkedin', start: '/connect/linkedin' },
        { key: 'youtube', name: 'YouTube', icon: 'fab fa-youtube', cls: 'bg-yt', what: 'Upload videos to your channel.',
          oauth: 'youtube', start: '/connect/youtube' }
    ];

    function untilText(iso) {
        return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function renderSocialCards() {
        const byPlatform = {};
        currentSocialAccounts.filter(a => a.status === 'connected').forEach(a => { byPlatform[a.platform] = a; });
        $('#socialCards').html(NETWORKS.map(function (n) {
            const acc = byPlatform[n.key];
            if (n.key === 'instagram' && oauthProviders.instagram) {
                n = { ...n, oauth: 'instagram', start: '/connect/instagram', via: null, alsoFacebook: !!oauthProviders.facebook };
            }
            const oneClick = !!oauthProviders[n.oauth];
            let status, body, actions;
            if (acc) {
                const expired = !!acc.expired;
                const name = acc.connection_type === 'mcp' ? `${acc.account_name || n.name} (MCP)` : (acc.account_name || n.name);
                status = expired ? '<span class="sc-status is-expired"><i class="fas fa-clock"></i>Expired</span>'
                    : '<span class="sc-status is-on"><i class="fas fa-circle-check"></i>Connected</span>';
                body = `<div class="sc-account">Connected as <strong>${escapeHtml(name)}</strong></div>`
                    + (acc.token_expires_at ? `<div class="sc-note${expired ? ' is-bad' : ''}">${expired
                        ? `The sign-in ran out on ${untilText(acc.token_expires_at)}. Connect again to keep publishing.`
                        : `Signed in until ${untilText(acc.token_expires_at)}. You'll get a reminder a week before.`}</div>` : '');
                actions = (expired && oneClick ? `<a class="sc-btn sc-primary" href="${n.start}" target="_blank" rel="noopener" data-oauth-start>Connect again</a>` : '')
                    + `<button type="button" class="sc-btn sc-quiet" data-disconnect="${n.key}">Disconnect</button>`;
            } else {
                status = '<span class="sc-status">Not connected</span>';
                body = `<div class="sc-what">${n.what}</div>`;
                if (n.key === 'instagram' && byPlatform.facebook && n.oauth !== 'instagram') {
                    // The Page is connected: its linked Instagram account is one click away
                    actions = '<button type="button" class="sc-btn sc-primary" id="igFromFacebookBtn">Add the Instagram linked to your Page</button>';
                } else if (oneClick) {
                    actions = `<a class="sc-btn sc-primary" href="${n.start}" target="_blank" rel="noopener" data-oauth-start><i class="${n.icon} me-2"></i>Connect ${n.via ? 'with ' + n.via : n.name}</a>`;
                    if (n.via) body += `<div class="sc-note">Instagram connects through the Facebook Page it's linked to.</div>`;
                    if (n.key === 'instagram' && n.oauth === 'instagram') {
                        body += '<div class="sc-note">Needs an Instagram business or creator account. No Facebook Page needed.</div>';
                        if (byPlatform.facebook) actions += '<button type="button" class="sc-btn sc-quiet" id="igFromFacebookBtn">Use the one linked to my Page</button>';
                    }
                } else {
                    actions = `<button type="button" class="sc-btn sc-primary" data-not-ready="${n.key}">Connect ${n.name}</button>`;
                }
            }
            return `<div class="social-card">
                <div class="sc-head"><span class="platform-icon-circle ${n.cls}"><i class="${n.icon}"></i></span>
                    <strong class="sc-name">${n.name}</strong>${status}</div>
                ${body}
                <div class="sc-actions">${actions}</div>
            </div>`;
        }).join(''));
        NETWORKS.forEach(function (n) {
            const acc = byPlatform[n.key];
            $(`[data-adv-detail="${n.key}"]`).text(acc ? ` ${acc.account_id || acc.mcp_endpoint || ''}` : ' Not connected');
        });
    }

    // Connect on a network whose one-click sign-in isn't set up: say why, and
    // what switches it on (admins get the exact keys and return address)
    const SETUP = {
        facebook: { keys: ['META_APP_ID', 'META_APP_SECRET'], app: 'a Meta Business app (developers.facebook.com)', uri: 'facebook' },
        instagram: { keys: ['INSTAGRAM_APP_ID', 'INSTAGRAM_APP_SECRET'], app: 'the Instagram product in your Meta app (API setup with Instagram login)', uri: 'instagram',
                     alt: 'Or set up Facebook (META_APP_ID / META_APP_SECRET): Instagram accounts linked to a Facebook Page then connect with it.' },
        linkedin: { keys: ['LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET'], app: 'a LinkedIn app with "Share on LinkedIn" (linkedin.com/developers)', uri: 'linkedin' },
        youtube: { keys: ['YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET'], app: 'a Google OAuth client with the YouTube Data API (console.cloud.google.com)', path: '/api/auth/youtube/callback' }
    };
    let notReadyPlatform = null;
    $(document).on('click', '[data-not-ready]', function () {
        notReadyPlatform = $(this).data('not-ready');
        const n = NETWORKS.find(x => x.key === notReadyPlatform), s = SETUP[notReadyPlatform] || {};
        const uris = oauthRedirectUris;
        let html;
        if (uris) {  // admins: what to set
            const back = s.uri ? uris[s.uri] : `${window.location.origin}${s.path}`;
            html = `<p>Connecting ${escapeHtml(n.name)} with one click needs ${escapeHtml(s.app)}. Once it exists:</p>
                <ol class="small">
                    <li>Register this return address in it:<br><code>${escapeHtml(back || '')}</code></li>
                    <li>Add its keys to <code>.env</code>: ${s.keys.map(k => `<code>${k}</code>`).join(' and ')}</li>
                    <li>Restart the app. This button then opens ${escapeHtml(n.name)}'s own sign-in.</li>
                </ol>
                ${s.alt ? `<p class="small text-slate-500">${escapeHtml(s.alt)}</p>` : ''}
                <p class="small text-slate-500 mb-0">Step by step: docs/social-connect-setup.md</p>`;
        } else {
            html = `<p>One-click ${escapeHtml(n.name)} sign-in is being switched on for your workspace. You'll just click Connect and sign in on ${escapeHtml(n.name)}, with nothing to copy.</p>
                <p class="small text-slate-500 mb-0">If you're comfortable with developer tools, you can connect now with an access token.</p>`;
        }
        $('#notReadyTitle').text(`${n.name}: one-click connect isn't switched on yet`);
        $('#notReadyBody').html(html);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('notReadyModal')).show();
    });
    $('#notReadyManual').on('click', function () {
        bootstrap.Modal.getOrCreateInstance(document.getElementById('notReadyModal')).hide();
        if (notReadyPlatform) openConnectModal(notReadyPlatform);
    });

    $(document).on('click', '[data-disconnect]', function () {
        const platform = $(this).data('disconnect');
        const name = (NETWORKS.find(n => n.key === platform) || {}).name || platform;
        if (!window.confirm(`Disconnect ${name}? Scheduled posts to ${name} won't go out until you connect it again.`)) return;
        $.ajax({ url: `/api/social/accounts/${encodeURIComponent(platform)}`, type: 'DELETE' })
            .done(() => { showToast(`${name} disconnected.`, 'success'); loadUserSocialAccounts(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || `Could not disconnect ${name}.`, 'error'));
    });
    // Connect links open the sign-in in a new tab: refresh the cards once the user comes back here
    let awaitingOAuth = false;
    $(document).on('click', '[data-oauth-start]', function () { awaitingOAuth = true; });
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible' || !awaitingOAuth) return;
        awaitingOAuth = false;
        loadUserSocialAccounts();
    });
    $(document).on('click', '#igFromFacebookBtn', function () {
        const $btn = $(this).prop('disabled', true);
        $.post('/api/social/instagram/from-facebook')
            .done(r => { showToast(`Instagram connected${r.username ? ' (@' + r.username + ')' : ''}.`, 'success'); loadUserSocialAccounts(); })
            .fail(xhr => { showToast((xhr.responseJSON || {}).error || 'Could not find an Instagram account linked to your Page.', 'error'); $btn.prop('disabled', false); });
    });
    $('#openGuidesBtn').on('click', function () {
        const tab = document.getElementById('setup-guides-tab');
        if (tab && window.bootstrap) bootstrap.Tab.getOrCreateInstance(tab).show();
    });

    // ── Security tab: sign out every other browser ──────────────────────
    $('#endSessionsBtn').on('click', function () {
        if (!window.confirm('Sign out on every other device and browser? You stay signed in here.')) return;
        const $btn = $(this).prop('disabled', true);
        $.post('/api/auth/sessions/end-others')
            .done(() => showToast('Signed out everywhere else.', 'success'))
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not do that. Please try again.', 'error'))
            .always(() => $btn.prop('disabled', false));
    });

    // ── Notifications tab: weekly ideas email on/off + "send me one now" ──
    function ideasEmailMeta(s) {
        const last = s.last_sent_at
            ? `Last sent ${new Date(s.last_sent_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}.`
            : 'Not sent yet.';
        return `Goes to ${s.email}. ${last}`;
    }
    function loadNotificationSettings() {
        $.getJSON('/api/me/notifications').done(function (r) {
            const s = r && r.ideas_email;
            if (!s) return;
            $('#ideasEmailSwitch').prop('checked', !!s.enabled).prop('disabled', false);
            $('#nudgesEmailSwitch').prop('checked', !!s.nudges_enabled).prop('disabled', false);
            $('#recapEmailSwitch').prop('checked', !!s.recap_enabled).prop('disabled', false);
            $('#ideasEmailMeta').text(ideasEmailMeta(s));
        });
    }
    const EMAIL_SWITCHES = {
        ideasEmailSwitch: ['ideas_email_enabled', 'Weekly ideas email'],
        nudgesEmailSwitch: ['nudges_email_enabled', 'Reminder emails'],
        recapEmailSwitch: ['recap_email_enabled', 'Monthly recap']
    };
    $('#ideasEmailSwitch, #nudgesEmailSwitch, #recapEmailSwitch').on('change', function () {
        const $switch = $(this).prop('disabled', true);
        const enabled = $switch.prop('checked');
        const [field, what] = EMAIL_SWITCHES[this.id];
        $.ajax({
            url: '/api/me/notifications',
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({ [field]: enabled }),
            success: function () { showToast(`${what} switched ${enabled ? 'on' : 'off'}.`, 'success'); },
            error: function () {
                $switch.prop('checked', !enabled);
                showToast('Could not save your email setting.', 'error');
            },
            complete: function () { $switch.prop('disabled', false); }
        });
    });
    $('#sendIdeasEmailNowBtn').on('click', function () {
        const $btn = $(this).prop('disabled', true);
        const label = $btn.html();
        $btn.html('<span class="spinner-border spinner-border-sm me-1" role="status"></span>Writing your ideas...');
        $.ajax({
            url: '/api/me/notifications/ideas-email/send-now',
            type: 'POST',
            success: function (r) {
                showToast(`Sent ${r.ideas} ideas to ${r.email}.`, 'success');
                loadNotificationSettings();
            },
            error: function (xhr) { showToast((xhr.responseJSON || {}).error || 'The email could not be sent.', 'error'); },
            complete: function () { $btn.prop('disabled', false).html(label); }
        });
    });
    $('#sendRecapEmailNowBtn').on('click', function () {
        const $btn = $(this).prop('disabled', true);
        const label = $btn.html();
        $btn.html('<span class="spinner-border spinner-border-sm me-1" role="status"></span>Putting your recap together...');
        $.ajax({
            url: '/api/me/notifications/recap-email/send-now',
            type: 'POST',
            success: function (r) { showToast(`Sent your recap (${r.posts} posts) to ${r.email}.`, 'success'); },
            error: function (xhr) { showToast((xhr.responseJSON || {}).error || 'The email could not be sent.', 'error'); },
            complete: function () { $btn.prop('disabled', false).html(label); }
        });
    });
    loadNotificationSettings();
    // ── Security tab: two-step sign-in ───────────────────────────────────
    function loadTwoFactor() {
        $.getJSON('/api/auth/2fa').done(function (r) {
            $('#tfaStatus').text(r.enabled
                ? 'On. Signing in asks for a code from your authenticator app as well as your password.'
                : 'Off. Add a code from an authenticator app to signing in, so a stolen password alone is not enough.');
            $('#tfaRequired').toggleClass('d-none', !(r.required && !r.enabled));
            $('#tfaOff').toggleClass('d-none', r.enabled);
            $('#tfaOn').toggleClass('d-none', !r.enabled);
            $('#tfaSetup').addClass('d-none');
        });
    }
    $('#tfaStartBtn').on('click', function () {
        $.post('/api/auth/2fa/setup').done(function (r) {
            $('#tfaOff').addClass('d-none');
            $('#tfaSetup').removeClass('d-none');
            $('#tfaKey').text(r.secret.replace(/(.{4})/g, '$1 ').trim());
            $('#tfaQr').empty();
            if (window.QRCode) new QRCode(document.getElementById('tfaQr'), { text: r.uri, width: 168, height: 168 });
            $('#tfaCode').val('').focus();
        }).fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not start the set-up.', 'error'));
    });
    $('#tfaEnableBtn').on('click', function () {
        $.ajax({ url: '/api/auth/2fa/enable', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ code: $('#tfaCode').val() }) })
            .done(() => { showToast('Two-step sign-in is on.', 'success'); loadTwoFactor(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'That code did not work.', 'error'));
    });
    $('#tfaDisableBtn').on('click', function () {
        $.ajax({ url: '/api/auth/2fa/disable', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ code: $('#tfaOffCode').val() }) })
            .done(() => { showToast('Two-step sign-in is off.', 'success'); $('#tfaOffCode').val(''); loadTwoFactor(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'That code did not work.', 'error'));
    });
    loadTwoFactor();

    // ── Plan & usage tab ─────────────────────────────────────────────────
    function money(plan, currency, annual) {
        const value = currency === 'INR' ? (annual ? plan.annual_inr : plan.price_inr) : (annual ? plan.annual_usd : plan.price_usd);
        if (value === null || value === undefined) return annual ? '–' : 'On request';
        if (Number(value) === 0) return 'Free';
        return currency === 'INR'
            ? '₹' + Number(value).toLocaleString('en-IN') + (annual ? '' : ' + GST')
            : '$' + Number(value).toLocaleString('en-US');
    }
    function meter(label, part, extra) {
        const unlimited = part.limit === null;
        const total = unlimited ? 0 : part.limit + (part.pack_remaining || 0) + Math.max(0, part.used - part.limit);
        const pct = unlimited || !total ? 0 : Math.min(100, Math.round((part.used / total) * 100));
        const text = unlimited ? `${part.used} used · unlimited` : `${part.used} of ${part.limit}${extra || ''}`;
        return `<div class="plan-meter${!unlimited && part.remaining === 0 ? ' is-full' : ''}">
            <div class="plan-meter-head"><span>${label}</span><strong>${escapeHtml(text)}</strong></div>
            <div class="plan-meter-bar"><span style="width:${pct}%"></span></div></div>`;
    }
    function loadPlan() {
        $.when($.getJSON('/api/me/plan'), $.getJSON('/api/plans')).done(function (mine, all) {
            const usage = mine[0].usage;
            const currency = mine[0].currency || 'USD';
            const plans = all[0].plans || [];
            if (!usage) return;
            const end = new Date(usage.period_end).toLocaleDateString(undefined, { day: 'numeric', month: 'long' });
            $('#planSummary').html(`You're on the <strong>${escapeHtml(usage.plan.name)}</strong> plan. `
                + (usage.is_admin ? 'Admins have no limits.' : `This month's allowance resets on ${escapeHtml(end)}.`));
            const packs = (part, noun) => part.pack_remaining ? ` + ${part.pack_remaining} in packs` : '';
            $('#planMeters').html(usage.is_admin ? '' : [
                meter('Posts this month', usage.posts),
                meter('Images this month', usage.images, packs(usage.images)),
                usage.videos.limit === 0 && !usage.videos.pack_remaining
                    ? '<div class="plan-meter"><div class="plan-meter-head"><span>Videos</span><strong>Packs only</strong></div></div>'
                    : meter('Videos', usage.videos, packs(usage.videos))
            ].join(''));
            const rows = [
                ['Price a month', p => money(p, currency, false)],
                ['Price a year', p => money(p, currency, true)],
                ['Best for', p => escapeHtml(p.best_for || '')],
                ['Posts a month', p => p.posts_month === null ? 'Custom' : p.posts_month],
                ['Images a month', p => p.images_month === null ? 'Custom' : p.images_month],
                ['Images a day', p => p.images_day === null ? 'Custom' : p.images_day],
                ['Video', p => p.videos_month === null ? 'Included' : (p.videos_day ? 'Packs' + (p.video_pack_discount ? `, ${p.video_pack_discount}% off` : '') : '–')],
                ['Approvals', p => p.approvals ? '<i class="fas fa-check text-success" aria-label="Yes"></i>' : '–'],
                ['Support', p => escapeHtml(p.support || '')]
            ];
            const head = '<thead><tr><th></th>' + plans.map(p => `<th class="${p.slug === usage.plan.slug ? 'is-current' : ''}">${escapeHtml(p.name)}${p.slug === usage.plan.slug ? '<small>Your plan</small>' : ''}</th>`).join('') + '</tr></thead>';
            const body = '<tbody>' + rows.map(([label, cell]) => `<tr><th scope="row">${label}</th>` + plans.map(p => `<td class="${p.slug === usage.plan.slug ? 'is-current' : ''}">${cell(p)}</td>`).join('') + '</tr>').join('') + '</tbody>';
            $('#planCompare').html(head + body);
            loadBilling(plans, usage, currency);
            loadBillingHistory();
        }).fail(function () { $('#planSummary').text('Could not load your plan.'); });
    }

    // ── Payments & invoices, and billing details ─────────────────────────
    function loadBillingHistory() {
        $.getJSON('/api/billing/history').done(function (r) {
            const payments = r.payments || [];
            $('#billingHistory').toggleClass('d-none', !payments.length);
            const STATUS = { paid: 'Paid', refunded: 'Refunded', partially_refunded: 'Partly refunded' };
            $('#billingHistoryTable').html('<thead><tr><th>Date</th><th>What</th><th>Amount</th><th>Status</th><th>Documents</th></tr></thead><tbody>'
                + payments.map(p => `<tr>
                    <td>${escapeHtml(new Date(p.paid_at).toLocaleDateString())}</td>
                    <td>${escapeHtml(p.description || '')}</td>
                    <td>${minorToText(p.amount, p.currency)}${p.refunded_amount ? `<div class="small text-muted">${minorToText(p.refunded_amount, p.currency)} refunded</div>` : ''}</td>
                    <td>${STATUS[p.status] || escapeHtml(p.status)}</td>
                    <td>${(p.invoices || []).map(i => `<a href="/invoices/${encodeURI(i.number)}" target="_blank" rel="noopener">${i.kind === 'credit_note' ? 'Credit note' : 'Invoice'} ${escapeHtml(i.number)}</a>`).join('<br>')}</td>
                </tr>`).join('') + '</tbody>');
        });
    }
    $.getJSON('/api/me/billing-profile').done(function (r) {
        const p = r.profile || {};
        $('#billState').append((r.states || []).map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join(''));
        $('#billName').val(p.name || '');
        $('#billGstin').val(p.gstin || '');
        $('#billState').val(p.state || '');
        $('#billAddress').val(p.address || '');
    });
    $('#billSaveBtn').on('click', function () {
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/me/billing-profile', type: 'PUT', contentType: 'application/json',
            data: JSON.stringify({ name: $('#billName').val(), gstin: $('#billGstin').val(), state: $('#billState').val(), address: $('#billAddress').val() }) })
            .done(r => { $('#billState').val(r.profile.state || ''); showToast('Billing details saved.', 'success'); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not save the billing details.', 'error'))
            .always(() => $btn.prop('disabled', false));
    });

    // ── Buying: Razorpay (INR) or Stripe (USD). Plan rows get Buy buttons, packs a Top up list ──
    let billingProvider = 'razorpay';
    function minorToText(amount, currency) {
        return currency === 'USD'
            ? '$' + (amount / 100).toLocaleString('en-US', { minimumFractionDigits: amount % 100 ? 2 : 0, maximumFractionDigits: 2 })
            : '₹' + (amount / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 });
    }
    function loadBilling(plans, usage, currency) {
        $.getJSON('/api/billing/options').done(function (r) {
            const on = !!r.enabled;
            billingProvider = r.provider;
            $('#planNote').text(!on
                ? 'Online payment is coming soon. To change your plan or buy a top-up pack, contact your admin.'
                : (r.provider === 'stripe'
                    ? 'Payments are handled by Stripe. You will go to Stripe\'s secure page to pay, then come back here.'
                    : 'Prices include 18% GST at checkout. Payments are handled by Razorpay (cards, UPI, net banking).'));
            if (!on) return;
            const byItem = {};
            (r.items || []).forEach(i => { byItem[i.item] = i; });
            const sub = r.subscription;
            const buy = item => {
                if (!byItem[item]) return '';
                const [, slug, period] = item.split(':');
                const label = period === 'year' ? 'Yearly' : 'Monthly';
                // Auto-renew is the main button; a one-time purchase stays available as a link
                return sub ? `<button type="button" class="btn btn-sm btn-outline-primary buy-btn" data-item="${escapeHtml(item)}">${label}, once</button>`
                    : `<button type="button" class="btn btn-sm btn-primary subscribe-btn" data-plan="${escapeHtml(slug)}" data-period="${period}">${label}</button>
                       <button type="button" class="btn btn-link btn-sm p-0 buy-btn" data-item="${escapeHtml(item)}">or pay once</button>`;
            };
            const cells = plans.map(p => {
                const current = p.slug === usage.plan.slug;
                const buttons = buy(`plan:${p.slug}:month`) + buy(`plan:${p.slug}:year`);
                return `<td class="${current ? 'is-current' : ''}">${buttons
                    ? `<div class="d-flex flex-column gap-1 align-items-center">${buttons}${current && usage.paid_until ? '<small class="text-muted">Renew</small>' : ''}</div>`
                    : (p.price_inr === null ? '<a href="mailto:sales@stradit.com">Contact us</a>' : '–')}</td>`;
            }).join('');
            $('#planCompare tbody').append(`<tr><th scope="row">Buy</th>${cells}</tr>`);
            const packs = (r.items || []).filter(i => i.item.startsWith('pack:'));
            $('#packShop').toggleClass('d-none', !packs.length);
            $('#packShopItems').html(packs.map(i => `<div class="pack-item">
                <div><strong>${escapeHtml(i.description)}</strong><div class="small text-slate-500">${minorToText(i.base, i.currency)}${i.tax ? ' + GST' : ''}</div></div>
                <button type="button" class="btn btn-sm btn-primary buy-btn" data-item="${escapeHtml(i.item)}">Buy</button></div>`).join(''));
            const until = usage.paid_until ? escapeHtml(new Date(usage.paid_until).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })) : null;
            if (sub && sub.status === 'active') {
                $('#planSummary').append(` Renews automatically every ${escapeHtml(sub.period)}${until ? ` (next on ${until})` : ''}.
                    <button type="button" class="btn btn-link btn-sm p-0 align-baseline" id="cancelRenewalBtn">Cancel automatic renewal</button>`);
            } else if (sub && sub.status === 'cancelled') {
                $('#planSummary').append(` Automatic renewal is off${until ? `; your plan runs until ${until}` : ''}.`);
            } else if (until) {
                $('#planSummary').append(` Paid until ${until}.`);
            }
        });
    }
    // Automatic renewal: Razorpay subscription window (INR) or Stripe Checkout in subscription mode (USD)
    $(document).on('click', '.subscribe-btn', function () {
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/billing/subscribe', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ plan: $btn.data('plan'), period: $btn.data('period') }) })
            .done(function (r) {
                if (r.provider === 'stripe') { window.location.href = r.url; return; }
                if (!window.Razorpay) {
                    showToast('The payment window could not load. Check your connection and try again.', 'error');
                    return $btn.prop('disabled', false);
                }
                const checkout = new window.Razorpay({
                    key: r.key_id, subscription_id: r.subscription_id, name: 'AVIR AI', description: r.description,
                    prefill: r.prefill, theme: { color: '#c2410c' },
                    handler: function (result) {
                        $.ajax({ url: '/api/billing/subscribe/verify', type: 'POST', contentType: 'application/json', data: JSON.stringify(result) })
                            .done(() => { showToast('Automatic renewal is on. Your plan is active.', 'success'); loadPlan(); })
                            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'We could not confirm the payment. If money was taken, it will be applied within a few minutes.', 'error'));
                    },
                    modal: { ondismiss: () => $btn.prop('disabled', false) }
                });
                checkout.open();
            })
            .fail(xhr => { showToast((xhr.responseJSON || {}).error || 'Automatic renewal could not be started.', 'error'); $btn.prop('disabled', false); });
    });
    $(document).on('click', '#cancelRenewalBtn', function () {
        if (!window.confirm('Stop automatic renewal? You keep your plan until the end of the time you have paid for.')) return;
        $.post('/api/billing/subscription/cancel')
            .done(() => { showToast('Automatic renewal is off. Your plan runs until the end of the paid time.', 'success'); loadPlan(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not cancel automatic renewal.', 'error'));
    });

    $(document).on('click', '.buy-btn', function () {
        const $btn = $(this).prop('disabled', true);
        const item = $btn.data('item');
        if (billingProvider === 'stripe') {
            // Stripe Checkout is a page of its own: go there, and come back to /settings?checkout=success
            $.ajax({ url: '/api/billing/stripe/checkout', type: 'POST', contentType: 'application/json', data: JSON.stringify({ item: item }) })
                .done(r => { window.location.href = r.url; })
                .fail(xhr => { showToast((xhr.responseJSON || {}).error || 'Payment could not be started.', 'error'); $btn.prop('disabled', false); });
            return;
        }
        $.ajax({ url: '/api/billing/checkout', type: 'POST', contentType: 'application/json', data: JSON.stringify({ item: item }) })
            .done(function (order) {
                if (!window.Razorpay) {
                    showToast('The payment window could not load. Check your connection and try again.', 'error');
                    return $btn.prop('disabled', false);
                }
                const checkout = new window.Razorpay({
                    key: order.key_id, order_id: order.order_id, amount: order.amount, currency: order.currency,
                    name: 'AVIR AI', description: order.description, prefill: order.prefill, theme: { color: '#c2410c' },
                    handler: function (result) {
                        $.ajax({ url: '/api/billing/verify', type: 'POST', contentType: 'application/json', data: JSON.stringify(result) })
                            .done(() => { showToast(`Payment received: ${order.description}.`, 'success'); loadPlan(); })
                            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'We could not confirm the payment. If money was taken, it will be applied within a few minutes.', 'error'));
                    },
                    modal: { ondismiss: () => $btn.prop('disabled', false) }
                });
                checkout.on('payment.failed', () => showToast('The payment did not go through. You have not been charged.', 'error'));
                checkout.open();
            })
            .fail(function (xhr) {
                showToast((xhr.responseJSON || {}).error || 'Payment could not be started.', 'error');
                $btn.prop('disabled', false);
            });
    });
    // Back from Stripe: confirm the payment with the server (which asks Stripe), then tidy the address
    (function confirmStripeReturn() {
        const params = new URLSearchParams(window.location.search);
        const status = params.get('checkout');
        if (!status) return;
        history.replaceState(null, '', window.location.pathname + '#plan');
        if (status === 'cancelled') return showToast('Payment cancelled. You have not been charged.', 'info');
        $.ajax({ url: '/api/billing/stripe/confirm', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ session_id: params.get('session_id') }) })
            .done(r => { showToast(r.applied ? `Payment received: ${r.applied.description}.` : 'Payment received.', 'success'); loadPlan(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'We could not confirm the payment yet. It will be applied within a few minutes.', 'warning'));
    })();
    loadPlan();
    $('#plan-tab').on('shown.bs.tab', loadPlan);

    // The email's "Email settings" link lands on /settings#notifications
    const HASH_TABS = { '#notifications': 'notifications-tab', '#plan': 'plan-tab', '#security': 'security-tab' };
    if (HASH_TABS[window.location.hash]) {
        const tab = document.getElementById(HASH_TABS[window.location.hash]);
        if (tab && window.bootstrap) bootstrap.Tab.getOrCreateInstance(tab).show();
    }

    // Reload scheduled posts when switching to Tab 3
    $('#scheduled-posts-tab').on('shown.bs.tab', function () {
        loadScheduledPosts();
    });

    function loadUserSocialAccounts() {
        $.ajax({
            url: '/api/social/accounts',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                currentSocialAccounts = r.accounts || [];
                renderSocialCards();
            }
        });
    }

    $('input[name="connectionType"]').on('change', function () {
        const val = $(this).val();
        if (val === 'mcp') {
            $('#mcpFieldsContainer').removeClass('d-none');
            $('#directFieldsContainer').addClass('d-none');
        } else {
            $('#directFieldsContainer').removeClass('d-none');
            $('#mcpFieldsContainer').addClass('d-none');
        }
    });

    window.openConnectModal = function (platform) {
        const titles = {
            facebook: 'Connect Facebook Page',
            instagram: 'Connect Instagram Business',
            linkedin: 'Connect LinkedIn Account',
            youtube: 'Connect YouTube Account'
        };
        const icons = {
            facebook: '<i class="fab fa-facebook color-fb"></i>',
            instagram: '<i class="fab fa-instagram color-ig"></i>',
            linkedin: '<i class="fab fa-linkedin color-li"></i>',
            youtube: '<i class="fab fa-youtube color-yt"></i>'
        };

        $('#connectPlatformInput').val(platform);
        $('#connectModalTitle').text(titles[platform] || 'Connect Account');
        // Instagram: the account is found from the Facebook Page; the handle fills itself in
        $('#accountIdLabel').text(platform === 'instagram' ? 'Facebook Page ID (or Instagram account ID):' : 'Page ID / Account ID / Author URN:');
        $('#accountNameLabel').text(platform === 'instagram' ? 'Instagram handle (filled in automatically):' : 'Account Name / Handle:');
        $('#connectModalIcon').html(icons[platform] || '<i class="fas fa-plug"></i>');

        const existing = currentSocialAccounts.find(a => a.platform === platform) || {};
        $('#connectAccountNameInput').val(existing.account_name || '');
        $('#connectAccountIdInput').val(existing.account_id || '');
        $('#connectAccessTokenInput').val(existing.access_token || '');
        $('#mcpEndpointInput').val(existing.mcp_endpoint || '');
        $('#mcpTokenInput').val('');
        $('#mcpToolNameInput').val(existing.mcp_tool_name || 'linkedin_publish_post');

        if (platform === 'linkedin') {
            $('#connectionTypeSelector').removeClass('d-none');
            if (existing.connection_type === 'mcp') {
                $('#connTypeMcp').prop('checked', true).trigger('change');
            } else {
                $('#connTypeDirect').prop('checked', true).trigger('change');
            }
        } else {
            $('#connectionTypeSelector').addClass('d-none');
            $('#connTypeDirect').prop('checked', true).trigger('change');
        }
        
        if (platform === 'youtube') {
            $('#refreshTokenContainer').show();
            $('#connectRefreshTokenInput').val(existing.refresh_token || '');
        } else {
            $('#refreshTokenContainer').hide();
            $('#connectRefreshTokenInput').val('');
        }

        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('connectAccountModal'));
        modal.show();
    };

    // One click: the Instagram account linked to the connected Facebook Page
    $('#igFromFacebookBtn').on('click', function () {
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/social/instagram/from-facebook', type: 'POST', contentType: 'application/json', data: '{}' })
            .done(r => { showToast(`Instagram connected: @${r.username}`, 'success'); loadUserSocialAccounts(); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not connect Instagram.', 'error'))
            .always(() => $btn.prop('disabled', false));
    });

    $('#testMcpConnBtn').on('click', function () {
        const endpoint = $('#mcpEndpointInput').val().trim();
        const token = $('#mcpTokenInput').val().trim();
        const toolName = $('#mcpToolNameInput').val().trim();

        if (!endpoint) {
            showToast('Please enter an MCP Server Endpoint URL', 'error');
            return;
        }

        const btn = $(this);
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Testing MCP Server...');

        $.ajax({
            url: '/api/social/mcp/test',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                mcp_endpoint: endpoint,
                mcp_token: token,
                mcp_tool_name: toolName
            }),
            success: function (r) {
                btn.prop('disabled', false).html('<i class="fas fa-vial me-1"></i>Test MCP Server Connection');
                if (r.success) {
                    showToast(r.message || 'MCP Server connected cleanly!', 'success');
                } else {
                    showToast(r.error || 'MCP Server connection failed', 'error');
                }
            },
            error: function (xhr) {
                btn.prop('disabled', false).html('<i class="fas fa-vial me-1"></i>Test MCP Server Connection');
                showToast('MCP Server Test Error: ' + (xhr.responseJSON?.error || 'Connection Failed'), 'error');
            }
        });
    });

    $('#saveConnectAccountBtn').on('click', function () {
        const platform = $('#connectPlatformInput').val();
        const accountName = $('#connectAccountNameInput').val().trim();
        const accountId = $('#connectAccountIdInput').val().trim();
        const accessToken = $('#connectAccessTokenInput').val().trim();
        const connectionType = $('input[name="connectionType"]:checked').val() || 'direct';
        const mcpEndpoint = $('#mcpEndpointInput').val().trim();
        const mcpToken = $('#mcpTokenInput').val().trim();
        const mcpToolName = $('#mcpToolNameInput').val().trim();
        const refreshToken = $('#connectRefreshTokenInput').val().trim();

        if (!accountName && platform !== 'instagram') {  // Instagram: filled in from the account
            showToast('Please enter an Account Name or Handle', 'error');
            return;
        }

        if (platform === 'linkedin' && connectionType === 'mcp' && !mcpEndpoint) {
            showToast('Please enter an MCP Server Endpoint URL', 'error');
            return;
        }

        $.ajax({
            url: '/api/social/accounts',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platform: platform,
                account_name: accountName,
                account_id: accountId,
                access_token: accessToken,
                refresh_token: refreshToken,
                connection_type: connectionType,
                mcp_endpoint: mcpEndpoint,
                mcp_token: mcpToken,
                mcp_tool_name: mcpToolName
            }),
            success: function (r) {
                if (r.success) {
                    showToast(`${platform.toUpperCase()} account connected via ${connectionType.toUpperCase()} mode!`, 'success');
                    bootstrap.Modal.getInstance(document.getElementById('connectAccountModal')).hide();
                    loadUserSocialAccounts();
                }
            },
            error: function (xhr) {
                showToast('Connection failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    });

    function loadScheduledPosts() {
        $.ajax({
            url: '/api/social/scheduled',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const posts = r.scheduled_posts || [];
                if (!posts.length) {
                    $('#scheduledPostsTbody').html(`
                        <tr>
                            <td colspan="6" class="text-center py-4 text-slate-400">No scheduled posts yet. Click "Create Manual Post" above to schedule one - it will be listed here with its status. <a href="/help/social-accounts">How publishing works</a></td>
                        </tr>
                    `);
                    return;
                }

                let html = '';
                posts.forEach(p => {
                    const platforms = Array.isArray(p.platforms) ? p.platforms.map(pl => {
                        const icons = { facebook: '<i class="fab fa-facebook color-fb"></i>', instagram: '<i class="fab fa-instagram color-ig"></i>', linkedin: '<i class="fab fa-linkedin color-li"></i>', youtube: '<i class="fab fa-youtube color-yt"></i>' };
                        return `<span class="ap-tag me-1">${icons[pl] || ''}${escapeHtml(pl.charAt(0).toUpperCase() + pl.slice(1))}</span>`;
                    }).join('') : p.platforms;

                    let cJson = {};
                    if (typeof p.content_json === 'string') {
                        try { cJson = JSON.parse(p.content_json); } catch (e) {}
                    } else if (typeof p.content_json === 'object') {
                        cJson = p.content_json || {};
                    }

                    const mediaUrl = p.thumb || cJson.image_url || cJson.media_url || p.media_url;
                    const mediaTag = mediaUrl ? `<img src="${escapeHtml(mediaUrl)}" class="sp-thumb" alt="">` : '<div class="sp-thumb sp-thumb-empty"><i class="fas fa-align-left"></i></div>';

                    const storyText = p.title || cJson.caption || cJson.story || p.story || 'Custom Manual Post';
                    // Times are stored in UTC and shown in the viewer's own time zone
                    const when = p.scheduled_at_iso
                        ? new Date(p.scheduled_at_iso).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
                        : p.scheduled_at;
                    // What happened on each platform (posts scheduled from Studio Chat)
                    const names = { facebook: 'Facebook', instagram: 'Instagram', linkedin: 'LinkedIn', youtube: 'YouTube' };
                    const resultLines = Object.entries(p.results || {}).map(([pl, res]) => res.ok
                        ? (res.permalink ? `<div class="sp-result ok"><i class="fas fa-check me-1"></i>${names[pl] || escapeHtml(pl)}: <a href="${escapeHtml(res.permalink)}" target="_blank" rel="noopener">view post</a></div>` : '')
                        : `<div class="sp-result bad"><i class="fas fa-triangle-exclamation me-1"></i>${names[pl] || escapeHtml(pl)}: ${escapeHtml(res.error || 'not published')}</div>`).join('');
                    const manualBadge = cJson.is_manual ? '<span class="ap-tag ap-tag-primary ms-1">Manual</span>' : '';

                    const statusBadges = {
                        pending: '<span class="ap-status ap-status-pending"><i class="fas fa-clock"></i>Pending</span>',
                        published: '<span class="ap-status ap-status-approved"><i class="fas fa-check-circle"></i>Published</span>',
                        failed: '<span class="ap-status ap-status-rejected"><i class="fas fa-triangle-exclamation"></i>Failed</span>',
                        partial: '<span class="ap-status ap-status-pending"><i class="fas fa-circle-exclamation"></i>Partly published</span>',
                        publishing: '<span class="ap-status ap-status-pending"><i class="fas fa-spinner"></i>Publishing</span>',
                        cancelled: '<span class="ap-status ap-status-neutral"><i class="fas fa-ban"></i>Cancelled</span>'
                    };

                    const canRetry = ['failed', 'partial', 'publishing'].includes(p.status) && (p.items || []).length;
                    const cancelBtn = p.status === 'pending'
                        ? `<button type="button" class="sp-cancel-btn" onclick="cancelScheduledPostItem(${p.id})"><i class="fas fa-ban"></i>Cancel</button>`
                        : canRetry ? `<button type="button" class="sp-cancel-btn" onclick="retryScheduledPostItem(${p.id})" title="Send again the platforms that did not go out"><i class="fas fa-rotate-right"></i>Try again</button>`
                        : '—';

                    html += `
                        <tr>
                            <td class="font-monospace">#${p.id}</td>
                            <td style="max-width: 300px;">
                                <div class="d-flex align-items-center">
                                    ${mediaTag}
                                    <div class="text-truncate">
                                        <span class="fw-semibold text-slate-800" title="${escapeHtml(storyText)}">${escapeHtml(storyText)}</span>
                                        ${manualBadge}
                                        ${resultLines}
                                    </div>
                                </div>
                            </td>
                            <td>${platforms}</td>
                            <td class="text-navy font-semibold">${escapeHtml(when)}</td>
                            <td>${statusBadges[p.status] || p.status}</td>
                            <td class="text-end">${cancelBtn}</td>
                        </tr>
                    `;
                });
                $('#scheduledPostsTbody').html(html);
            },
            error: function (xhr) {
                console.error("Error loading scheduled posts:", xhr);
                $('#scheduledPostsTbody').html(`
                    <tr>
                        <td colspan="6" class="text-center py-4 text-danger">
                            <i class="fas fa-exclamation-triangle me-1"></i>Failed to load scheduled posts. (HTTP ${xhr.status})
                        </td>
                    </tr>
                `);
            }
        });
    }

    window.loadScheduledPosts = loadScheduledPosts;

    window.openManualPostModal = function () {
        // Switch to Tab 3: Upcoming Scheduled Posts
        try {
            const scheduledTabBtn = document.getElementById('scheduled-posts-tab');
            if (scheduledTabBtn) {
                bootstrap.Tab.getOrCreateInstance(scheduledTabBtn).show();
            }
        } catch (e) {}

        $('#manualPostCaption').val('');
        $('#manualPostPhotoInput').val('');
        $('#manualPostPhotoPreview').attr('src', '').addClass('d-none');
        $('#manualPostVideoPreview').attr('src', '').addClass('d-none');
        $('#manualPostPreviewContainer').addClass('d-none');

        const now = new Date();
        now.setHours(now.getHours() + 1);
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        const defaultIso = now.toISOString().slice(0, 16);
        $('#manualPostScheduleTime').val(defaultIso);

        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('createManualPostModal'));
        modal.show();
    };

    $('#manualPostPhotoInput').on('change', function (e) {
        const file = e.target.files[0];
        if (file) {
            const fileUrl = URL.createObjectURL(file);
            if (file.type.startsWith('video/')) {
                $('#manualPostPhotoPreview').addClass('d-none');
                $('#manualPostVideoPreview').attr('src', fileUrl).removeClass('d-none');
            } else {
                $('#manualPostVideoPreview').addClass('d-none');
                $('#manualPostPhotoPreview').attr('src', fileUrl).removeClass('d-none');
            }
            $('#manualPostPreviewContainer').removeClass('d-none');
        }
    });

    $('#removeManualPhotoBtn').on('click', function () {
        $('#manualPostPhotoInput').val('');
        $('#manualPostPhotoPreview').attr('src', '').addClass('d-none');
        $('#manualPostVideoPreview').attr('src', '').addClass('d-none');
        $('#manualPostPreviewContainer').addClass('d-none');
    });

    function submitManualPost(publishNow) {
        const caption = $('#manualPostCaption').val().trim();
        const scheduledAt = $('#manualPostScheduleTime').val();
        const selectedPlatforms = [];
        $('input[name="manualPlatforms"]:checked').each(function () {
            selectedPlatforms.push($(this).val());
        });

        if (!selectedPlatforms.length) {
            showToast('Please select at least one target social media platform.', 'warning');
            return;
        }

        if (!caption) {
            showToast('Please enter a post caption / text content.', 'warning');
            return;
        }

        const formData = new FormData();
        formData.append('caption', caption);
        formData.append('scheduled_at', scheduledAt && !isNaN(new Date(scheduledAt)) ? new Date(scheduledAt).toISOString() : '');
        formData.append('publish_now', publishNow ? 'true' : 'false');

        selectedPlatforms.forEach(p => formData.append('platforms', p));

        const photoFile = $('#manualPostPhotoInput')[0].files[0];
        if (photoFile) {
            formData.append('photo', photoFile);
        }

        const targetBtn = publishNow ? $('#publishManualNowBtn') : $('#saveManualScheduleBtn');
        const originalText = targetBtn.html();
        targetBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Processing...');

        $.ajax({
            url: '/api/social/manual-schedule',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function (r) {
                targetBtn.prop('disabled', false).html(originalText);
                if (r.success) {
                    showToast(r.message || 'Manual post created successfully!', 'success');
                    bootstrap.Modal.getInstance(document.getElementById('createManualPostModal')).hide();
                    loadScheduledPosts();
                } else {
                    showToast(r.error || 'Failed to create post', 'error');
                }
            },
            error: function (xhr) {
                targetBtn.prop('disabled', false).html(originalText);
                showToast('Post creation failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    }

    $('#saveManualScheduleBtn').on('click', function () { submitManualPost(false); });
    $('#publishManualNowBtn').on('click', function () { submitManualPost(true); });

    // Sends again, now, the platforms of a post that did not go out
    window.retryScheduledPostItem = function (postId) {
        $.ajax({ url: `/api/social/scheduled/${postId}/retry`, type: 'POST' })
            .done(function () {
                showToast('Trying again - it goes out within a minute.', 'info');
                loadScheduledPosts();
            })
            .fail(function (xhr) { showToast((xhr.responseJSON || {}).error || 'Could not try again.', 'error'); });
    };

    window.cancelScheduledPostItem = function (postId) {
        // Undo instead of "Are you sure?" (static/js/undo.js)
        const $row = $(`[onclick="cancelScheduledPostItem(${postId})"]`).closest('tr, .sp-item, .sp-row, li');
        window.withUndo({
            message: 'Scheduled post cancelled.',
            url: `/api/social/scheduled/${postId}/cancel`,
            hide: () => $row.fadeOut(150),
            restore: () => $row.fadeIn(150),
            done: () => loadScheduledPosts(),
            failed: () => showToast('Failed to cancel scheduled post', 'error'),
        });
    };

    function showToast(msg, type = 'info') {
        const icons = {
            success: '<i class="fas fa-circle-check text-emerald me-2"></i>',
            error: '<i class="fas fa-circle-xmark text-rose me-2"></i>',
            info: '<i class="fas fa-circle-info text-blue me-2"></i>'
        };
        $('#toastBody').html((icons[type] || '') + escapeHtml(msg));
        const toastElem = document.getElementById('toast');
        if (toastElem) {
            const toast = new bootstrap.Toast(toastElem, { delay: 4000 });
            toast.show();
        }
    }


    window.testLiveAccountConnection = function (platform) {
        showToast(`Testing live ${platform.toUpperCase()} API credentials...`, 'info');
        $.ajax({
            url: `/api/social/verify/${platform}`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ platform: platform }),
            success: function (r) {
                if (r.success && r.verified) {
                    showToast(r.message || `${platform.toUpperCase()} API credentials verified cleanly!`, 'success');
                    if (r.page_name) {
                        alert(`LIVE META GRAPH API VERIFICATION SUCCESSFUL!\n\nPlatform: ${r.platform.toUpperCase()}\nConnected Page: ${r.page_name}\nCategory: ${r.category}\nPage ID: ${r.page_id}\nPermissions: ${(r.permissions || []).join(', ')}\nStatus: LIVE & ACTIVE`);
                    } else {
                        alert(`VERIFICATION SUCCESSFUL!\n\n${r.message}`);
                    }
                } else {
                    showToast(r.error || `Verification failed for ${platform}`, 'error');
                    alert(`VERIFICATION FAILED:\n${r.error || 'Invalid credentials'}`);
                }
            },
            error: function (xhr) {
                showToast('API verification failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
                alert(`API VERIFICATION ERROR:\n${xhr.responseJSON?.error || 'Could not verify credentials.'}`);
            }
        });
    };
});
