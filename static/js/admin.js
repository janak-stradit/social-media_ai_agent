// Admin Control Center - previously a modal on the Studio Chat page
// (templates/index.html's #adminModal), now a standalone page gated by
// @admin_required_page (see app.py's /admin route, auth/utils.py). Data
// loads on page ready instead of on modal-show.
$(document).ready(function () {
    function showToast(msg, type = 'info') {
        const icons = {
            success: '<i class="fas fa-check-circle text-success me-2"></i>',
            error: '<i class="fas fa-exclamation-circle text-danger me-2"></i>',
            warning: '<i class="fas fa-exclamation-triangle text-warning me-2"></i>',
            info: '<i class="fas fa-info-circle text-info me-2"></i>'
        };
        $('#toastBody').html((icons[type] || '') + msg);
        const toastElem = document.getElementById('toast');
        const toast = new bootstrap.Toast(toastElem, { delay: 3000 });
        toast.show();
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ── Tab 1: User Credits & Limits ─────────────────────────────────────
    const ACCOUNT_TYPE_LABELS = {
        individual: 'Individual',
        small: 'Small Industry',
        medium: 'Medium Industry',
        enterprise: 'Enterprise'
    };

    function accountTypeBadge(u) {
        const type = u.account_type;
        if (!type || !ACCOUNT_TYPE_LABELS[type]) {
            return '<span class="badge-acct badge-acct-none">Not set</span>';
        }
        return `<span class="badge-acct badge-acct-${type}">${ACCOUNT_TYPE_LABELS[type]}</span>`;
    }

    function loadAdminUsers() {
        $.ajax({
            url: '/api/admin/users',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const users = r.users || [];
                window._allAdminUsers = users;
                fillCostUserFilter();
                renderAdminUserKpis(users);
                renderAdminAccountSummary(users);
                applyAdminUserFilters();
            }
        });
    }

    function renderAdminAccountSummary(users) {
        const activeType = window._adminAccountTypeFilter || '';
        const counts = { individual: 0, small: 0, medium: 0, enterprise: 0, none: 0 };
        users.forEach(u => {
            if (u.account_type && counts.hasOwnProperty(u.account_type)) {
                counts[u.account_type]++;
            } else {
                counts.none++;
            }
        });

        const chips = [
            { key: '', label: 'All Accounts', count: users.length },
            { key: 'individual', label: 'Individual', count: counts.individual },
            { key: 'small', label: 'Small Industry', count: counts.small },
            { key: 'medium', label: 'Medium Industry', count: counts.medium },
            { key: 'enterprise', label: 'Enterprise', count: counts.enterprise },
            { key: 'none', label: 'Not Set', count: counts.none }
        ];

        const html = chips.map(chip => `
            <div class="summary-chip${chip.key === activeType ? ' active' : ''}" data-account-type="${chip.key}" onclick="adminFilterByAccountType('${chip.key}')">
                <span>${chip.label}</span><span class="count">${chip.count}</span>
            </div>
        `).join('');
        $('#adminAccountSummary').html(html);
    }

    window.adminFilterByAccountType = function (type) {
        window._adminAccountTypeFilter = type;
        $('#adminAccountTypeFilter').val(type);
        if (window._allAdminUsers) renderAdminAccountSummary(window._allAdminUsers);
        applyAdminUserFilters();
    };

    function applyAdminUserFilters() {
        if (!window._allAdminUsers) return;
        const q = ($('#adminUserSearchInput').val() || '').toLowerCase().trim();
        const type = window._adminAccountTypeFilter || '';

        let filtered = window._allAdminUsers;
        if (type) {
            filtered = filtered.filter(u => (type === 'none' ? !u.account_type : u.account_type === type));
        }
        if (q) {
            filtered = filtered.filter(u => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q));
        }
        renderAdminUsersTable(filtered);
    }

    const money = n => '$' + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const initials = name => (String(name || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();

    // Overview cards above the Users table
    function renderAdminUserKpis(users) {
        const active = users.filter(u => u.is_active !== false).length;
        const used = users.reduce((t, u) => t + Number(u.used_credits || 0), 0);
        const allocated = users.reduce((t, u) => t + Number(u.credit_limit || 0), 0);
        const attention = users.filter(u => u.has_pending_request || (u.credit_limit > 0 && u.used_credits / u.credit_limit >= 0.9)).length;
        const pending = users.filter(u => u.has_pending_request).length;
        $('#adminUserKpis').html(`
            <div class="admin-kpi"><div class="admin-kpi-icon"><i class="fas fa-users"></i></div>
                <div class="min-w-0"><div class="admin-kpi-label">Users</div><div class="admin-kpi-value">${users.length}</div>
                <div class="admin-kpi-sub">${users.filter(u => u.is_admin).length} admin</div></div></div>
            <div class="admin-kpi"><div class="admin-kpi-icon green"><i class="fas fa-user-check"></i></div>
                <div class="min-w-0"><div class="admin-kpi-label">Active</div><div class="admin-kpi-value">${active}</div>
                <div class="admin-kpi-sub">${users.length - active} deactivated</div></div></div>
            <div class="admin-kpi"><div class="admin-kpi-icon blue"><i class="fas fa-wallet"></i></div>
                <div class="min-w-0"><div class="admin-kpi-label">Credits used</div><div class="admin-kpi-value">${money(used)}</div>
                <div class="admin-kpi-sub">of ${money(allocated)} allocated</div></div></div>
            <div class="admin-kpi"><div class="admin-kpi-icon amber"><i class="fas fa-triangle-exclamation"></i></div>
                <div class="min-w-0"><div class="admin-kpi-label">Need attention</div><div class="admin-kpi-value">${attention}</div>
                <div class="admin-kpi-sub">${pending} request${pending === 1 ? '' : 's'} pending · ≥90% used</div></div></div>
        `);
    }

    // Brand configuration state per user (db._brand_status)
    const BRAND_SCAN_ERRORS = {
        // codes from services/website_scraper_service.py FAILURE_REASONS + brand_profile_service
        invalid_url: 'invalid website address', unsafe_url: 'not a public website', unreachable: "couldn't reach the site",
        timeout: 'site took too long', blocked: 'site blocked automated access', http_error: 'site returned an error',
        not_html: 'not a web page', thin_content: 'too little text on the site', analysis_failed: 'AI analysis failed'
    };
    function brandSite(url) {
        if (!url) return '';
        const label = String(url).replace(/^https?:\/\//, '').replace(/\/$/, '');
        const href = /^https?:\/\//.test(url) ? url : `https://${url}`;
        return `<a href="${escapeAttr(href)}" target="_blank" rel="noopener" title="${escapeAttr(url)}">${escapeHtml(label)}</a>`;
    }
    // Statuses an admin can rescan from (a self-serve account with a website on file)
    function canRescanBrand(u) {
        const b = u.brand || {};
        return ['individual', 'small', 'medium'].includes(u.account_type) && !!b.website
            && ['configured', 'failed', 'not_set_up'].includes(b.status);
    }
    function brandStatusCell(b, u) {
        b = b || { status: 'not_onboarded' };
        const cell = (badge, detail) => `<div class="admin-brand">${badge}${detail ? `<div class="admin-brand-detail">${detail}</div>` : ''}</div>`;
        switch (b.status) {
            case 'configured':
                return cell('<span class="admin-brand-badge ok"><i class="fas fa-circle-check"></i>Configured</span>',
                    [b.company_name ? `<strong class="text-dark">${escapeHtml(b.company_name)}</strong>` : '', brandSite(b.website)].filter(Boolean).join(' · ')
                    + (b.analyzed_at ? `<div>Analyzed ${escapeHtml(b.analyzed_at)}${b.compliance_confirmed ? ' · <span title="Compliance rules confirmed by the user">compliance ✓</span>' : ''}</div>` : ''));
            case 'analyzing':
                return cell('<span class="admin-brand-badge busy"><i class="fas fa-spinner fa-spin"></i>Analyzing website</span>', brandSite(b.website));
            case 'failed':
                return cell('<span class="admin-brand-badge fail"><i class="fas fa-circle-exclamation"></i>Scan failed</span>'
                    + (u && canRescanBrand(u) ? `<button type="button" class="admin-brand-rescan" onclick="adminRescanBrand(${u.id})"
                        title="Scan ${escapeAttr(b.website)} again. ${escapeAttr(u.name)} is notified when the brand profile is ready."><i class="fas fa-rotate-right"></i>Rescan</button>` : ''),
                    [escapeHtml(BRAND_SCAN_ERRORS[b.error] || (b.error || '').replace(/_/g, ' ')), brandSite(b.website)].filter(Boolean).join(' · '));
            case 'not_set_up':
                return cell('<span class="admin-brand-badge todo"><i class="fas fa-hourglass-half"></i>Not set up</span>',
                    b.website ? brandSite(b.website) : 'Skipped the website at onboarding');
            case 'company':
                return cell('<span class="admin-brand-badge company"><i class="fas fa-building"></i>Company brand</span>', 'Uses the shared Brand Configuration');
            default:
                return cell('<span class="admin-brand-badge none">Not onboarded</span>', 'No account type chosen yet');
        }
    }

    function renderAdminUsersTable(users) {
        if (!users.length) {
            $('#adminUsersTbody').html('<tr><td colspan="6" class="text-center py-5 text-muted">No users match these filters.</td></tr>');
            return;
        }
        $('#adminUsersTbody').html(users.map(u => {
            const isActive = u.is_active !== false;
            const limit = Number(u.credit_limit || 0);
            const used = Number(u.used_credits || 0);
            const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
            const barClass = pct >= 100 ? 'full' : (pct >= 75 ? 'warn' : '');
            const remaining = Math.max(0, Number(u.remaining_credits || 0));
            const note = u.has_pending_request
                ? '<span class="pending"><i class="fas fa-clock me-1"></i>Extension requested</span>'
                : (remaining <= 0 ? '<span class="text-danger fw-bold">Credits used up</span>' : `${money(remaining)} left`);
            const menuId = `adminUserMenu${u.id}`;
            return `
                <tr>
                    <td>
                        <div class="admin-user-cell">
                            <div class="admin-avatar" aria-hidden="true">${escapeHtml(initials(u.name))}</div>
                            <div class="min-w-0">
                                <div class="admin-user-name">${escapeHtml(u.name)}${u.is_admin ? '<span class="admin-tag-admin">Admin</span>' : ''}</div>
                                <div class="admin-user-email">${escapeHtml(u.email)} · #${u.id}</div>
                            </div>
                        </div>
                    </td>
                    <td>${accountTypeBadge(u)}</td>
                    <td>${brandStatusCell(u.brand, u)}</td>
                    <td>
                        <div class="admin-credit">
                            <div class="admin-credit-top"><span><strong>${money(used)}</strong> of ${money(limit)}</span><span>${Math.round(pct)}%</span></div>
                            <div class="admin-credit-bar" title="${Math.round(pct)}% of the credit limit used"><span class="${barClass}" style="width:${pct}%"></span></div>
                            <div class="admin-credit-note">${note}</div>
                        </div>
                        ${imagesLine(u)}
                    </td>
                    <td><span class="admin-status${isActive ? '' : ' off'}">${isActive ? 'Active' : 'Deactivated'}</span></td>
                    <td>
                        <div class="admin-row-actions">
                            <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 10)" title="Add $10 to the credit limit">+$10</button>
                            <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 50)" title="Add $50 to the credit limit">+$50</button>
                            <div class="dropdown">
                                <button type="button" class="btn-xs btn-xs-outline-neutral admin-kebab" id="${menuId}" data-bs-toggle="dropdown"
                                        data-bs-display="static" aria-expanded="false" aria-label="More actions for ${escapeAttr(u.name)}">
                                    <i class="fas fa-ellipsis"></i>
                                </button>
                                <ul class="dropdown-menu dropdown-menu-end admin-menu" aria-labelledby="${menuId}">
                                    <li><button type="button" class="dropdown-item" onclick="adminSetCustomCredit(${u.id}, ${limit})"><i class="fas fa-sliders"></i>Set credit limit</button></li>
                                    <li><button type="button" class="dropdown-item" onclick="adminEditUserProfile(${u.id})"><i class="fas fa-user-pen"></i>Edit profile</button></li>
                                    <li><button type="button" class="dropdown-item" onclick="openImageAccessPanel(${u.id})"><i class="fas fa-image"></i>Image &amp; video access…</button></li>
                                    ${canRescanBrand(u) ? `<li><button type="button" class="dropdown-item" onclick="adminRescanBrand(${u.id})"><i class="fas fa-rotate-right"></i>Rescan website</button></li>` : ''}
                                    <li><button type="button" class="dropdown-item" onclick="adminSetUserActive(${u.id}, ${!isActive})">
                                        <i class="fas ${isActive ? 'fa-user-slash' : 'fa-user-check'}"></i>${isActive ? 'Deactivate account' : 'Activate account'}</button></li>
                                    ${u.is_admin ? '' : `<li><hr class="dropdown-divider"></li>
                                    <li><button type="button" class="dropdown-item text-danger" onclick="adminOpenDeleteUser(${u.id})"><i class="fas fa-trash-can"></i>Delete user…</button></li>`}
                                </ul>
                            </div>
                        </div>
                    </td>
                </tr>`;
        }).join(''));
    }

    // Rescan a user's website (admin only). The scan runs in the background; the
    // list refreshes until it finishes, and the user is notified when it's ready.
    let brandPoll = null;
    function pollBrandScans(tries) {
        clearTimeout(brandPoll);
        const busy = (window._allAdminUsers || []).some(u => u.brand && u.brand.status === 'analyzing');
        if (!busy || tries <= 0) return;
        brandPoll = setTimeout(function () {
            if ($('.admin-menu.show').length) return pollBrandScans(tries);  // don't close an open menu
            $.get('/api/admin/users', function (r) {
                if (!r.success) return;
                const before = {};
                (window._allAdminUsers || []).forEach(u => { before[u.id] = u.brand && u.brand.status; });
                window._allAdminUsers = r.users || [];
                renderAdminUserKpis(window._allAdminUsers);
                applyAdminUserFilters();
                window._allAdminUsers.forEach(u => {
                    if (before[u.id] !== 'analyzing' || !u.brand || u.brand.status === 'analyzing') return;
                    if (u.brand.status === 'failed') showToast(`The scan for ${u.name} failed: ${BRAND_SCAN_ERRORS[u.brand.error] || 'unknown error'}.`, 'error');
                    else showToast(`${u.name}'s brand profile is ready. They've been notified.`, 'success');
                });
                pollBrandScans(tries - 1);
            });
        }, 5000);
    }

    window.adminRescanBrand = function (userId) {
        const user = (window._allAdminUsers || []).find(u => u.id === userId);
        $.ajax({
            url: `/api/admin/users/${userId}/brand-rescan`,
            type: 'POST',
            contentType: 'application/json',
            data: '{}',
            success: function (r) {
                if (!r.success) return;
                if (user) user.brand = r.brand;
                applyAdminUserFilters();
                showToast(`Scanning ${r.brand.website}. ${user ? user.name : 'The user'} will be notified when the brand profile is ready.`, 'info');
                pollBrandScans(60);  // up to 5 minutes
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Could not start the scan.', 'error');
            }
        });
    };

    window.adminSetUserActive = function (userId, isActive) {
        $.ajax({
            url: `/api/admin/users/${userId}/active`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ is_active: isActive }),
            success: function (r) {
                if (r.success) {
                    showToast(isActive ? 'User activated.' : 'User deactivated.', 'success');
                    loadAdminUsers();
                } else {
                    showToast(r.error || 'Failed to update user.', 'error');
                }
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to update user.', 'error');
            }
        });
    };

    $('#adminUserSearchInput').on('input', applyAdminUserFilters);

    $('#adminAccountTypeFilter').on('change', function () {
        window._adminAccountTypeFilter = $(this).val();
        if (window._allAdminUsers) renderAdminAccountSummary(window._allAdminUsers);
        applyAdminUserFilters();
    });

    window.adminAddCredits = function (userId, addAmount) {
        $.ajax({
            url: `/api/admin/users/${userId}/credits`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ add_amount: addAmount }),
            success: function (r) {
                if (r.success) {
                    showToast(`Added +$${addAmount} credits to user!`, 'success');
                    loadAdminUsers();
                }
            },
            error: function (xhr) {
                showToast('Failed to update credits: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    window.adminSetCustomCredit = function (userId, currentLimit) {
        const input = prompt(`Set new credit limit ($USD) for User ID ${userId}:`, currentLimit);
        if (input === null) return;
        const newLimit = parseFloat(input);
        if (isNaN(newLimit) || newLimit < 0) {
            showToast('Invalid credit limit value', 'warning');
            return;
        }

        $.ajax({
            url: `/api/admin/users/${userId}/credits`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ new_limit: newLimit }),
            success: function (r) {
                if (r.success) {
                    showToast(`Updated credit limit to $${newLimit.toFixed(2)}!`, 'success');
                    loadAdminUsers();
                }
            },
            error: function (xhr) {
                showToast('Failed to update limit: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    window.adminEditUserProfile = function (userId) {
        const user = (window._allAdminUsers || []).find(u => u.id === userId);
        if (!user) return;

        window._editUserId = userId;
        $('#editUserFormError').addClass('d-none').text('');
        $('#editUserId').text('#' + user.id);
        $('#editUserAccountType').html(accountTypeBadge(user));
        $('#editUserStatus').html(user.is_active !== false
            ? '<span class="badge bg-success">Active</span>'
            : '<span class="badge bg-danger">Deactivated</span>');
        $('#editUserCreditLimit').text('$' + Number(user.credit_limit).toFixed(2));
        $('#editUserNameInput').val(user.name);
        $('#editUserEmailInput').val(user.email);
        $('#editUserAccountTypeSelect').val(user.account_type || '');
        $('#editUserWebsiteInput').val(user.company_website || '');

        $('#editUserBackdrop').addClass('open');
        $('#editUserPanel').addClass('open');
    };

    window.closeEditUserPanel = function () {
        $('#editUserBackdrop').removeClass('open');
        $('#editUserPanel').removeClass('open');
        window._editUserId = null;
    };

    window.submitEditUserProfile = function () {
        const userId = window._editUserId;
        if (!userId) return;

        const newName = ($('#editUserNameInput').val() || '').trim();
        const newEmail = ($('#editUserEmailInput').val() || '').trim();
        const newAccountType = $('#editUserAccountTypeSelect').val() || '';
        const newWebsite = ($('#editUserWebsiteInput').val() || '').trim();
        const $error = $('#editUserFormError');

        if (!newName || !newEmail) {
            $error.text('Name and email cannot be empty.').removeClass('d-none');
            return;
        }

        const $saveBtn = $('#editUserSaveBtn');
        $saveBtn.prop('disabled', true);
        $error.addClass('d-none').text('');

        $.ajax({
            url: `/api/admin/users/${userId}/profile`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                name: newName,
                email: newEmail,
                account_type: newAccountType,
                company_website: newWebsite
            }),
            success: function (r) {
                $saveBtn.prop('disabled', false);
                if (r.success) {
                    showToast('User profile updated!', 'success');
                    loadAdminUsers();
                    closeEditUserPanel();
                } else {
                    $error.text(r.error || 'Failed to update profile.').removeClass('d-none');
                }
            },
            error: function (xhr) {
                $saveBtn.prop('disabled', false);
                $error.text((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to update profile.').removeClass('d-none');
            }
        });
    };

    window.adminOpenDeleteUser = function (userId) {
        const user = (window._allAdminUsers || []).find(u => u.id === userId);
        if (!user) return;

        window._deleteUserId = userId;
        window._deleteUserEmail = user.email;
        $('#deleteUserFormError').addClass('d-none').text('');
        $('#deleteUserId').text('#' + user.id);
        $('#deleteUserAccountType').html(accountTypeBadge(user));
        $('#deleteUserName').text(user.name);
        $('#deleteUserEmailTarget').text(user.email);
        $('#deleteUserConfirmInput').val('');
        $('#deleteUserConfirmBtn').prop('disabled', true);

        $('#deleteUserBackdrop').addClass('open');
        $('#deleteUserPanel').addClass('open');
        $('#deleteUserConfirmInput').trigger('focus');
    };

    window.closeDeleteUserPanel = function () {
        $('#deleteUserBackdrop').removeClass('open');
        $('#deleteUserPanel').removeClass('open');
        window._deleteUserId = null;
        window._deleteUserEmail = null;
    };

    $(document).on('input', '#deleteUserConfirmInput', function () {
        const typed = $(this).val().trim().toLowerCase();
        const target = (window._deleteUserEmail || '').toLowerCase();
        $('#deleteUserConfirmBtn').prop('disabled', !target || typed !== target);
    });

    window.submitDeleteUser = function () {
        const userId = window._deleteUserId;
        const typedEmail = ($('#deleteUserConfirmInput').val() || '').trim();
        if (!userId || !typedEmail) return;

        const $btn = $('#deleteUserConfirmBtn');
        const $error = $('#deleteUserFormError');
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Deleting...');
        $error.addClass('d-none').text('');

        $.ajax({
            url: `/api/admin/users/${userId}`,
            type: 'DELETE',
            contentType: 'application/json',
            data: JSON.stringify({ confirm_email: typedEmail }),
            success: function (r) {
                if (r.success) {
                    showToast('User and all their data permanently deleted.', 'success');
                    closeDeleteUserPanel();
                    loadAdminUsers();
                } else {
                    $btn.prop('disabled', false).html('<i class="fas fa-trash-can me-1"></i>Permanently Delete');
                    $error.text(r.error || 'Failed to delete user.').removeClass('d-none');
                }
            },
            error: function (xhr) {
                $btn.prop('disabled', false).html('<i class="fas fa-trash-can me-1"></i>Permanently Delete');
                $error.text((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to delete user.').removeClass('d-none');
            }
        });
    };

    // ── Tab 2: Credit Extension Requests ─────────────────────────────────
    function loadAdminRequests() {
        $.ajax({
            url: '/api/admin/credit-requests',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const requests = r.requests || [];
                window._allAdminReqs = requests;

                const pendingCount = requests.filter(req => req.status === 'pending').length;
                if (pendingCount > 0) {
                    $('#adminPendingBadge').text(pendingCount).removeClass('d-none');
                } else {
                    $('#adminPendingBadge').addClass('d-none');
                }

                renderAdminReqTable(requests);
            }
        });
    }

    function renderAdminReqTable(reqs) {
        let html = '';
        if (!reqs.length) {
            html = '<tr><td colspan="8" class="text-center py-4 text-muted">No credit extension requests found.</td></tr>';
        } else {
            reqs.forEach(req => {
                let statusBadge = '<span class="badge bg-warning text-dark">Pending</span>';
                if (req.status === 'approved') statusBadge = '<span class="badge bg-success">Approved</span>';
                if (req.status === 'rejected') statusBadge = '<span class="badge bg-danger">Rejected</span>';

                let actionBtns = '—';
                if (req.status === 'pending') {
                    actionBtns = `
                        <div class="admin-actions-row">
                            <button type="button" class="btn-xs btn-xs-solid-success" onclick="adminApproveReq(${req.id})">
                                <i class="fas fa-check me-1"></i>Approve (+$${req.requested_amount})
                            </button>
                            <button type="button" class="btn-xs btn-xs-solid-danger" onclick="adminRejectReq(${req.id})">
                                <i class="fas fa-times me-1"></i>Reject
                            </button>
                        </div>
                    `;
                }

                html += `
                    <tr>
                        <td>#${req.id}</td>
                        <td><strong>${escapeHtml(req.user_name)}</strong><br><small class="text-muted">${escapeHtml(req.user_email)}</small></td>
                        <td>$${Number(req.current_limit).toFixed(2)}</td>
                        <td><strong class="text-success">+$${Number(req.requested_amount).toFixed(2)}</strong></td>
                        <td style="max-width: 250px;">${escapeHtml(req.reason || '—')}</td>
                        <td>${req.created_at}</td>
                        <td>${statusBadge}</td>
                        <td>${actionBtns}</td>
                    </tr>
                `;
            });
        }
        $('#adminReqTbody').html(html);
    }

    $('#filterReqAll').on('click', function () {
        $(this).addClass('active').siblings().removeClass('active');
        if (window._allAdminReqs) renderAdminReqTable(window._allAdminReqs);
    });

    $('#filterReqPending').on('click', function () {
        $(this).addClass('active').siblings().removeClass('active');
        if (window._allAdminReqs) {
            renderAdminReqTable(window._allAdminReqs.filter(r => r.status === 'pending'));
        }
    });

    window.adminApproveReq = function (reqId) {
        $.ajax({
            url: `/api/admin/credit-requests/${reqId}/approve`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Request approved! User credit limit increased. '
                        + (r.email_sent ? 'The user has been emailed.' : 'The email to the user could not be sent.'),
                        r.email_sent ? 'success' : 'warning');
                    loadAdminRequests();
                    loadAdminUsers();
                }
            },
            error: function (xhr) {
                showToast('Approval failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    window.adminRejectReq = function (reqId) {
        $.ajax({
            url: `/api/admin/credit-requests/${reqId}/reject`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Request rejected. '
                        + (r.email_sent ? 'The user has been emailed.' : 'The email to the user could not be sent.'),
                        r.email_sent ? 'info' : 'warning');
                    loadAdminRequests();
                }
            },
            error: function (xhr) {
                showToast('Rejection failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    // ── Tab 3: Global Cost History ───────────────────────────────────────
    // ── Global Cost History: server-side filters + pagination ───────────
    const COST_TYPE_LABELS = { runs: 'Runs', runs_with_images: 'Runs with images', charges: 'Image charges' };
    const COST_SORT_LABELS = { oldest: 'Oldest first', cost: 'Highest cost' };
    const costState = { page: 1, pageSize: 25 };
    let costRequest = null;

    function costFilters() {
        return {
            q: $('#costFilterQ').val().trim(),
            user_id: $('#costFilterUser').val(),
            type: $('#costFilterType').val(),
            platform: $('#costFilterPlatform').val(),
            date_from: $('#costFilterFrom').val(),
            date_to: $('#costFilterTo').val(),
            min_cost: $('#costFilterMinCost').val(),
            sort: $('#costFilterSort').val()
        };
    }

    // Users for the "User" filter, from the users table already loaded
    function fillCostUserFilter() {
        const users = window._allAdminUsers || [];
        const $sel = $('#costFilterUser');
        if (!users.length || $sel.data('filled') === users.length) return;
        const current = $sel.val();
        $sel.html('<option value="">All users</option>' + users
            .slice().sort((a, b) => String(a.name).localeCompare(String(b.name)))
            .map(u => `<option value="${u.id}">${escapeHtml(u.name)} (${escapeHtml(u.email)})</option>`).join(''));
        $sel.val(current).data('filled', users.length);
    }
    window.fillCostUserFilter = fillCostUserFilter;

    function renderCostActiveFilters(f) {
        const chips = [];
        const chip = (key, text) => chips.push(`<span class="cost-chip">${escapeHtml(text)}<button type="button" data-clear="${key}" aria-label="Remove filter ${escapeAttr(text)}">&times;</button></span>`);
        if (f.q) chip('q', `"${f.q}"`);
        if (f.user_id) chip('user_id', $('#costFilterUser option:selected').text().replace(/ \(.*\)$/, ''));
        if (f.type && f.type !== 'all') chip('type', COST_TYPE_LABELS[f.type]);
        if (f.platform) chip('platform', $('#costFilterPlatform option:selected').text());
        if (f.date_from || f.date_to) chip('dates', `${f.date_from || '…'} → ${f.date_to || 'today'}`);
        if (f.min_cost) chip('min_cost', `≥ $${f.min_cost}`);
        if (f.sort && f.sort !== 'newest') chip('sort', COST_SORT_LABELS[f.sort]);
        $('#costActiveFilters').html(chips.join(''));
    }

    // 1 … 4 5 [6] 7 8 … 20
    function costPageNumbers(page, pages) {
        const keep = new Set([1, pages, page - 1, page, page + 1]);
        if (page <= 3) [2, 3, 4].forEach(n => keep.add(n));
        if (page >= pages - 2) [pages - 3, pages - 2, pages - 1].forEach(n => keep.add(n));
        const list = [...keep].filter(n => n >= 1 && n <= pages).sort((a, b) => a - b);
        const out = [];
        list.forEach((n, i) => {
            if (i && n - list[i - 1] > 1) out.push('gap');
            out.push(n);
        });
        return out;
    }

    function renderCostPagination(r) {
        const total = Number(r.total || 0), page = Number(r.page || 1), pages = Number(r.pages || 1), size = Number(r.page_size || costState.pageSize);
        const from = total ? (page - 1) * size + 1 : 0, to = Math.min(total, page * size);
        $('#costPageInfo').html(total ? `Showing <strong>${from.toLocaleString()}–${to.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong>` : '');
        if (pages <= 1) { $('#costPages').html(''); return; }
        const btn = (label, target, opts = {}) => `<button type="button" class="cost-page-btn${opts.active ? ' active' : ''}" data-page="${target}"
            ${opts.disabled ? 'disabled' : ''} ${opts.active ? 'aria-current="page"' : ''} aria-label="${opts.aria || 'Page ' + target}">${label}</button>`;
        $('#costPages').html(
            btn('<i class="fas fa-angle-left"></i>', page - 1, { disabled: page <= 1, aria: 'Previous page' })
            + costPageNumbers(page, pages).map(n => n === 'gap' ? '<span class="cost-page-gap">…</span>' : btn(n, n, { active: n === page })).join('')
            + btn('<i class="fas fa-angle-right"></i>', page + 1, { disabled: page >= pages, aria: 'Next page' })
        );
    }

    function loadAdminCostHistory(page) {
        if (page) costState.page = page;
        const f = costFilters();
        if (f.date_from && f.date_to && f.date_from > f.date_to) {
            $('#costResultsSummary').html('<span class="text-danger">The start date is after the end date.</span>');
            return;
        }
        const params = $.param(Object.assign({ page: costState.page, page_size: costState.pageSize },
            Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v != null))));
        renderCostActiveFilters(f);
        $('#adminCostHistoryTbody').addClass('loading');
        if (costRequest) costRequest.abort();
        costRequest = $.ajax({
            url: '/api/admin/cost-history?' + params,
            type: 'GET',
            complete: function () { costRequest = null; $('#adminCostHistoryTbody').removeClass('loading'); },
            error: function (xhr, status) {
                if (status === 'abort') return;
                $('#adminCostHistoryTbody').html('<tr><td colspan="8" class="text-center py-4 text-danger">Could not load the cost history.</td></tr>');
            },
            success: function (r) {
                if (!r.success) return;
                const history = r.history || [];
                const summary = r.summary || {};
                // Past the last page (e.g. a narrower filter): go to the last one
                if (!history.length && r.total > 0 && costState.page > r.pages) { loadAdminCostHistory(r.pages); return; }

                const totalCost = Number(summary.total_system_cost_usd || 0);
                const totalRuns = Number(summary.total_runs || 0);
                $('#adminTotalSystemCost').text(money(totalCost));
                $('#adminTotalRuns').text(totalRuns.toLocaleString());
                $('#adminAvgRunCost').text(totalRuns ? '$' + (totalCost / totalRuns).toFixed(3) : '$0.00');
                $('#adminTotalSystemTokens').text(Number(summary.total_tokens || 0).toLocaleString());

                const filtered = r.filtered || {};
                const isFiltered = $('#costActiveFilters').children().length > 0;
                $('#costResultsSummary').html(
                    `<strong>${Number(filtered.count || 0).toLocaleString()}</strong> ${isFiltered ? 'matching ' : ''}entr${filtered.count === 1 ? 'y' : 'ies'}`
                    + ` · <strong>$${Number(filtered.cost_usd || 0).toFixed(4)}</strong> spend`
                    + ` · <strong>${Number(filtered.tokens || 0).toLocaleString()}</strong> tokens`);
                renderCostPagination(r);

                let html = '';
                if (!history.length) {
                    html = isFiltered
                        ? '<tr><td colspan="8" class="text-center py-4 text-muted">Nothing matches these filters. <a href="#" id="costEmptyReset">Reset filters</a></td></tr>'
                        : '<tr><td colspan="8" class="text-center py-4 text-muted">No generation history recorded.</td></tr>';
                } else {
                    history.forEach(h => {
                        // Image/video charged outside a saved run (e.g. Analysis Dashboard): no run details
                        const isCharge = h.kind === 'media_charge';
                        html += `
                            <tr class="${isCharge ? '' : 'run-row'}" ${isCharge ? 'title="Image generated outside a Studio Chat post (e.g. Analysis Dashboard)"' : `onclick="openRunDetails(${h.id})" title="View everything this run generated"`}>
                                <td>${isCharge ? '<span class="run-video-badge" style="height:auto;padding:2px 8px">Image charge</span>' : `#${h.id}`}</td>
                                <td>${escapeHtml(h.user_email)}</td>
                                <td>${h.timestamp}</td>
                                <td><div class="run-excerpt">${escapeHtml(h.story)}</div></td>
                                <td>${runThumbsHtml(h.media)}</td>
                                <td><span class="badge bg-secondary me-1">${h.tone || 'Auto'}</span> <small class="text-muted">${(h.platforms || []).join(', ')}</small></td>
                                <td><span class="font-monospace">${Number(h.tokens_used).toLocaleString()}</span></td>
                                <td><strong class="text-warning font-monospace">$${Number(h.cost_usd).toFixed(6)}</strong></td>
                            </tr>
                        `;
                    });
                }
                $('#adminCostHistoryTbody').html(html);
            }
        });
    }

    // Filters: text waits for a pause in typing; everything else applies at once
    let costSearchTimer = null;
    $('#costFilterQ').on('input', function () {
        clearTimeout(costSearchTimer);
        costSearchTimer = setTimeout(() => loadAdminCostHistory(1), 350);
    });
    $('#costFilterMinCost').on('input', function () {
        clearTimeout(costSearchTimer);
        costSearchTimer = setTimeout(() => loadAdminCostHistory(1), 450);
    });
    $('#costFilterUser, #costFilterType, #costFilterPlatform, #costFilterFrom, #costFilterTo, #costFilterSort')
        .on('change', () => loadAdminCostHistory(1));
    $('#costPageSize').on('change', function () {
        costState.pageSize = Number($(this).val()) || 25;
        loadAdminCostHistory(1);
    });
    $(document).on('click', '#costPages .cost-page-btn', function () {
        const target = Number($(this).data('page'));
        if (!target) return;
        loadAdminCostHistory(target);
        document.getElementById('costFilters').scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    function resetCostFilters() {
        $('#costFilterQ, #costFilterFrom, #costFilterTo, #costFilterMinCost').val('');
        $('#costFilterUser, #costFilterPlatform').val('');
        $('#costFilterType').val('all');
        $('#costFilterSort').val('newest');
        loadAdminCostHistory(1);
    }
    $('#costFilterReset').on('click', resetCostFilters);
    $(document).on('click', '#costEmptyReset', function (e) { e.preventDefault(); resetCostFilters(); });
    $(document).on('click', '#costActiveFilters [data-clear]', function () {
        const key = $(this).data('clear');
        if (key === 'q') $('#costFilterQ').val('');
        if (key === 'user_id') $('#costFilterUser').val('');
        if (key === 'type') $('#costFilterType').val('all');
        if (key === 'platform') $('#costFilterPlatform').val('');
        if (key === 'dates') $('#costFilterFrom, #costFilterTo').val('');
        if (key === 'min_cost') $('#costFilterMinCost').val('');
        if (key === 'sort') $('#costFilterSort').val('newest');
        loadAdminCostHistory(1);
    });

    // ── Init ──────────────────────────────────────────────────────────────

    // ── Global Cost History: thumbnails + run details panel ──────────────
    function runThumbsHtml(media) {
        const images = (media && media.images) || [];
        const videos = (media && media.videos) || [];
        if (!images.length && !videos.length) return '<span class="run-no-media">Text only</span>';
        const shown = images.slice(0, 3).map(u => `<img class="run-thumb" src="${escapeAttr(u)}" alt="" loading="lazy">`).join('');
        const more = images.length > 3 ? `<span class="run-thumb-more">+${images.length - 3}</span>` : '';
        const video = videos.length ? `<span class="run-video-badge" title="${videos.length} video(s)"><i class="fas fa-video"></i></span>` : '';
        return `<div class="run-thumbs">${shown}${more}${video}</div>`;
    }

    const PLATFORM_LABELS = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube' };
    const PLATFORM_ICONS = { linkedin: 'fab fa-linkedin', facebook: 'fab fa-facebook', instagram: 'fab fa-instagram', youtube: 'fab fa-youtube' };

    window.openRunDetails = function (runId) {
        $('#runDetailsTitle').html(`<i class="fas fa-layer-group text-primary me-2"></i>Run #${runId}`);
        $('#runDetailsBody').html('<div class="text-center text-muted py-5"><i class="fas fa-spinner fa-spin me-2"></i>Loading run...</div>');
        $('#runDetailsBackdrop, #runDetailsPanel').addClass('open');
        $.getJSON(`/api/admin/runs/${runId}`).done(function (r) {
            const run = r.run || {};
            $('#runDetailsTitle').html(`<i class="fas fa-layer-group text-primary me-2"></i>Run #${Number(run.id || runId)}`);
            const outputs = (run.outputs || []).map(o => {
                const q = o.quality || {};
                const checks = q.checks_total ? `<span class="run-chip${q.checks_passed < q.checks_total ? ' warn' : ''}">${q.checks_passed}/${q.checks_total} checks</span>` : '';
                const rewritten = q.rewritten ? '<span class="run-chip warn">caption rewritten</span>' : '';
                const flags = o.compliance_flags ? `<span class="run-chip warn">${o.compliance_flags} compliance flag${o.compliance_flags === 1 ? '' : 's'}</span>` : '';
                const image = o.image ? `<img class="run-output-image" src="${escapeAttr(o.image.url)}" alt="Generated image" onclick="adminPreviewImage('${escapeAttr(o.image.url)}')">` : '';
                const video = o.video ? `<video class="run-output-image" src="${escapeAttr(o.video.url)}" controls preload="metadata"></video>` : '';
                const prompt = (o.image && o.image.prompt) || o.media_prompt;
                return `
                    <div class="run-output">
                        <div class="run-output-head">
                            <span class="run-platform"><i class="${PLATFORM_ICONS[o.platform] || 'fas fa-share-nodes'}"></i>${escapeHtml(PLATFORM_LABELS[o.platform] || o.platform)}</span>
                            <span class="d-flex gap-1 flex-wrap justify-content-end">${checks}${rewritten}${flags}</span>
                        </div>
                        ${image}${video}
                        ${o.caption ? `<div class="run-caption">${escapeHtml(o.caption)}</div>` : '<div class="run-caption text-muted">No caption saved.</div>'}
                        ${o.hashtags.length ? `<div class="run-tags">${o.hashtags.map(t => `<span>${escapeHtml(t)}</span>`).join('')}</div>` : ''}
                        ${prompt ? `<details class="run-prompt"><summary>${o.image ? 'Image prompt' : 'Media prompt'}</summary><div class="mt-1">${escapeHtml(prompt)}</div></details>` : ''}
                    </div>`;
            }).join('');
            const agents = (run.agents || []).map(a => `<li><strong>${escapeHtml(a.name || '')}</strong> - ${escapeHtml(a.role || '')}</li>`).join('');
            $('#runDetailsBody').html(`
                <div class="run-meta">
                    <div><span>User</span><strong title="${escapeAttr(run.user_email)}">${escapeHtml(run.user_name)}</strong></div>
                    <div><span>When</span><strong>${escapeHtml(run.timestamp || '')}</strong></div>
                    <div><span>Tone</span><strong>${escapeHtml(run.tone || 'Auto')}</strong></div>
                    <div><span>Platforms</span><strong>${escapeHtml((run.platforms || []).map(p => PLATFORM_LABELS[p] || p).join(', ') || '—')}</strong></div>
                    <div><span>Tokens</span><strong>${Number(run.tokens_used || 0).toLocaleString()}</strong></div>
                    <div><span>Cost</span><strong>$${Number(run.cost_usd || 0).toFixed(4)}</strong></div>
                </div>
                <div class="run-section-title">Brief / prompt</div>
                <div class="run-brief">${escapeHtml(run.story || '')}</div>
                <div class="run-section-title">Generated content</div>
                ${outputs || '<div class="text-muted small">Nothing was saved for this run.</div>'}
                ${agents ? `<div class="run-section-title">Pipeline</div><ul class="run-agents">${agents}</ul>` : ''}
            `);
        }).fail(function (xhr) {
            $('#runDetailsBody').html(`<div class="alert-inline alert-inline-danger">${escapeHtml((xhr.responseJSON || {}).error || 'Could not load this run.')}</div>`);
        });
    };

    window.closeRunDetails = function () {
        $('#runDetailsBackdrop, #runDetailsPanel').removeClass('open');
    };

    // Full-size image view (same look as Studio Chat's preview)
    window.adminPreviewImage = function (src) {
        let $box = $('#imageLightbox');
        if (!$box.length) {
            $('body').append(`
                <div class="image-lightbox" id="imageLightbox" role="dialog" aria-modal="true" aria-label="Image preview">
                    <div class="image-lightbox-toolbar">
                        <a class="image-lightbox-btn" id="imageLightboxOpen" target="_blank" rel="noopener" title="Open full size in a new tab"><i class="fas fa-up-right-from-square"></i></a>
                        <button type="button" class="image-lightbox-btn" id="imageLightboxClose" title="Close (Esc)"><i class="fas fa-xmark"></i></button>
                    </div>
                    <img class="image-lightbox-img" alt="Generated image preview">
                </div>`);
            $box = $('#imageLightbox');
            $box.on('click', function (e) { if (e.target === this) $box.removeClass('show'); });
            $('#imageLightboxClose').on('click', () => $box.removeClass('show'));
        }
        $box.find('.image-lightbox-img').attr('src', src);
        $('#imageLightboxOpen').attr('href', src);
        $box.addClass('show');
    };

    $(document).on('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if ($('#imageLightbox').hasClass('show')) { $('#imageLightbox').removeClass('show'); return; }
        if ($('#runDetailsPanel').hasClass('open')) closeRunDetails();
        if ($('#imageAccessPanel').hasClass('open')) closeImageAccessPanel();
        if ($('#presetEditPanel').hasClass('open')) closePresetEditor();
    });

    // ── Image limits & model access ────────────────────────────────────
    // Image Settings tab = defaults for everyone (daily limit, models + price);
    // users table ⋯ -> Image access = one user's own limit / model.
    window._imageSettings = null;

    function imagePrice(id) {
        const m = ((window._imageSettings || {}).models || []).find(x => x.id === id);
        return m ? `$${Number(m.price).toFixed(2)}` : '';
    }

    function imagesLine(u) {
        const im = u.images;
        if (!im) return '';
        const full = !im.unlimited && im.used_today >= im.limit;
        const count = im.unlimited ? `${im.used_today} today · ∞` : `${im.used_today} / ${im.limit} today`;
        const custom = (im.custom_limit !== null && im.custom_limit !== undefined) || im.custom_model
            ? '<span class="tag">custom</span>' : '';
        const video = im.video_enabled ? '<span class="tag"><i class="fas fa-video"></i> video</span>' : '';
        const title = `Images today: ${im.unlimited ? `${im.used_today} (unlimited)` : `${im.used_today} of ${im.limit}`}`
            + ` · model ${im.model} · video ${im.video_enabled ? 'on' : 'off'}. Click to change.`;
        return `<div><span class="admin-images-line${full ? ' full' : ''}" role="button" tabindex="0" title="${escapeAttr(title)}"
                    onclick="openImageAccessPanel(${u.id})"><i class="fas fa-image"></i>${count}${custom}${video}</span></div>`;
    }

    window.loadImageSettings = function (render) {
        return $.getJSON('/api/admin/image-settings').done(function (r) {
            window._imageSettings = r.settings;
            window._availableImageModels = r.available_models || [];
            if (render) renderImageSettings();
        });
    };

    function modelRowHtml(m, isDefault) {
        return `
            <tr>
                <td><input type="text" class="form-control form-control-sm img-model-id" list="imgAvailableModels" maxlength="128"
                           value="${escapeAttr(m.id || '')}" placeholder="e.g. gemini-3.1-flash-image" aria-label="Model id"></td>
                <td><div class="img-price">$<input type="number" class="form-control form-control-sm img-model-price" min="0" step="0.01"
                           value="${m.price === undefined ? '' : escapeAttr(m.price)}" aria-label="Price per image"></div></td>
                <td><label class="img-default-radio"><input type="radio" name="imgDefaultModel"${isDefault ? ' checked' : ''}>Default</label></td>
                <td><button type="button" class="img-remove" title="Remove this model" onclick="removeImageModelRow(this)"><i class="fas fa-trash-can"></i></button></td>
            </tr>`;
    }

    function syncRemoveButtons() {
        const $rows = $('#imgModelsTbody tr');
        $rows.find('.img-remove').prop('disabled', $rows.length <= 1);
    }

    function renderImageSettings() {
        const s = window._imageSettings;
        if (!s) return;
        $('#imgDefaultLimitInput').val(s.default_limit);
        $('#imgModelsTbody').html(s.models.map(m => modelRowHtml(m, m.id === s.default_model)).join(''));
        const available = window._availableImageModels || [];
        $('#imgAvailableModels').html(available.map(id => `<option value="${escapeAttr(id)}">`).join(''));
        $('#imgAvailableHint').text(available.length
            ? `Image models on your HeyRoute image key: ${available.join(', ')}. Other models on the key are text models and can't make images.`
            : 'Type the HeyRoute model id exactly as HeyRoute lists it.');
        $('#imgSettingsError').addClass('d-none');
        syncRemoveButtons();
        flagNonImageModels();
    }

    // Red outline + tooltip on a model the image key can't make images with
    function flagNonImageModels() {
        const available = window._availableImageModels || [];
        $('#imgModelsTbody .img-model-id').each(function () {
            const id = $(this).val().trim();
            const bad = available.length && id && !available.includes(id);
            $(this).toggleClass('is-invalid', !!bad)
                .attr('title', bad ? `${id} can't make images on your HeyRoute image key - remove it or pick an image model.` : '');
        });
    }
    $(document).on('input change', '#imgModelsTbody .img-model-id', flagNonImageModels);

    window.addImageModelRow = function () {
        $('#imgModelsTbody').append(modelRowHtml({ id: '', price: '' }, false));
        syncRemoveButtons();
        $('#imgModelsTbody tr:last .img-model-id').trigger('focus');
    };

    window.removeImageModelRow = function (btn) {
        const $row = $(btn).closest('tr');
        const wasDefault = $row.find('input[name=imgDefaultModel]').prop('checked');
        $row.remove();
        if (wasDefault) $('#imgModelsTbody input[name=imgDefaultModel]').first().prop('checked', true);
        syncRemoveButtons();
    };

    window.saveImageSettings = function () {
        const $err = $('#imgSettingsError').addClass('d-none');
        let defaultModel = '';
        const models = $('#imgModelsTbody tr').map(function () {
            const id = $(this).find('.img-model-id').val().trim();
            if ($(this).find('input[name=imgDefaultModel]').prop('checked')) defaultModel = id;
            return { id: id, price: $(this).find('.img-model-price').val() };
        }).get().filter(m => m.id);
        const missingPrice = models.find(m => m.price === '' || Number(m.price) < 0);
        const fail = msg => $err.text(msg).removeClass('d-none');
        if (!models.length) return fail('Add at least one image model.');
        if (missingPrice) return fail(`Enter a price for ${missingPrice.id}.`);
        if (!defaultModel) return fail('Choose the default model.');
        const $btn = $('#imgSettingsSaveBtn').prop('disabled', true);
        $.ajax({
            url: '/api/admin/image-settings',
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({
                default_limit: $('#imgDefaultLimitInput').val(),
                default_model: defaultModel,
                models: models.map(m => ({ id: m.id, price: Number(m.price) }))
            }),
            success: function (r) {
                window._imageSettings = r.settings;
                renderImageSettings();
                showToast('Image settings saved.', 'success');
                loadAdminUsers();  // limits/models shown in the users table follow the defaults
            },
            error: function (xhr) { fail((xhr.responseJSON || {}).error || 'Could not save image settings.'); },
            complete: function () { $btn.prop('disabled', false); }
        });
    };

    window.openImageAccessPanel = function (userId) {
        const u = (window._allAdminUsers || []).find(x => x.id === userId);
        if (!u) return;
        const open = function () {
            const s = window._imageSettings;
            const im = u.images || {};
            window._imageAccessUserId = userId;
            $('#imageAccessUser').text(u.name).attr('title', u.email);
            $('#imageAccessUsed').text(im.unlimited ? `${im.used_today} (unlimited)` : `${im.used_today} of ${im.limit}`);
            $('#imageAccessDefaultHint').text(u.is_admin
                ? 'Admins: unlimited'
                : `${s.default_limit} image${s.default_limit === 1 ? '' : 's'} per day (Image Settings)`);
            const custom = im.custom_limit;
            const choice = custom === null || custom === undefined ? 'default' : (custom === -1 ? 'unlimited' : 'custom');
            $(`input[name=imageAccessLimit][value=${choice}]`).prop('checked', true);
            $('#imageAccessCustomInput').val(choice === 'custom' ? custom : (im.limit || s.default_limit));
            const options = [`<option value="">Default - ${escapeHtml(s.default_model)} (${imagePrice(s.default_model)}/image)</option>`]
                .concat(s.models.map(m => `<option value="${escapeAttr(m.id)}">${escapeHtml(m.id)} (${imagePrice(m.id)}/image)</option>`));
            // A model that was removed from Image Settings still shows, so saving doesn't silently change it
            if (im.custom_model && !s.models.some(m => m.id === im.custom_model)) {
                options.push(`<option value="${escapeAttr(im.custom_model)}">${escapeHtml(im.custom_model)} (removed from Image Settings)</option>`);
            }
            $('#imageAccessModelSelect').html(options.join('')).val(im.custom_model || '');
            // Admins always have video, so their switch is on and locked
            $('#videoAccessSwitch').prop('checked', !!im.video_enabled).prop('disabled', !!u.is_admin);
            $('#videoAccessHint').text(u.is_admin
                ? 'Admins always have video.'
                : 'Off: the Video option shows "Soon" for this user. Videos are billed per second, so switch it on only for users who should have it.');
            $('#imageAccessError').addClass('d-none');
            $('#imageAccessBackdrop, #imageAccessPanel').addClass('open');
        };
        if (window._imageSettings) open(); else loadImageSettings(false).done(open);
    };

    window.closeImageAccessPanel = function () {
        $('#imageAccessBackdrop, #imageAccessPanel').removeClass('open');
    };

    $(document).on('focus input', '#imageAccessCustomInput', function () {
        $('input[name=imageAccessLimit][value=custom]').prop('checked', true);
    });

    window.submitImageAccess = function () {
        const $err = $('#imageAccessError').addClass('d-none');
        const choice = $('input[name=imageAccessLimit]:checked').val();
        let limit = null;
        if (choice === 'unlimited') limit = -1;
        if (choice === 'custom') {
            limit = Number($('#imageAccessCustomInput').val());
            if (!Number.isInteger(limit) || limit < 0 || limit > 10000) {
                return $err.text('Enter a whole number of images per day (0-10000).').removeClass('d-none');
            }
        }
        const $btn = $('#imageAccessSaveBtn').prop('disabled', true);
        $.ajax({
            url: `/api/admin/users/${window._imageAccessUserId}/image-access`,
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({
                limit: limit,
                model: $('#imageAccessModelSelect').val() || null,
                video_enabled: $('#videoAccessSwitch').prop('checked')
            }),
            success: function () {
                closeImageAccessPanel();
                showToast('Image and video access updated.', 'success');
                loadAdminUsers();
            },
            error: function (xhr) {
                $err.text((xhr.responseJSON || {}).error || 'Could not update image access.').removeClass('d-none');
            },
            complete: function () { $btn.prop('disabled', false); }
        });
    };

    $(document).on('keydown', '.admin-images-line', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.click(); }
    });

    // ── Image Settings -> Image commands (/3dbillboard, /metaad, ...) ─────
    window._adminPresets = [];
    let presetEditingId = null;

    window.loadAdminPresets = function () {
        $.getJSON('/api/admin/image-presets').done(function (r) {
            window._adminPresets = r.presets || [];
            renderAdminPresets();
        }).fail(function () {
            $('#presetAdminList').html('<div class="small text-danger">Could not load the image commands.</div>');
        });
    };

    function renderAdminPresets() {
        $('#presetAdminList').html(window._adminPresets.map(p => {
            const sizes = p.sizes.join(', ') + (p.all_sizes.length ? ` (or all ${p.all_sizes.length} sizes)` : '');
            return `
                <div class="preset-admin-row${p.enabled ? '' : ' off'}">
                    <div class="preset-admin-icon"><i class="fas ${escapeAttr(p.icon)}"></i></div>
                    <div class="preset-admin-main">
                        <div class="preset-admin-title"><code>${escapeHtml(p.command)}</code>${escapeHtml(p.label)}${p.customized.some(f => f !== 'enabled') ? '<span class="preset-admin-tag" title="Name, description, hint, prompt or logo setting changed from the built-in version">Customized</span>' : ''}</div>
                        <div class="preset-admin-desc">${escapeHtml(p.description)}</div>
                        <div class="preset-admin-meta">${escapeHtml(sizes)}${p.ad_copy ? ' · ad copy' : ''} · ${p.stamp_logo ? 'logo stamped' : 'no logo'}${p.enabled ? '' : ' · <strong>Off - hidden from users</strong>'}</div>
                    </div>
                    <label class="preset-switch" title="${p.enabled ? 'On - users see it in the / menu' : 'Off - hidden from users'}">
                        <input type="checkbox" ${p.enabled ? 'checked' : ''} data-preset-toggle="${escapeAttr(p.id)}" aria-label="${escapeAttr(p.command)} on or off"><span></span>
                    </label>
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-preset-edit="${escapeAttr(p.id)}"><i class="fas fa-pen me-1"></i>Edit</button>
                </div>`;
        }).join('') || '<div class="small text-muted">No image commands.</div>');
    }

    function replacePreset(updated) {
        window._adminPresets = window._adminPresets.map(p => p.id === updated.id ? updated : p);
        renderAdminPresets();
    }

    $(document).on('change', '[data-preset-toggle]', function () {
        const id = $(this).data('preset-toggle');
        const on = this.checked;
        $.ajax({
            url: `/api/admin/image-presets/${encodeURIComponent(id)}`, type: 'PUT', contentType: 'application/json',
            data: JSON.stringify({ enabled: on }),
            success: function (r) {
                replacePreset(r.preset);
                showToast(`/${id} is now ${on ? 'on' : 'off'} for everyone.`, on ? 'success' : 'info');
            },
            error: function (xhr) {
                showToast((xhr.responseJSON || {}).error || 'Could not change the command.', 'error');
                renderAdminPresets();  // back to the saved state
            }
        });
    });
    $(document).on('click', '[data-preset-edit]', function () { openPresetEditor($(this).data('preset-edit')); });

    function fillPresetEditor(p) {
        $('#presetEditTitle').html(`<i class="fas ${escapeAttr(p.icon)} text-primary me-2"></i>${escapeHtml(p.command)}`);
        $('#presetLabelInput').val(p.label);
        $('#presetDescInput').val(p.description);
        $('#presetHintInput').val(p.placeholder);
        $('#presetSceneInput').val(p.scene).trigger('input');
        $('#presetLogoInput').prop('checked', !!p.stamp_logo);
        $('#presetOccasionNote').toggleClass('d-none', !p.occasion);
        $('#presetAlwaysAdded').html(escapeHtml(p.always_added)
            + '<br><span class="text-muted">Plus: the product details from the photo, the brand\'s colours and style, the user\'s extra text, and the image size.</span>');
        $('#presetBuiltinScene').text(p.defaults.scene);
        $('#presetResetBtn').prop('disabled', !p.customized.length).data('armed', false)
            .html('<i class="fas fa-rotate-left me-1"></i>Reset to built-in');
        $('#presetEditError').addClass('d-none');
    }

    window.openPresetEditor = function (id) {
        const p = window._adminPresets.find(x => x.id === id);
        if (!p) return;
        presetEditingId = id;
        fillPresetEditor(p);
        $('#presetEditBackdrop, #presetEditPanel').addClass('open');
        setTimeout(() => $('#presetLabelInput').trigger('focus'), 150);
    };

    window.closePresetEditor = function () {
        $('#presetEditBackdrop, #presetEditPanel').removeClass('open');
        presetEditingId = null;
    };

    $(document).on('input', '#presetSceneInput', function () {
        const n = $(this).val().length;
        $('#presetSceneCount').text(`${n.toLocaleString()} / 3,000 characters${n < 80 ? ' - at least 80' : ''}`)
            .toggleClass('text-danger', n < 80);
    });

    window.savePresetEditor = function () {
        const id = presetEditingId;
        if (!id) return;
        const $btn = $('#presetSaveBtn').prop('disabled', true);
        $.ajax({
            url: `/api/admin/image-presets/${encodeURIComponent(id)}`, type: 'PUT', contentType: 'application/json',
            data: JSON.stringify({
                label: $('#presetLabelInput').val(), description: $('#presetDescInput').val(),
                placeholder: $('#presetHintInput').val(), scene: $('#presetSceneInput').val(),
                stamp_logo: $('#presetLogoInput').prop('checked')
            }),
            success: function (r) {
                replacePreset(r.preset);
                closePresetEditor();
                showToast(`/${id} saved - users get the new version right away.`, 'success');
            },
            error: function (xhr) {
                $('#presetEditError').text((xhr.responseJSON || {}).error || 'Could not save the command.').removeClass('d-none');
            },
            complete: function () { $btn.prop('disabled', false); }
        });
    };

    // Two clicks: the first arms the button, so a stray click can't wipe an edited prompt
    window.resetPresetEditor = function () {
        const id = presetEditingId;
        const $btn = $('#presetResetBtn');
        if (!id) return;
        if (!$btn.data('armed')) {
            $btn.data('armed', true).html('<i class="fas fa-triangle-exclamation me-1"></i>Click again to reset');
            return;
        }
        $.post(`/api/admin/image-presets/${encodeURIComponent(id)}/reset`).done(function (r) {
            replacePreset(r.preset);
            fillPresetEditor(r.preset);
            showToast(`/${id} is back to the built-in version.`, 'info');
        }).fail(function (xhr) {
            $('#presetEditError').text((xhr.responseJSON || {}).error || 'Could not reset the command.').removeClass('d-none');
        });
    };

    // ── Invitations ────────────────────────────────────────────────────
    // Admin emails someone an invitation link (/signup?invite=...). Statuses:
    // pending / accepted / expired / revoked. Resend = new link + 7 days.
    const INVITE_STATUS = {
        pending: '<span class="badge bg-warning text-dark">Pending</span>',
        accepted: '<span class="badge bg-success">Joined</span>',
        expired: '<span class="badge bg-secondary">Expired</span>',
        revoked: '<span class="badge bg-danger">Revoked</span>'
    };

    function formatInviteDate(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        return isNaN(d) ? '—' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    let defaultInviterName = '';

    // Sorting helpers, shared by both tables. Blank values always go last.
    function icStore(key, value) {
        try {
            if (value === undefined) return JSON.parse(localStorage.getItem('avir.admin.' + key) || 'null');
            localStorage.setItem('avir.admin.' + key, JSON.stringify(value));
        } catch (e) { /* private window: not remembered */ }
        return null;
    }
    const isBlank = v => v === null || v === undefined || v === '';
    function sortBy(rows, getter, dir) {
        return rows.map((row, i) => [row, getter(row), i]).sort((x, y) => {
            const bx = isBlank(x[1]), by = isBlank(y[1]);
            if (bx || by) return bx === by ? x[2] - y[2] : (bx ? 1 : -1);
            const cmp = typeof x[1] === 'number' && typeof y[1] === 'number' ? x[1] - y[1]
                : String(x[1]).localeCompare(String(y[1]), undefined, { numeric: true, sensitivity: 'base' });
            return (dir === 'asc' ? cmp : -cmp) || x[2] - y[2];
        }).map(x => x[0]);
    }
    function sortHeader(col, sort, attr) {
        const active = sort.key === col.key;
        const ariaSort = active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none';
        const icon = active ? (sort.dir === 'asc' ? 'fa-arrow-up' : 'fa-arrow-down') : 'fa-sort';
        return `<th aria-sort="${ariaSort}"><button type="button" class="ic-sort${active ? ' active' : ''}" ${attr}="${col.key}"
            title="Sort by ${escapeAttr(col.label.toLowerCase())}">${escapeHtml(col.label)}<i class="fas ${icon}" aria-hidden="true"></i></button></th>`;
    }
    function nextSort(sort, col) {
        return sort.key === col.key ? { key: col.key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key: col.key, dir: col.defaultDir || 'asc' };
    }

    // How far a person got, and what kind of email they were sent (db.STAGES, InviteMessage.style)
    const STAGES = ['added', 'preview_ready', 'invited', 'opened', 'signed_up', 'made_post'];
    const STAGE_LABELS = { added: 'Added', preview_ready: 'Preview ready', invited: 'Invited', opened: 'Opened',
                           signed_up: 'Signed up', made_post: 'Made a post' };
    const MAIL_KINDS = { preview: 'Their posts', note: 'Short note', designed: 'Designed' };
    function stageBar(stage) {
        const at = STAGES.indexOf(stage);
        const steps = STAGES.slice(2);  // invited -> made a post
        return `<span class="ic-stagebar" aria-hidden="true">${steps.map(s => {
            const i = STAGES.indexOf(s);
            return `<i class="${i <= at ? (stage === 'made_post' ? 'done' : 'on') : ''}"></i>`;
        }).join('')}</span><span class="ic-stage-label">Stage: ${STAGE_LABELS[stage] || stage}</span>`;
    }
    function mailKind(m) {
        return m.status === 'failed' ? '<span class="ic-kind failed">Failed</span>'
            : `<span class="ic-kind ${escapeAttr(m.style)}">${MAIL_KINDS[m.style] || escapeHtml(m.style)}</span>`;
    }
    function lastEmailCell(m, count) {
        if (!m) return `<td class="ic-lastmail">${icNone}</td>`;
        return `<td class="ic-lastmail"><button type="button" data-view-email="${m.id}" title="${escapeAttr(m.subject)}">${escapeHtml(m.subject || '(no subject)')}</button>`
            + `<span class="ic-sub">${mailKind(m)}${icShortDate(m.sent_at)}${count > 1 ? ` · ${count} emails` : ''}</span></td>`;
    }
    const icShortDate = iso => { const d = new Date(iso); return isNaN(d) ? '—' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };
    const icNone = '<span class="ic-none">—</span>';

    window.closeEmailView = function () { $('#emailViewBackdrop, #emailViewPanel').removeClass('open'); };
    function showEmail(m) {
        $('#emailViewBody').html(`
            <dl class="ic-email-meta">
                <dt>To</dt><dd>${escapeHtml(m.email)}</dd>
                <dt>From</dt><dd>${escapeHtml(m.sender_name || 'AVIR AI')}</dd>
                <dt>Sent</dt><dd>${formatInviteDate(m.sent_at)}${m.status === 'failed' ? ` · <span class="text-danger">not delivered: ${escapeHtml(m.error || '')}</span>` : ''}</dd>
                <dt>Kind</dt><dd>${mailKind(m)}</dd>
                <dt>Subject</dt><dd><strong>${escapeHtml(m.subject)}</strong></dd>
            </dl>
            <pre class="ic-email-body">${escapeHtml(m.body)}</pre>
            <p class="small text-muted mt-2 mb-0">The plain-text version, exactly as sent. ${m.style === 'designed' ? 'The person also saw the designed HTML version of it.' : 'The HTML version shows the same text.'}</p>`);
        $('#emailViewBackdrop, #emailViewPanel').addClass('open');
    }
    $(document).on('click', '[data-view-email]', function () {
        $.getJSON(`/api/admin/invite-messages/${$(this).data('view-email')}`).done(r => showEmail(r.message))
            .fail(() => showToast('Could not load that email.', 'error'));
    });

    // ── Sent invitations: filter by status, search, sort ──
    const SENT_FILTERS = [['all', 'All'], ['pending', 'Pending'], ['accepted', 'Joined'], ['expired', 'Expired'], ['revoked', 'Revoked']];
    const SENT_STATUS_ORDER = { pending: 0, accepted: 1, expired: 2, revoked: 3 };
    const SENT_COLUMNS = [
        { key: 'invitee', label: 'Invitee', sort: i => (i.name || i.email || '').toLowerCase() },
        { key: 'company', label: 'Company', sort: i => (i._company || '').toLowerCase() },
        { key: 'status', label: 'Status', sort: i => SENT_STATUS_ORDER[i.status] },
        { key: 'invited_by', label: 'Invited by', sort: i => (i.invited_by || '').toLowerCase() },
        { key: 'email_sent', label: 'Email sent', sort: i => ((i.last_email || {}).subject || '').toLowerCase() },
        { key: 'sent', label: 'Sent', sort: i => i.sent_at, defaultDir: 'desc' },
        { key: 'ends', label: 'Joined / Expires', sort: i => (i.status === 'accepted' ? i.accepted_at : i.expires_at), defaultDir: 'desc' },
        { key: 'actions', label: 'Actions' },
    ];
    const sent = { rows: [], filter: 'all', q: '', sort: icStore('sentSort') || { key: 'sent', dir: 'desc' } };

    function sentDate(iso) {  // date, with the time underneath
        const d = new Date(iso);
        if (!iso || isNaN(d)) return '—';
        return `<span class="text-nowrap">${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span>`
            + `<div class="ic-sub">${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</div>`;
    }

    function renderSentInvites() {
        const companies = {};
        (ic.contacts || []).forEach(c => { companies[c.email] = c.company || ''; });
        sent.rows.forEach(i => { i._company = companies[(i.email || '').toLowerCase()] || ''; });
        const counts = { all: sent.rows.length };
        sent.rows.forEach(i => { counts[i.status] = (counts[i.status] || 0) + 1; });
        $('#sentFilters').html(SENT_FILTERS.map(([f, label]) =>
            `<button type="button" class="ic-filter${sent.filter === f ? ' active' : ''}" data-sent-filter="${f}" aria-pressed="${sent.filter === f}">${label}<b>${counts[f] || 0}</b></button>`).join(''));
        $('#sentThead').html('<tr>' + SENT_COLUMNS.map(c => c.sort ? sortHeader(c, sent.sort, 'data-sent-sort') : `<th>${c.label}</th>`).join('') + '</tr>');

        const q = sent.q.toLowerCase();
        const col = SENT_COLUMNS.find(c => c.key === sent.sort.key && c.sort) || SENT_COLUMNS[5];
        const rows = sortBy(sent.rows.filter(i => (sent.filter === 'all' || i.status === sent.filter)
            && (!q || [i.email, i.name, i._company, i.invited_by, i.message, (i.last_email || {}).subject].some(v => (v || '').toLowerCase().includes(q)))), col.sort, sent.sort.dir);
        $('#sentShowing').text(sent.rows.length ? `Showing ${rows.length} of ${sent.rows.length}` : '');
        if (!sent.rows.length) {
            $('#adminInvitesTbody').html(`
                <tr><td colspan="8" class="text-center py-5 text-muted">
                    <i class="fas fa-envelope-open-text fa-2x mb-2 d-block opacity-50"></i>
                    No invitations yet - use <strong>Invite one person</strong> or pick people in the contact list.
                </td></tr>`);
            return;
        }
        if (!rows.length) {
            $('#adminInvitesTbody').html('<tr><td colspan="8" class="text-center py-4 text-muted">No invitations match these filters.</td></tr>');
            return;
        }
        $('#adminInvitesTbody').html(rows.map(i => {
            const canResend = i.status !== 'accepted';
            const canRevoke = i.status === 'pending';
            return `
                <tr>
                    <td><strong>${escapeHtml(i.email)}</strong>${i.name ? `<div class="small text-muted">${escapeHtml(i.name)}</div>` : ''}</td>
                    <td class="ic-sent-company">${i._company ? escapeHtml(i._company) : '<span class="ic-none">—</span>'}</td>
                    <td>${INVITE_STATUS[i.status] || escapeHtml(i.status)}${i.click_count && i.status === 'pending' ? `<span class="ic-sub ms-1">opened ${i.click_count}×</span>` : ''}${stageBar(i.stage)}</td>
                    <td>${escapeHtml(i.invited_by || '—')}</td>
                    ${i.last_email ? lastEmailCell(i.last_email, i.emails_sent) : `<td class="ic-msg">${i.message ? `<span title="${escapeAttr(i.message)}">${escapeHtml(i.message)}</span>` : '<span class="ic-none">—</span>'}</td>`}
                    <td>${sentDate(i.sent_at)}</td>
                    <td>${sentDate(i.status === 'accepted' ? i.accepted_at : i.expires_at)}</td>
                    <td class="text-nowrap">
                        ${canResend ? `<button type="button" class="btn-xs btn-xs-outline-neutral me-1" onclick="resendInvitation(${i.id}, this)"><i class="fas fa-paper-plane me-1"></i>Resend</button>` : ''}
                        ${canRevoke ? `<button type="button" class="btn-xs btn-xs-outline-neutral text-danger" onclick="revokeInvitation(${i.id}, '${escapeAttr(i.email)}')"><i class="fas fa-ban me-1"></i>Revoke</button>` : ''}
                    </td>
                </tr>`;
        }).join(''));
    }

    window.loadInvitations = function () {
        $.getJSON('/api/admin/invitations').done(function (r) {
            defaultInviterName = (r && r.default_inviter_name) || '';
            sent.rows = (r && r.invitations) || [];
            const pending = sent.rows.filter(i => i.status === 'pending').length;
            $('#adminInvitesBadge').text(pending).toggleClass('d-none', !pending);
            $('#icSentCount').text(sent.rows.length);
            renderSentInvites();
        }).fail(function () {
            $('#adminInvitesTbody').html('<tr><td colspan="8" class="text-center py-4 text-danger">Could not load invitations.</td></tr>');
        });
    };

    $(document).on('click', '[data-sent-filter]', function () { sent.filter = $(this).data('sent-filter'); renderSentInvites(); });
    $(document).on('click', '[data-sent-sort]', function () {
        sent.sort = nextSort(sent.sort, SENT_COLUMNS.find(c => c.key === $(this).data('sent-sort')));
        icStore('sentSort', sent.sort);
        renderSentInvites();
    });
    let sentSearchTimer = null;
    $('#sentSearch').on('input', function () {
        clearTimeout(sentSearchTimer);
        const v = this.value;
        sentSearchTimer = setTimeout(() => { sent.q = v.trim(); renderSentInvites(); }, 200);
    });

    // ── Contact list: upload people, pick them, invite them in bulk ─────────
    const IC_STATUS = {
        not_invited: ['Not invited', 'fa-circle'], invited: ['Invited', 'fa-paper-plane'], joined: ['Joined', 'fa-circle-check'],
        expired: ['Link expired', 'fa-clock'], revoked: ['Revoked', 'fa-ban'], failed: ['Email failed', 'fa-triangle-exclamation'],
        clicked: ['Opened link', 'fa-arrow-pointer'], unsubscribed: ['Unsubscribed', 'fa-bell-slash'],
    };
    const IC_FILTERS = ['all', 'not_invited', 'invited', 'clicked', 'joined', 'expired', 'failed', 'unsubscribed'];
    const IC_STATUS_ORDER = { not_invited: 0, failed: 1, invited: 2, clicked: 3, expired: 4, revoked: 5, joined: 6, unsubscribed: 7 };
    const IC_PREVIEW_ORDER = { ready: 0, running: 1, pending: 2, failed: 3 };
    function icPreviewCell(c) {
        const p = c.preview;
        if (!p) return `<td class="ic-preview-cell">${icNone}</td>`;
        if (p.status === 'ready') {
            return `<td class="ic-preview-cell"><a href="/p/${encodeURIComponent(p.token)}" target="_blank" rel="noopener" title="Open the page this person sees">`
                + `<span class="ic-prev ready"><i class="fas fa-eye"></i>Ready</span></a>`
                + `<span class="ic-sub d-block mt-1">${p.views ? `Seen ${p.views} time${p.views === 1 ? '' : 's'}` : 'Not seen yet'}</span></td>`;
        }
        if (p.status === 'failed') return `<td class="ic-preview-cell"><span class="ic-prev failed" title="${escapeAttr(p.error || '')}"><i class="fas fa-triangle-exclamation"></i>Failed</span></td>`;
        return `<td class="ic-preview-cell"><span class="ic-prev busy"><i class="fas fa-spinner fa-spin"></i>${p.status === 'running' ? 'Preparing' : 'Queued'}</span></td>`;
    }
    const IC_SIZE_WORDS = [['micro', 1], ['solo', 1], ['small', 2], ['mid', 3], ['medium', 3], ['large', 4], ['enterprise', 5]];
    function sizeRank(v) {  // "Small" < "Medium" < "Large"; "11-50" < "51-200"
        const s = String(v || '').toLowerCase();
        if (!s) return '';
        const word = IC_SIZE_WORDS.find(([w]) => s.includes(w));
        if (word) return word[1] * 1e6;
        const n = parseInt(s.replace(/[^0-9]+/g, ' ').trim(), 10);
        return isNaN(n) ? s : n;
    }
    const icText = v => (v ? escapeHtml(v) : icNone);
    const icWebsite = c => (/^https?:\/\//i.test(c.website) ? c.website : 'https://' + c.website);
    const IC_COLUMNS = [
        { key: 'person', label: 'Person', sort: c => (c.name || c.email).toLowerCase(),
          cell: c => `<td class="ic-person"><strong>${escapeHtml(c.name || c.email)}</strong>${c.name ? `<span>${escapeHtml(c.email)}</span>` : ''}`
            + `${c.job_title ? `<span class="ic-role">${escapeHtml(c.job_title)}</span>` : ''}`
            + `${c.shared_inbox ? '<span class="ic-tag" title="A shared inbox such as info@ or sales@: the invitation reaches a team, not one person">Shared inbox</span>' : ''}</td>` },
        { key: 'company', label: 'Company', sort: c => (c.company || c.website || '').toLowerCase(),
          cell: c => `<td class="ic-company">${c.website ? `<a href="${escapeAttr(icWebsite(c))}" target="_blank" rel="noopener noreferrer">${escapeHtml(c.company || c.website)}</a>` : icText(c.company)}`
            + `${c.description ? `<span class="ic-desc" title="${escapeAttr(c.description)}">${escapeHtml(c.description)}</span>` : ''}</td>` },
        { key: 'preview', label: 'Preview', optional: true, sort: c => (c.preview ? IC_PREVIEW_ORDER[c.preview.status] : ''), cell: icPreviewCell },
        { key: 'industry', label: 'Industry', optional: true, sort: c => (c.industry || '').toLowerCase(), cell: c => `<td>${icText(c.industry)}</td>` },
        { key: 'size', label: 'Size', optional: true, sort: c => sizeRank(c.company_size), cell: c => `<td class="text-nowrap">${icText(c.company_size)}</td>` },
        { key: 'location', label: 'Location', optional: true, sort: c => [c.city, c.country].filter(Boolean).join(', ').toLowerCase(),
          cell: c => `<td class="text-nowrap">${icText([c.city, c.country].filter(Boolean).join(', '))}</td>` },
        { key: 'phone', label: 'Phone', optional: true, sort: c => c.phone,
          cell: c => `<td class="text-nowrap">${c.phone ? `<a href="tel:${escapeAttr(c.phone.replace(/[^0-9+]/g, ''))}">${escapeHtml(c.phone)}</a>` : icNone}</td>` },
        { key: 'added', label: 'Added', optional: true, sort: c => c.created_at, defaultDir: 'desc',
          cell: c => `<td><span class="ic-sub d-block text-nowrap">${icShortDate(c.created_at)}</span><span class="ic-sub ic-batch" title="${escapeAttr(c.batch || 'Added by hand')}">`
            + `${c.batch ? `<i class="fas fa-file-lines me-1"></i>${escapeHtml(c.batch)}` : 'Added by hand'}</span></td>` },
        { key: 'status', label: 'Status & stage', sort: c => STAGES.indexOf(c.stage) * 10 + (IC_STATUS_ORDER[c.status] || 0),
          cell: c => `<td>${icStatusPill(c)}${stageBar(c.stage)}</td>` },
        { key: 'last_email', label: 'Last email', optional: true, sort: c => (c.last_email || {}).sent_at || '', defaultDir: 'desc',
          cell: c => lastEmailCell(c.last_email, c.emails_sent) },
        { key: 'last_invited', label: 'Last invited', optional: true, sort: c => c.last_invited_at, defaultDir: 'desc',
          cell: c => `<td><span class="ic-sub text-nowrap"${c.last_invited_at ? ` title="${escapeAttr(formatInviteDate(c.last_invited_at))}"` : ''}>${c.last_invited_at ? icShortDate(c.last_invited_at) : '—'}${c.invite_count > 1 ? `<br>${c.invite_count} times` : ''}</span></td>` },
    ];
    const IC_MORE = { industry: '', city: '', size: '', inbox: '', source: '' };
    const ic = { contacts: [], counts: {}, batches: [], filter: 'all', batch: '', q: '', selected: new Set(), expanded: new Set(),
                 more: { ...IC_MORE }, sort: icStore('icSort') || { key: 'added', dir: 'desc' }, hidden: new Set(icStore('icHiddenCols') || ['phone', 'added', 'last_invited', 'size']) };  // off until chosen (Columns menu)
    const icColumns = () => IC_COLUMNS.filter(c => !c.optional || !ic.hidden.has(c.key));

    function icStatusPill(c) {
        const [label, icon] = IC_STATUS[c.status] || [c.status, 'fa-circle'];
        const title = c.status === 'failed' && c.last_error ? ` title="${escapeAttr(c.last_error)}"` : '';
        return `<span class="ic-status ${c.status}"${title}><i class="fas ${icon}"></i>${label}</span>`;
    }

    function icVisible() {
        const q = ic.q.toLowerCase(), m = ic.more;
        const rows = ic.contacts.filter(c => (ic.filter === 'all' || c.status === ic.filter || (ic.filter === 'expired' && c.status === 'revoked'))
            && (!ic.batch || String(c.batch_id) === ic.batch)
            && (!m.industry || c.industry === m.industry) && (!m.city || c.city === m.city) && (!m.size || c.company_size === m.size)
            && (!m.inbox || (m.inbox === 'shared') === !!c.shared_inbox) && (!m.source || c.source === m.source)
            && (!q || [c.email, c.name, c.company, c.job_title, c.city, c.country, c.industry, c.phone, c.website, c.notes, c.description]
                .some(v => (v || '').toLowerCase().includes(q))));
        const col = IC_COLUMNS.find(c => c.key === ic.sort.key) || IC_COLUMNS.find(c => c.key === 'added');
        return sortBy(rows, col.sort, ic.sort.dir);
    }

    function icRenderSelection() {
        const n = ic.selected.size;
        $('#icSelection').toggleClass('d-none', !n);
        $('#icSelectionCount').text(`${n} selected`);
        const visible = icVisible();
        const allShown = visible.length && visible.every(c => ic.selected.has(c.id));
        $('#icSelectAllMatching').text(!allShown && visible.length > n ? `Select all ${visible.length} shown` : '').toggle(!allShown && visible.length > n);
        $('#icCheckAll').prop('checked', !!allShown).prop('indeterminate', !allShown && visible.some(c => ic.selected.has(c.id)));
    }

    // Lists of values present in the contacts, for the filter menus
    function icOptions(values, current, allLabel, rank) {
        const uniq = [...new Set(values.filter(Boolean))];
        if (current && !uniq.includes(current)) uniq.push(current);
        const ordered = rank ? sortBy(uniq, rank, 'asc') : uniq.sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
        return `<option value="">${allLabel}</option>` + ordered.map(v => `<option value="${escapeAttr(v)}"${v === current ? ' selected' : ''}>${escapeHtml(v)}</option>`).join('');
    }

    function icLinkify(value) {  // "https://a ; https://b" -> links, one per line
        return String(value).split(/\s*;\s*/).filter(Boolean).map(part => /^https?:\/\/\S+$/i.test(part)
            ? `<a href="${escapeAttr(part)}" target="_blank" rel="noopener noreferrer">${escapeHtml(part.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, ''))}</a>`
            : escapeHtml(part)).join('<br>');
    }

    // Stage dates + every email sent, fetched once per person when their row opens
    const icJourneys = {};
    function icJourneyHtml(id) {
        const j = icJourneys[id];
        if (!j) return '<p class="ic-sub mb-2"><i class="fas fa-spinner fa-spin me-1"></i>Loading their emails…</p>';
        const reachedAt = {};
        j.stages.forEach(s => { reachedAt[s.key] = s.at; });
        const journey = `<ol class="ic-journey" aria-label="Stage">${j.stages.map(s =>
            `<li class="${s.at ? 'reached' : ''}"><b>${STAGE_LABELS[s.key]}</b><span>${s.at ? icShortDate(s.at) : 'Not yet'}</span></li>`).join('')}</ol>`;
        const mails = j.messages.length ? `<div class="ic-mails">${j.messages.map(m => `
            <details class="ic-mail">
                <summary>${mailKind(m)}<strong>${escapeHtml(m.subject || '(no subject)')}</strong>
                    <span class="ic-sub">${formatInviteDate(m.sent_at)}${m.sender_name ? ` · from ${escapeHtml(m.sender_name)}` : ''}</span></summary>
                <pre>${escapeHtml(m.body)}${m.status === 'failed' ? `\n\nNot delivered: ${escapeHtml(m.error || '')}` : ''}</pre>
            </details>`).join('')}</div>` : '<p class="ic-sub mb-0">No emails sent to this person yet.</p>';
        return `<h6 class="ic-detail-h">Stage</h6>${journey}<h6 class="ic-detail-h">Emails sent (${j.messages.length})</h6>${mails}<hr class="my-3">`;
    }
    function icLoadJourney(id, force) {
        if (icJourneys[id] && !force) return;
        $.getJSON(`/api/admin/invite-contacts/${id}/journey`).done(j => {
            icJourneys[id] = j;
            $(`[data-journey="${id}"]`).html(icJourneyHtml(id));
        }).fail(() => $(`[data-journey="${id}"]`).html('<p class="ic-sub text-danger">Could not load their emails.</p>'));
    }

    function icDetailRow(c, span) {
        const item = (label, value, html) => (isBlank(value) ? '' : `<div class="ic-detail-item"><dt>${escapeHtml(label)}</dt><dd>${html || escapeHtml(value)}</dd></div>`);
        const contact = [
            item('Email', c.email, `<a href="mailto:${escapeAttr(c.email)}">${escapeHtml(c.email)}</a>${c.shared_inbox ? ' <span class="ic-sub">(shared inbox)</span>' : ''}`),
            item('Phone', c.phone), item('Job title', c.job_title),
            item('Website', c.website, c.website ? icLinkify(icWebsite(c)) : ''),
            item('Industry', c.industry), item('Company size', c.company_size), item('City', c.city), item('Country', c.country),
            item('About the company', c.description), item('Notes', c.notes),
        ].join('');
        const extra = Object.entries(c.extra || {}).map(([k, v]) => item(k, v, icLinkify(v))).join('');
        const linkLive = ['invited', 'clicked', 'expired'].includes(c.status);
        const p = c.preview || {};
        const invite = [
            item('Status', (IC_STATUS[c.status] || [c.status])[0]),
            item('Invitations sent', String(c.invite_count || 0)),
            item('Link clicks', c.invite_count ? String(c.clicks || 0) : '', `${c.clicks || 0}${c.first_clicked_at ? ` (first ${formatInviteDate(c.first_clicked_at)})` : ''}`),
            item('Preview', p.status, p.status === 'ready'
                ? `<a href="/p/${encodeURIComponent(p.token)}" target="_blank" rel="noopener">Open their page</a> · seen ${p.views || 0} time${p.views === 1 ? '' : 's'}${p.claimed ? ' · copied into their account' : ''}`
                : escapeHtml(p.status === 'failed' ? `Failed: ${p.error || ''}` : 'Being prepared')),
            item('Email opening line', p.first_line),
            item('Made something', c.status === 'joined' ? (c.activated ? 'Yes, created a post' : 'Not yet') : ''),
            item('Unsubscribed', c.unsubscribed_at, formatInviteDate(c.unsubscribed_at)),
            item('Last invited', c.last_invited_at, formatInviteDate(c.last_invited_at)),
            item(c.status === 'expired' ? 'Link expired' : 'Link expires', linkLive ? c.expires_at : '', formatInviteDate(c.expires_at)),
            item('Last error', c.last_error),
            item('Added', c.created_at, `${formatInviteDate(c.created_at)} ${c.batch ? `from ${escapeHtml(c.batch)}` : 'by hand'}`),
        ].join('');
        return `<tr class="ic-detail"><td colspan="${span}">
            <div class="ic-journey-wrap" data-journey="${c.id}">${icJourneyHtml(c.id)}</div>
            <div class="ic-detail-grid">
            <section><h6>Contact &amp; company</h6><dl>${contact}</dl></section>
            ${extra ? `<section><h6>From the uploaded file</h6><dl>${extra}</dl></section>` : ''}
            <section><h6>Invitation</h6><dl>${invite}</dl></section></div></td></tr>`;
    }

    function icRender() {
        $('#icContactsCount').text(ic.counts.all || 0);
        $('#icFilters').html(IC_FILTERS.map(f => {
            const count = f === 'expired' ? (ic.counts.expired || 0) + (ic.counts.revoked || 0) : (ic.counts[f] || 0);
            const label = f === 'all' ? 'All' : IC_STATUS[f][0];
            return `<button type="button" class="ic-filter${ic.filter === f ? ' active' : ''}" data-ic-filter="${f}" aria-pressed="${ic.filter === f}">${label}<b>${count}</b></button>`;
        }).join(''));
        const batchValue = ic.batch;
        $('#icBatchFilter').html('<option value="">All uploads</option>' + ic.batches.map(b =>
            `<option value="${b.id}">${escapeHtml(b.filename)} · ${formatInviteDate(b.created_at).split(',')[0]}</option>`).join('')).val(batchValue);
        $('[data-ic-more="industry"]').html(icOptions(ic.contacts.map(c => c.industry), ic.more.industry, 'All industries'));
        $('[data-ic-more="city"]').html(icOptions(ic.contacts.map(c => c.city), ic.more.city, 'All cities'));
        $('[data-ic-more="size"]').html(icOptions(ic.contacts.map(c => c.company_size), ic.more.size, 'All sizes', sizeRank));
        $('[data-ic-more]').each(function () { $(this).val(ic.more[$(this).data('ic-more')]).toggleClass('is-set', !!this.value); });
        const filtered = ic.filter !== 'all' || ic.batch || ic.q || Object.values(ic.more).some(Boolean);
        $('#icClearFilters').toggleClass('d-none', !filtered);

        const cols = icColumns();
        const span = cols.length + 2;
        $('#icThead').html(`<tr><th class="ic-check"><input type="checkbox" class="form-check-input" id="icCheckAll" aria-label="Select all shown"></th>`
            + cols.map(c => sortHeader(c, ic.sort, 'data-ic-sort')).join('')
            + '<th class="ic-more-col"><span class="visually-hidden">Details</span></th></tr>');
        const rows = icVisible();
        $('#icShowing').text(ic.contacts.length ? `Showing ${rows.length} of ${ic.contacts.length}` : '');
        if (!ic.contacts.length) {
            $('#icTbody').html(`<tr><td colspan="${span}" class="text-center py-5 text-muted">
                <i class="fas fa-address-book fa-2x mb-2 d-block opacity-50"></i>
                No contacts yet. Use <strong>Upload a list</strong> to add people from a CSV or Excel file.</td></tr>`);
        } else if (!rows.length) {
            $('#icTbody').html(`<tr><td colspan="${span}" class="text-center py-4 text-muted">Nobody matches these filters.
                <button type="button" class="ic-link ms-1" data-ic-clear>Clear filters</button></td></tr>`);
        } else {
            $('#icTbody').html(rows.map(c => {
                const open = ic.expanded.has(c.id);
                const who = escapeAttr(c.name || c.email);
                return `
                <tr class="${ic.selected.has(c.id) ? 'is-selected' : ''}${open ? ' is-open' : ''}">
                    <td class="ic-check"><input type="checkbox" class="form-check-input" data-ic-id="${c.id}" ${ic.selected.has(c.id) ? 'checked' : ''}
                        aria-label="Select ${who}"></td>
                    ${cols.map(col => col.cell(c)).join('')}
                    <td class="ic-more-col"><button type="button" class="ic-expand" data-ic-expand="${c.id}" aria-expanded="${open}"
                        title="${open ? 'Hide details' : 'All details'}" aria-label="${open ? 'Hide' : 'Show'} all details for ${who}"><i class="fas fa-chevron-${open ? 'up' : 'down'}"></i></button></td>
                </tr>${open ? icDetailRow(c, span) : ''}`;
            }).join(''));
        }
        icRenderSelection();
    }

    $('#icColMenu').html('<div class="dropdown-header">Show columns</div>' + IC_COLUMNS.filter(c => c.optional).map(c =>
        `<label class="dropdown-item ic-col-opt"><input type="checkbox" class="form-check-input me-2 mt-0" data-ic-col="${c.key}"${ic.hidden.has(c.key) ? '' : ' checked'}>${c.label}</label>`).join(''));
    $(document).on('change', '[data-ic-col]', function () {
        const key = $(this).data('ic-col');
        if (this.checked) ic.hidden.delete(key); else ic.hidden.add(key);
        icStore('icHiddenCols', [...ic.hidden]);
        icRender();
    });
    $(document).on('click', '[data-ic-sort]', function () {
        ic.sort = nextSort(ic.sort, IC_COLUMNS.find(c => c.key === $(this).data('ic-sort')));
        icStore('icSort', ic.sort);
        icRender();
    });
    $(document).on('click', '[data-ic-expand]', function () {
        const id = Number($(this).data('ic-expand'));
        if (ic.expanded.has(id)) ic.expanded.delete(id); else { ic.expanded.add(id); icLoadJourney(id, true); }
        icRender();
        $(`[data-ic-expand="${id}"]`).trigger('focus');
    });
    $(document).on('change', '[data-ic-more]', function () { ic.more[$(this).data('ic-more')] = this.value; icRender(); });
    $(document).on('click', '#icClearFilters, [data-ic-clear]', function () {
        ic.filter = 'all'; ic.batch = ''; ic.q = ''; ic.more = { ...IC_MORE };
        $('#icSearch').val('');
        icRender();
    });

    function icApply(r) {
        ic.contacts = r.contacts || [];
        ic.counts = r.counts || {};
        ic.batches = r.batches || [];
        if (r.outreach) ic.outreach = r.outreach;
        const ids = new Set(ic.contacts.map(c => c.id));
        ic.selected = new Set([...ic.selected].filter(id => ids.has(id)));
        icRenderFunnel(r.funnel);
        icRender();
        if (sent.rows.length) renderSentInvites();  // the company column comes from the contacts
        icWatchPreviews();
    }

    // Sent -> clicked -> joined -> made a post: where people drop off
    function icRenderFunnel(f) {
        if (!f || !f.contacts) { $('#icFunnel').addClass('d-none'); return; }
        const pct = n => (f.invited ? `<em>${Math.round(n / f.invited * 100)}%</em>` : '');
        const steps = [
            [f.contacts, 'Contacts', ''],
            [f.previews_ready, 'Previews ready', ''],
            [f.invited, 'Invited', ''],
            [f.clicked, 'Opened the link', pct(f.clicked)],
            [f.joined, 'Signed up', pct(f.joined)],
            [f.activated, 'Made a post', pct(f.activated)],
        ];
        $('#icFunnel').removeClass('d-none').html(steps.map(([n, label, extra]) =>
            `<div class="ic-funnel-step"><b>${n || 0} ${extra}</b><span>${label}</span></div>`).join('')
            + `<div class="ic-funnel-note">Percentages are of people invited. Clicks by company mail filters that check links aren't counted.`
            + `${f.unsubscribed ? ` ${f.unsubscribed} unsubscribed and won't be emailed again.` : ''}</div>`);
    }

    // While previews are being prepared, refresh the list until they're done
    let icPreviewTimer = null, icPreviewPolls = 0;
    function icWatchPreviews() {
        clearTimeout(icPreviewTimer);
        const busy = ic.contacts.some(c => c.preview && ['pending', 'running'].includes(c.preview.status));
        if (!busy) { icPreviewPolls = 0; return; }
        if (icPreviewPolls++ > 150) return;  // ~10 minutes
        icPreviewTimer = setTimeout(() => {
            if ($('.dropdown-menu.show').length || icSending) return icWatchPreviews();
            loadInviteContacts();
        }, 4000);
    }

    $('#icPreparePreviews').on('click', function () {
        const ids = [...ic.selected];
        if (!ids.length) return;
        const chosen = ic.contacts.filter(c => ic.selected.has(c.id));
        const ready = chosen.filter(c => c.preview && c.preview.status === 'ready').length;
        const refresh = ready > 0 && ready === chosen.length
            && window.confirm(`All ${ready} already have a preview. Make new ones? (This costs another AI call each.)`);
        if (ready === chosen.length && !refresh) return;
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/admin/invite-contacts/previews', type: 'POST', contentType: 'application/json', data: JSON.stringify({ ids: ids, refresh: refresh }) })
            .done(r => {
                showToast(r.queued ? `Preparing ${r.queued} preview${r.queued === 1 ? '' : 's'}. Each takes about a minute; the list updates as they finish.`
                    : 'They all have a preview already.', 'info');
                loadInviteContacts();
            })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not start the previews.', 'error'))
            .always(() => $btn.prop('disabled', false));
    });

    window.loadInviteContacts = function () {
        return $.getJSON('/api/admin/invite-contacts').done(icApply)
            .fail(() => $('#icTbody').html('<tr><td colspan="11" class="text-center py-4 text-danger">Could not load the contact list.</td></tr>'));
    };

    $(document).on('click', '[data-ic-view]', function () {
        const view = $(this).data('ic-view');
        $('[data-ic-view]').each(function () { $(this).toggleClass('active', $(this).data('ic-view') === view).attr('aria-selected', $(this).data('ic-view') === view); });
        $('#icContactsView').toggleClass('d-none', view !== 'contacts');
        $('#icSentView').toggleClass('d-none', view !== 'sent');
    });
    $(document).on('click', '[data-ic-filter]', function () { ic.filter = $(this).data('ic-filter'); icRender(); });
    $('#icBatchFilter').on('change', function () { ic.batch = this.value; icRender(); });
    let icSearchTimer = null;
    $('#icSearch').on('input', function () { clearTimeout(icSearchTimer); const v = this.value; icSearchTimer = setTimeout(() => { ic.q = v.trim(); icRender(); }, 200); });
    $(document).on('change', '[data-ic-id]', function () {
        const id = Number($(this).data('ic-id'));
        if (this.checked) ic.selected.add(id); else ic.selected.delete(id);
        $(this).closest('tr').toggleClass('is-selected', this.checked);
        icRenderSelection();
    });
    $(document).on('change', '#icCheckAll', function () {
        icVisible().forEach(c => { if (this.checked) ic.selected.add(c.id); else ic.selected.delete(c.id); });
        icRender();
    });
    $('#icSelectAllMatching').on('click', function () { icVisible().forEach(c => ic.selected.add(c.id)); icRender(); });
    $('#icClearSelection').on('click', function () { ic.selected.clear(); icRender(); });
    $('#icRemoveSelected').on('click', function () {
        const ids = [...ic.selected];
        if (!ids.length || !window.confirm(`Remove ${ids.length} ${ids.length === 1 ? 'person' : 'people'} from the contact list? Invitations already sent keep working.`)) return;
        $.ajax({ url: '/api/admin/invite-contacts', type: 'DELETE', contentType: 'application/json', data: JSON.stringify({ ids: ids }) })
            .done(r => { ic.selected.clear(); icApply(r); showToast(`Removed ${r.removed} from the list`, 'info'); })
            .fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not remove them.', 'error'));
    });

    // Upload a list: choose a file -> check every row -> import the ready ones
    let icPreview = null;
    window.openImportPanel = function () {
        icPreview = null;
        $('#importFileInput').val('');
        $('#importStepFile').removeClass('d-none');
        $('#importStepPreview').addClass('d-none');
        $('#importError').addClass('d-none').text('');
        $('#importConfirmBtn').prop('disabled', true).html('<i class="fas fa-check me-1"></i>Import');
        $('#importBackdrop, #importPanel').addClass('open');
    };
    window.closeImportPanel = function () { $('#importBackdrop, #importPanel').removeClass('open'); };

    const IMPORT_CHECK = { ready: ['ic-check-ok', 'Ready'], member: ['ic-check-skip', 'Already a member'],
                           duplicate: ['ic-check-skip', 'Repeated in the file'], invalid_email: ['ic-check-bad', 'Invalid email'] };

    function icUpload(file) {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        $('#importError').addClass('d-none');
        $('#importDrop strong').text('Reading the file...');
        $.ajax({ url: '/api/admin/invite-contacts/preview', type: 'POST', data: form, processData: false, contentType: false })
            .done(function (r) {
                icPreview = r;
                const sm = r.summary;
                $('#importFileName').text(r.filename);
                $('#importSummary').html(`
                    <div class="is-ready"><b>${sm.ready}</b><span>Ready to import</span></div>
                    <div><b>${sm.member}</b><span>Already members</span></div>
                    <div><b>${sm.duplicate}</b><span>Repeated</span></div>
                    <div><b>${sm.invalid_email}</b><span>Invalid email</span></div>`);
                $('#importColumns').html(`<strong>Saved as fields:</strong> ${escapeHtml(r.columns.join(', '))}.`
                    + (r.extra_columns.length ? ` <strong>Kept as extra details:</strong> ${escapeHtml(r.extra_columns.join(', '))}.` : '')
                    + ` ${sm.total} rows in the file.`
                    + (sm.shared_inbox ? ` <br><i class="fas fa-circle-info me-1"></i>${sm.shared_inbox} of the ready addresses are shared inboxes (such as info@ or sales@), so the invitation reaches a team rather than one person.` : ''));
                // Problems first, so they're seen
                const order = { invalid_email: 0, duplicate: 1, member: 2, ready: 3 };
                const shown = r.rows.slice().sort((a, b) => order[a.status] - order[b.status] || a.line - b.line).slice(0, 100);
                $('#importPreviewBody').html(shown.map(row => {
                    const [cls, label] = IMPORT_CHECK[row.status];
                    const shared = row.status === 'ready' && row.shared_inbox ? ' <span class="ic-sub">· shared inbox</span>' : '';
                    return `<tr><td>${row.line}</td><td>${escapeHtml(row.email || '(empty)').replace('@', '@<wbr>')}</td><td>${escapeHtml(row.name || '—')}</td>
                        <td>${escapeHtml(row.company || '')}</td><td>${escapeHtml(row.city || '')}</td><td class="${cls}">${label}${shared}</td></tr>`;
                }).join(''));
                $('#importPreviewMore').text(r.rows.length > 100 ? `Showing 100 of ${r.rows.length} rows.` : '');
                $('#importStepFile').addClass('d-none');
                $('#importStepPreview').removeClass('d-none');
                $('#importConfirmBtn').prop('disabled', !sm.ready).html(`<i class="fas fa-check me-1"></i>Import ${sm.ready} ${sm.ready === 1 ? 'person' : 'people'}`);
            })
            .fail(xhr => $('#importError').removeClass('d-none').text((xhr.responseJSON || {}).error || 'The file could not be read.'))
            .always(() => $('#importDrop strong').text('Drop your file here, or click to choose'));
    }

    $('#importFileInput').on('change', function () { icUpload(this.files[0]); });
    $('#importDrop').on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-over'); })
        .on('dragleave drop', function () { $(this).removeClass('is-over'); })
        .on('drop', function (e) { e.preventDefault(); icUpload(e.originalEvent.dataTransfer.files[0]); });
    $('#importChooseAgain').on('click', function () { openImportPanel(); });
    $('#importConfirmBtn').on('click', function () {
        if (!icPreview) return;
        const rows = icPreview.rows.filter(r => r.status === 'ready');
        const $btn = $(this).prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Importing...');
        $.ajax({ url: '/api/admin/invite-contacts/import', type: 'POST', contentType: 'application/json',
                 data: JSON.stringify({ filename: icPreview.filename, total: icPreview.summary.total, rows: rows }) })
            .done(function (r) {
                if ($('#importSelectAfter').is(':checked')) {
                    const emails = new Set(r.emails || []);
                    ic.selected = new Set();
                    (r.contacts || []).forEach(c => { if (emails.has(c.email) && c.status !== 'joined') ic.selected.add(c.id); });
                    ic.filter = 'all';
                    ic.batch = String(r.batch_id || '');
                }
                icApply(r);
                closeImportPanel();
                showToast(`Imported: ${r.added} new, ${r.updated} updated`, 'success');
            })
            .fail(xhr => { $('#importError').removeClass('d-none').text((xhr.responseJSON || {}).error || 'The import failed.'); $btn.prop('disabled', false).html('<i class="fas fa-check me-1"></i>Import'); });
    });

    // Send invitations to the selection, in parts, with progress
    let icSending = false;
    window.openBulkSendPanel = function () {
        const ids = [...ic.selected];
        if (!ids.length) return;
        const joined = ic.contacts.filter(c => ic.selected.has(c.id) && c.status === 'joined').length;
        const again = ic.contacts.filter(c => ic.selected.has(c.id) && ['invited', 'expired', 'revoked', 'failed'].includes(c.status)).length;
        $('#bulkSendTitle').html(`<i class="fas fa-paper-plane text-primary me-2"></i>Invite ${ids.length} ${ids.length === 1 ? 'person' : 'people'}`);
        $('#bulkSendIntro').html(`Each person gets their own sign-up link by email. It works for 30 days.`
            + (again ? ` <strong>${again}</strong> were invited before: they get a new link and the old one stops working.` : '')
            + (joined ? ` <strong>${joined}</strong> already joined and will be skipped.` : ''));
        $('#bulkFromInput').val(rememberedInviterName() || defaultInviterName);
        $('#bulkAddressInput').val((ic.outreach || {}).postal_address || '');
        const withPreview = ic.contacts.filter(c => ic.selected.has(c.id) && c.preview && c.preview.status === 'ready').length;
        $('#bulkStyleSelect').val(withPreview ? 'preview' : 'note');
        bulkSample.index = 0;
        bulkStyleChanged(true);
        $('#bulkProgress').addClass('d-none');
        $('#bulkResults').empty();
        $('#bulkSendBtn').prop('disabled', false).html(`<i class="fas fa-paper-plane me-1"></i>Send ${ids.length}`).removeClass('d-none');
        $('#bulkCancelBtn').text('Cancel');
        $('#bulkSendBackdrop, #bulkSendPanel').addClass('open');
    };
    window.closeBulkSendPanel = function () {
        if (icSending) return;  // finishes the current part first
        $('#bulkSendBackdrop, #bulkSendPanel').removeClass('open');
    };
    $('#icSendSelected').on('click', () => openBulkSendPanel());
    $('#bulkSendBtn').on('click', async function () {
        const ids = [...ic.selected];
        const inviter = ($('#bulkFromInput').val() || '').trim();
        const message = ($('#bulkMessageInput').val() || '').trim();
        const style = $('#bulkStyleSelect').val();
        const notePart = style === 'designed' ? {} : { subject: $('#bulkSubjectInput').val(), body: $('#bulkBodyInput').val() };
        const address = ($('#bulkAddressInput').val() || '').trim();
        if (!address && !window.confirm('There is no postal address. US anti-spam law requires one in this kind of email. Send anyway?')) return;
        try { localStorage.setItem('admin_inviter_name', inviter); } catch (e) { /* storage blocked */ }
        icSending = true;
        $(this).prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Sending...');
        $('#bulkCancelBtn').prop('disabled', true);
        $('#bulkProgress').removeClass('d-none');
        const results = [];
        for (let i = 0; i < ids.length; i += 25) {
            $('#bulkProgressBar').css('width', `${Math.round(i / ids.length * 100)}%`);
            $('#bulkProgressText').text(`Sending ${Math.min(i + 25, ids.length)} of ${ids.length}...`);
            try {
                const r = await $.ajax({ url: '/api/admin/invite-contacts/send', type: 'POST', contentType: 'application/json',
                                         data: JSON.stringify({ ids: ids.slice(i, i + 25), inviter_name: inviter, message: message,
                                                                style: style, postal_address: address, ...notePart }) });
                results.push(...(r.results || []));
            } catch (xhr) {
                ids.slice(i, i + 25).forEach(id => results.push({ id: id, status: 'failed', error: (xhr.responseJSON || {}).error || 'Request failed' }));
            }
        }
        icSending = false;
        $('#bulkProgressBar').css('width', '100%');
        const sent = results.filter(r => r.status === 'invited').length;
        const failed = results.filter(r => r.status === 'failed');
        const joined = results.filter(r => r.status === 'joined').length;
        const unsubscribed = results.filter(r => r.status === 'unsubscribed').length;
        if (ic.outreach) ic.outreach.postal_address = address;
        $('#bulkProgressText').text(`Done: ${sent} sent${joined ? `, ${joined} skipped (already joined)` : ''}`
            + `${unsubscribed ? `, ${unsubscribed} skipped (unsubscribed)` : ''}${failed.length ? `, ${failed.length} failed` : ''}.`);
        $('#bulkResults').html((sent ? `<div class="ic-result ok"><i class="fas fa-circle-check me-1"></i>${sent} invitation${sent === 1 ? '' : 's'} sent.</div>` : '')
            + (failed.length ? `<div class="ic-result bad"><i class="fas fa-triangle-exclamation me-1"></i>${failed.length} could not be sent:<ul>${failed.slice(0, 8).map(f =>
                `<li>${escapeHtml(f.email || 'Contact ' + f.id)}: ${escapeHtml(f.error || '')}</li>`).join('')}</ul>They are marked <strong>Email failed</strong> in the list, so you can retry them.</div>` : ''));
        $('#bulkSendBtn').addClass('d-none');
        $('#bulkCancelBtn').prop('disabled', false).text('Close');
        ic.selected = new Set(failed.map(f => f.id));  // keep only what still needs sending
        loadInviteContacts();
        loadInvitations();
    });

    // The email's text: a template per style, filled in per person (same rules
    // as services/invite_outreach.render), with a sample for the selected people
    const bulkSample = { index: 0 };
    function noteValues(c, sender) {
        const name = (c.name || '').trim(), first = name ? name.split(/\s+/)[0] : '', company = shortCompany(c.company);
        const p = c.preview || {};
        return {
            greeting: first ? `Hi ${first},` : (company ? `Hi ${company} team,` : 'Hi there,'),
            first_name: first || 'there', name: name, company: company || 'your company', city: c.city || '', industry: c.industry || '',
            first_line: p.first_line || '',
            preview_link: p.status === 'ready' ? `${location.origin}/p/${p.token}` : `${location.origin}/i/(their link)`,
            invite_link: `${location.origin}/i/(their link)`, sender_name: sender || 'The AVIR AI team',
        };
    }
    // "Ontime Packers and Movers Pvt. Ltd." -> "Ontime Packers and Movers" (services/invite_outreach.short_company)
    function shortCompany(name) {
        let s = String(name || '').trim(), prev = null;
        while (prev !== s) {
            prev = s;
            s = s.replace(/[\s,]+(pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|incorporated|corp\.?|corporation|co\.?|plc|gmbh|fzco|fze|fz-llc|l\.l\.c\.?)$/i, '').trim();
        }
        return s || String(name || '').trim();
    }
    function fillNote(template, values) {
        return String(template || '').replace(/\{([a-z_]+)\}/g, (m, key) => (key in values ? values[key] : m))
            .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    }
    function bulkStyleChanged(reset) {
        const style = $('#bulkStyleSelect').val();
        const templates = (ic.outreach || {}).templates || {};
        const note = style !== 'designed';
        $('#bulkNoteFields').toggleClass('d-none', !note);
        $('#bulkMessageGroup').toggleClass('d-none', note);
        if (note && (reset || !$('#bulkBodyInput').data('edited'))) {
            const t = templates[style] || {};
            $('#bulkSubjectInput').val(t.subject || '');
            $('#bulkBodyInput').val(t.body || '').data('edited', false);
        }
        $('#bulkMergeFields').html(((ic.outreach || {}).merge_fields || []).map(f => `<button type="button" class="ic-merge" data-merge="${f}">{${f}}</button>`).join(' '));
        const chosen = ic.contacts.filter(c => ic.selected.has(c.id));
        const missing = chosen.filter(c => !(c.preview && c.preview.status === 'ready')).length;
        $('#bulkStyleHelp').html(style === 'preview'
            ? (missing ? `<span class="text-warning-emphasis"><i class="fas fa-triangle-exclamation me-1"></i>${missing} of ${chosen.length} have no preview ready, so they get the short note instead. Use <strong>Prepare previews</strong> first to show everyone their posts.</span>`
                : 'A short plain email with a link to the posts made for them. No sign-up needed to look.')
            : style === 'note' ? 'A short plain email from you with their sign-up link. Plain emails reach the inbox more often than designed ones.'
                : 'The designed HTML invitation. Best for people who already know you.');
        renderBulkSample();
    }
    function renderBulkSample() {
        const chosen = ic.contacts.filter(c => ic.selected.has(c.id));
        if (!chosen.length || $('#bulkStyleSelect').val() === 'designed') return;
        bulkSample.index = (bulkSample.index + chosen.length) % chosen.length;
        const c = chosen[bulkSample.index];
        const style = $('#bulkStyleSelect').val();
        const usesPreview = style === 'preview' && c.preview && c.preview.status === 'ready';
        const t = ((ic.outreach || {}).templates || {});
        const subject = style === 'preview' && !usesPreview ? (t.note || {}).subject : $('#bulkSubjectInput').val();
        const body = style === 'preview' && !usesPreview ? (t.note || {}).body : $('#bulkBodyInput').val();
        const values = noteValues(c, ($('#bulkFromInput').val() || '').trim());
        $('#bulkSampleWho').text(`${c.name || c.email}${c.company ? ` (${c.company})` : ''} · ${bulkSample.index + 1} of ${chosen.length}`);
        $('#bulkSampleSubject').text(fillNote(subject, values));
        $('#bulkSampleBody').text(fillNote(body, values) + '\n\n--\nUnsubscribe link and postal address are added here.');
    }
    $('#bulkStyleSelect').on('change', () => bulkStyleChanged(true));
    $('#bulkBodyInput').on('input', function () { $(this).data('edited', true); renderBulkSample(); });
    $('#bulkSubjectInput, #bulkFromInput').on('input', renderBulkSample);
    $('#bulkSamplePrev').on('click', () => { bulkSample.index--; renderBulkSample(); });
    $('#bulkSampleNext').on('click', () => { bulkSample.index++; renderBulkSample(); });
    $(document).on('click', '[data-merge]', function () {
        const field = `{${$(this).data('merge')}}`, el = document.getElementById('bulkBodyInput');
        const at = el.selectionStart || el.value.length;
        el.value = el.value.slice(0, at) + field + el.value.slice(el.selectionEnd || at);
        el.focus();
        el.selectionStart = el.selectionEnd = at + field.length;
        $('#bulkBodyInput').data('edited', true);
        renderBulkSample();
    });

    function rememberedInviterName() {
        try { return localStorage.getItem('admin_inviter_name') || ''; } catch (e) { return ''; }
    }

    window.openInvitePanel = function () {
        $('#inviteEmailInput, #inviteNameInput, #inviteMessageInput').val('');
        // Last name used on this browser, else the admin account's name
        $('#inviteFromInput').val(rememberedInviterName() || defaultInviterName).trigger('input');
        $('#inviteFormError').addClass('d-none').text('');
        $('#inviteBackdrop, #invitePanel').addClass('open');
        setTimeout(() => $('#inviteEmailInput').trigger('focus'), 150);
    };

    window.closeInvitePanel = function () {
        $('#inviteBackdrop, #invitePanel').removeClass('open');
    };

    window.submitInvitation = function () {
        const email = ($('#inviteEmailInput').val() || '').trim();
        const $error = $('#inviteFormError');
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
            $error.removeClass('d-none').text('Enter a valid email address.');
            return;
        }
        const $btn = $('#inviteSendBtn');
        const original = $btn.html();
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Sending...');
        $error.addClass('d-none').text('');
        $.ajax({
            url: '/api/admin/invitations',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                email: email,
                name: ($('#inviteNameInput').val() || '').trim(),
                inviter_name: ($('#inviteFromInput').val() || '').trim(),
                message: ($('#inviteMessageInput').val() || '').trim()
            }),
            success: function () {
                try { localStorage.setItem('admin_inviter_name', ($('#inviteFromInput').val() || '').trim()); } catch (e) { /* storage blocked */ }
                showToast(`Invitation sent to ${escapeHtml(email)}`, 'success');
                closeInvitePanel();
                loadInvitations();
            },
            error: function (xhr) {
                const res = xhr.responseJSON || {};
                $error.removeClass('d-none').text(res.error || 'Could not send the invitation.');
                if (res.invitation) loadInvitations();  // saved even though the email failed
            },
            complete: function () { $btn.prop('disabled', false).html(original); }
        });
    };

    window.resendInvitation = function (id, btnEl) {
        const $btn = $(btnEl);
        const original = $btn.html();
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Sending...');
        $.post(`/api/admin/invitations/${id}/resend`)
            .done(function (r) {
                showToast(`Invitation re-sent to ${escapeHtml((r.invitation || {}).email || 'the user')}`, 'success');
                loadInvitations();
            })
            .fail(function (xhr) {
                showToast((xhr.responseJSON || {}).error || 'Could not resend the invitation.', 'error');
                $btn.prop('disabled', false).html(original);
            });
    };

    window.revokeInvitation = function (id, email) {
        if (!window.confirm(`Revoke the invitation for ${email}? Their link will stop working.`)) return;
        $.post(`/api/admin/invitations/${id}/revoke`)
            .done(function () { showToast('Invitation revoked', 'info'); loadInvitations(); })
            .fail(function (xhr) { showToast((xhr.responseJSON || {}).error || 'Could not revoke the invitation.', 'error'); });
    };

    $(document).on('input', '#inviteFromInput', function () {
        $('#inviteFromPreview').text(($(this).val() || '').trim() || 'The AVIR AI team');
    });

    $(document).on('keydown', '#inviteEmailInput, #inviteNameInput, #inviteFromInput', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); submitInvitation(); }
    });

    // ── Emails: scheduled-email switches, send to a group, history ───────
    const EMAIL_AUTOMATIONS = [
        { id: 'weekly_ideas', name: 'Weekly ideas', icon: 'fa-lightbulb', what: 'Three post ideas from the user\'s industry trends and upcoming festivals.' },
        { id: 'nudge_emails', name: 'Festival & trend nudges', icon: 'fa-bell', what: 'A festival a few days away, this week\'s top story, or a nudge after a quiet spell. Always shown under the bell; this switch emails them too.' },
        { id: 'monthly_recap', name: 'Monthly recap', icon: 'fa-chart-pie', what: 'Last month\'s posts per platform, tips and ideas for next month.' },
        { id: 'approval_reminders', name: 'Approval reminders', icon: 'fa-clock', what: 'Reminds a reviewer whose approval request is still waiting for a decision.' },
    ];
    const emailState = { page: 1, pageSize: 25, kinds: {}, options: null, previewOk: false, jobTimer: null };

    function fmtDateTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) + ' '
            + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    function renderEmailAutomation(r) {
        $('#emailSetupStatus').html(
            `<span class="em-pill ${r.smtp_configured ? 'ok' : 'bad'}"><i class="fas ${r.smtp_configured ? 'fa-check' : 'fa-xmark'}"></i>SMTP ${r.smtp_configured ? 'configured' : 'not configured'}</span>`
            + `<span class="em-pill ${r.base_url_set ? 'ok' : 'bad'}"><i class="fas ${r.base_url_set ? 'fa-check' : 'fa-xmark'}"></i>APP_BASE_URL ${r.base_url_set ? 'set' : 'not set'}</span>`
        );
        $('#emailAutomationList').html(EMAIL_AUTOMATIONS.map(a => {
            const st = (r.automation || {})[a.id] || {};
            const on = !!st.enabled;
            return `<div class="em-switch form-check form-switch">
                <input class="form-check-input" type="checkbox" role="switch" id="emAuto_${a.id}" data-automation="${a.id}" ${on ? 'checked' : ''}
                    aria-describedby="emAutoHelp_${a.id}">
                <div><label for="emAuto_${a.id}" class="mb-0"><strong><i class="fas ${a.icon} me-1" style="color:var(--brand-strong)"></i>${a.name}</strong></label>
                    <small id="emAutoHelp_${a.id}">${escapeHtml(a.what)}<br>${escapeHtml(((r.schedule || {})[a.id]) || '')}${st.source === 'env' ? ' &middot; set by .env' : ''}</small></div>
                <span class="em-state ${on ? 'on' : 'off'}">${on ? 'ON' : 'OFF'}</span>
            </div>`;
        }).join(''));
    }

    function loadEmailAutomation() {
        $.get('/api/admin/email-automation').done(renderEmailAutomation)
            .fail(() => $('#emailAutomationList').html('<div class="text-danger small">Could not load the email settings.</div>'));
    }

    $(document).on('change', '[data-automation]', function () {
        const $box = $(this), id = $box.data('automation'), on = $box.is(':checked');
        $box.prop('disabled', true);
        $.ajax({ url: '/api/admin/email-automation', method: 'PUT', contentType: 'application/json', data: JSON.stringify({ [id]: on }) })
            .done(function (r) {
                renderEmailAutomation(r);
                showToast(`${EMAIL_AUTOMATIONS.find(a => a.id === id).name} emails switched ${on ? 'on' : 'off'}`, 'success');
            })
            .fail(function (xhr) {
                $box.prop('checked', !on).prop('disabled', false);
                showToast((xhr.responseJSON || {}).error || 'Could not change the setting.', 'error');
            });
    });

    function campaignPayload() {
        return {
            kind: $('input[name="emCampaignKind"]:checked').val(),
            industry: $('#emIndustry').val(), account_type: $('#emAccountType').val(),
            region: $('#emRegion').val(), occasion: $('#emOccasion').val(),
        };
    }

    function resetCampaignPreview() {
        emailState.previewOk = false;
        $('#emSendBtn').prop('disabled', true);
        $('#emPreview').addClass('d-none').empty();
    }

    function loadCampaignOptions() {
        $.get('/api/admin/email-campaign/options').done(function (r) {
            emailState.options = r;
            const opt = (list) => list.map(o => `<option value="${escapeAttr(o.id)}">${escapeHtml(o.label)}</option>`).join('');
            $('#emIndustry').append(opt(r.industries));
            $('#emAccountType').append(opt(r.account_types));
            $('#emRegion').append(opt(r.regions));
            fillOccasions();
        });
    }

    // Festival emails go to the occasion's market: list only the chosen market's occasions
    const OCCASION_MARKET = { USA: 'US', India: 'India', UAE: 'UAE/GCC' };
    const MARKET_NAME = { US: 'US', India: 'India', 'UAE/GCC': 'UAE / GCC' };
    function fillOccasions() {
        const market = $('#emRegion').val();
        const list = ((emailState.options || {}).occasions || []).filter(o => !market || OCCASION_MARKET[o.region] === market);
        $('#emOccasion').html(list.length
            ? list.map(o => `<option value="${escapeAttr(o.key)}">${escapeHtml(o.name)} &middot; ${escapeHtml(o.date)} &middot; ${MARKET_NAME[OCCASION_MARKET[o.region]] || o.region} (in ${o.days_until} days)</option>`).join('')
            : `<option value="">No ${market ? MARKET_NAME[market] + ' ' : ''}occasions in the next 60 days</option>`);
    }

    $(document).on('change', 'input[name="emCampaignKind"]', function () {
        $('#emOccasionWrap').toggleClass('d-none', $(this).val() !== 'festival');
        resetCampaignPreview();
    });
    $(document).on('change', '#emIndustry, #emAccountType, #emOccasion', resetCampaignPreview);
    $(document).on('change', '#emRegion', function () { fillOccasions(); resetCampaignPreview(); });

    $('#emPreviewBtn').on('click', function () {
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/admin/email-campaign/preview', method: 'POST', contentType: 'application/json', data: JSON.stringify(campaignPayload()) })
            .done(function (r) {
                const skipped = r.skipped || {};
                if (skipped.other_market !== undefined) {
                    $('#emPreview').removeClass('d-none').html('This occasion is in a different market from the one selected - pick an occasion for that market.');
                    emailState.previewOk = false;
                    $('#emSendBtn').prop('disabled', true);
                    return;
                }
                const skipText = [
                    skipped.opted_out ? `${skipped.opted_out} unsubscribed` : '',
                    skipped.emailed_recently ? `${skipped.emailed_recently} emailed in the last 24h` : '',
                    skipped.already_had_this_occasion ? `${skipped.already_had_this_occasion} already got this occasion` : '',
                ].filter(Boolean).join(', ');
                const list = (r.sample || []).map(u => `<li>${escapeHtml(u.name || u.email)} &middot; ${escapeHtml(u.company || '')} <span class="text-muted">(${escapeHtml(u.email)})</span></li>`).join('');
                $('#emPreview').removeClass('d-none').html(
                    `<strong>${r.count} ${r.count === 1 ? 'person' : 'people'}</strong> will get this email${skipText ? ` &middot; skipped: ${escapeHtml(skipText)}` : ''}.`
                    + (list ? `<ul>${list}</ul>${r.count > (r.sample || []).length ? `<div class="mt-1 text-muted">and ${r.count - r.sample.length} more</div>` : ''}` : '')
                );
                emailState.previewOk = r.count > 0;
                $('#emSendBtn').prop('disabled', !emailState.previewOk);
            })
            .fail(function (xhr) { showToast((xhr.responseJSON || {}).error || 'Could not preview the recipients.', 'error'); })
            .always(() => $btn.prop('disabled', false));
    });

    function renderCampaignJob(job) {
        const done = job.sent + job.skipped + job.failed, pct = job.total ? Math.round(done / job.total * 100) : 100;
        $('#emJob').removeClass('d-none').html(
            `<div class="em-preview"><strong>${job.status === 'done' ? 'Finished' : 'Sending...'}</strong> ${done} of ${job.total}
             &middot; ${job.sent} sent${job.skipped ? `, ${job.skipped} skipped` : ''}${job.failed ? `, <span class="text-danger">${job.failed} failed</span>` : ''}
             <div class="em-progress"><span style="width:${pct}%"></span></div></div>`
        );
    }

    function pollCampaignJob(id) {
        clearTimeout(emailState.jobTimer);
        $.get(`/api/admin/email-campaign/jobs/${id}`).done(function (r) {
            renderCampaignJob(r.job);
            loadEmailHistory(1);
            if (r.job.status !== 'done') emailState.jobTimer = setTimeout(() => pollCampaignJob(id), 4000);
            else showToast(`Emails finished: ${r.job.sent} sent`, r.job.failed ? 'warning' : 'success');
        });
    }

    $('#emSendBtn').on('click', function () {
        if (!emailState.previewOk) return;
        const p = campaignPayload();
        const what = p.kind === 'festival' ? `the festival idea for "${$('#emOccasion option:selected').text().split(' · ')[0]}"` : '"Ideas for you"';
        if (!window.confirm(`Send ${what} to everyone in the preview now?`)) return;
        const $btn = $(this).prop('disabled', true);
        $.ajax({ url: '/api/admin/email-campaign/send', method: 'POST', contentType: 'application/json', data: JSON.stringify(p) })
            .done(function (r) { resetCampaignPreview(); renderCampaignJob(r.job); pollCampaignJob(r.job.id); })
            .fail(function (xhr) { $btn.prop('disabled', false); showToast((xhr.responseJSON || {}).error || 'Could not send.', 'error'); });
    });

    function emailFilters() {
        return {
            q: ($('#emailFilterQ').val() || '').trim(), kind: $('#emailFilterKind').val(), status: $('#emailFilterStatus').val(),
            date_from: $('#emailFilterFrom').val(), date_to: $('#emailFilterTo').val(),
        };
    }

    function emailDetailsText(d) {
        if (!d) return '';
        const parts = [];
        if (d.occasion) parts.push(`Occasion: ${d.occasion}`);
        if (d.industry) parts.push(`Industry: ${String(d.industry).replace(/_/g, ' ')}`);
        if (d.market) parts.push(`Market: ${d.market}`);
        if (d.account_type) parts.push(`Account: ${d.account_type}`);
        if (d.ideas && d.ideas.length) parts.push(`Ideas: ${d.ideas.join(' · ')}`);
        if (d.idea) parts.push(`Idea: ${d.idea}`);
        if (d.month) parts.push(`Month: ${d.month} (${d.posts} posts)`);
        return parts.join(' — ');
    }

    function renderEmailPagination(r) {
        const total = Number(r.total || 0), page = Number(r.page || 1), pages = Number(r.pages || 1), size = Number(r.page_size || emailState.pageSize);
        const from = total ? (page - 1) * size + 1 : 0, to = Math.min(total, page * size);
        $('#emailPageInfo').html(total ? `Showing <strong>${from.toLocaleString()}–${to.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong>` : '');
        if (pages <= 1) { $('#emailPages').html(''); return; }
        const btn = (label, target, opts = {}) => `<button type="button" class="cost-page-btn${opts.active ? ' active' : ''}" data-email-page="${target}"
            ${opts.disabled ? 'disabled' : ''} ${opts.active ? 'aria-current="page"' : ''} aria-label="${opts.aria || 'Page ' + target}">${label}</button>`;
        $('#emailPages').html(
            btn('<i class="fas fa-angle-left"></i>', page - 1, { disabled: page <= 1, aria: 'Previous page' })
            + costPageNumbers(page, pages).map(n => n === 'gap' ? '<span class="cost-page-gap">…</span>' : btn(n, n, { active: n === page })).join('')
            + btn('<i class="fas fa-angle-right"></i>', page + 1, { disabled: page >= pages, aria: 'Next page' })
        );
    }

    function loadEmailHistory(page) {
        if (page) emailState.page = page;
        const params = $.param(Object.assign({ page: emailState.page, page_size: emailState.pageSize }, emailFilters()));
        $('#emailHistoryTbody').addClass('loading');
        $.get(`/api/admin/emails?${params}`).done(function (r) {
            if (!Object.keys(emailState.kinds).length && r.kinds) {
                emailState.kinds = r.kinds;
                $('#emailFilterKind').append(Object.entries(r.kinds).map(([k, v]) => `<option value="${escapeAttr(k)}">${escapeHtml(v)}</option>`).join(''));
            }
            const s = r.summary || {};
            const topKinds = Object.entries(s.by_kind || {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
            $('#emailSummary').html(
                `<div class="em-stat"><b>${s.sent || 0}</b><span>Sent, last 7 days</span></div>`
                + `<div class="em-stat"><b style="color:${s.failed ? '#b91c1c' : '#172033'}">${s.failed || 0}</b><span>Failed, last 7 days</span></div>`
                + topKinds.map(([k, n]) => `<div class="em-stat"><b>${n}</b><span>${escapeHtml((r.kinds || {})[k] || k)}</span></div>`).join('')
            );
            const rows = r.emails || [];
            $('#emailHistoryTbody').html(rows.length ? rows.map(e => `<tr>
                <td style="white-space:nowrap;">${fmtDateTime(e.created_at)}</td>
                <td><div class="fw-semibold">${escapeHtml(e.user_name || '')}</div><div class="text-muted small">${escapeHtml(e.to_email)}</div></td>
                <td><span class="em-kind">${escapeHtml(e.kind_label)}</span></td>
                <td><div>${escapeHtml(e.subject)}</div>${emailDetailsText(e.details) ? `<div class="em-detail">${escapeHtml(emailDetailsText(e.details))}</div>` : ''}
                    ${e.error ? `<div class="em-error"><i class="fas fa-triangle-exclamation me-1"></i>${escapeHtml(e.error)}</div>` : ''}</td>
                <td><span class="em-badge ${e.status === 'sent' ? 'sent' : 'failed'}">${e.status === 'sent' ? 'Sent' : 'Failed'}</span></td>
                <td class="text-muted small">${e.triggered_by && e.triggered_by.startsWith('admin') ? 'Admin' : 'Automatic'}</td>
            </tr>`).join('') : '<tr><td colspan="6" class="text-center py-4 text-muted">No emails match these filters yet.</td></tr>');
            renderEmailPagination(r);
        }).fail(function () {
            $('#emailHistoryTbody').html('<tr><td colspan="6" class="text-center py-4 text-danger">Could not load the email history.</td></tr>');
        }).always(() => $('#emailHistoryTbody').removeClass('loading'));
    }

    let emailSearchTimer = null;
    $('#emailFilterQ').on('input', function () { clearTimeout(emailSearchTimer); emailSearchTimer = setTimeout(() => loadEmailHistory(1), 350); });
    $('#emailFilterKind, #emailFilterStatus, #emailFilterFrom, #emailFilterTo').on('change', () => loadEmailHistory(1));
    $('#emailPageSize').on('change', function () { emailState.pageSize = Number($(this).val()) || 25; loadEmailHistory(1); });
    $(document).on('click', '[data-email-page]', function () { loadEmailHistory(Number($(this).data('email-page'))); });
    $('#emailFilterReset').on('click', function () {
        $('#emailFilterQ, #emailFilterFrom, #emailFilterTo').val('');
        $('#emailFilterKind, #emailFilterStatus').val('');
        loadEmailHistory(1);
    });

    // ── Init ──
    loadAdminUsers();
    loadAdminRequests();
    loadAdminCostHistory();
    loadInvitations();
    loadInviteContacts();
    loadImageSettings(true);
    loadAdminPresets();
    loadEmailAutomation();
    loadCampaignOptions();
    loadEmailHistory(1);
});
