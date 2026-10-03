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
    function brandStatusCell(b) {
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
                return cell('<span class="admin-brand-badge fail"><i class="fas fa-circle-exclamation"></i>Scan failed</span>',
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
                    <td>${brandStatusCell(u.brand)}</td>
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
                                    <li><button type="button" class="dropdown-item" onclick="openImageAccessPanel(${u.id})"><i class="fas fa-image"></i>Image access…</button></li>
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
                    showToast('Request approved! User credit limit increased.', 'success');
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
                    showToast('Request rejected.', 'info');
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
        const title = `Images today: ${im.unlimited ? `${im.used_today} (unlimited)` : `${im.used_today} of ${im.limit}`}`
            + ` · model ${im.model}. Click to change.`;
        return `<div><span class="admin-images-line${full ? ' full' : ''}" role="button" tabindex="0" title="${escapeAttr(title)}"
                    onclick="openImageAccessPanel(${u.id})"><i class="fas fa-image"></i>${count}${custom}</span></div>`;
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
            data: JSON.stringify({ limit: limit, model: $('#imageAccessModelSelect').val() || null }),
            success: function () {
                closeImageAccessPanel();
                showToast('Image access updated.', 'success');
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

    window.loadInvitations = function () {
        $.getJSON('/api/admin/invitations').done(function (r) {
            defaultInviterName = (r && r.default_inviter_name) || '';
            const rows = (r && r.invitations) || [];
            const pending = rows.filter(i => i.status === 'pending').length;
            $('#adminInvitesBadge').text(pending).toggleClass('d-none', !pending);
            if (!rows.length) {
                $('#adminInvitesTbody').html(`
                    <tr><td colspan="6" class="text-center py-5 text-muted">
                        <i class="fas fa-envelope-open-text fa-2x mb-2 d-block opacity-50"></i>
                        No invitations yet - use <strong>Invite user</strong> to send the first one.
                    </td></tr>`);
                return;
            }
            $('#adminInvitesTbody').html(rows.map(i => {
                const canResend = i.status !== 'accepted';
                const canRevoke = i.status === 'pending';
                return `
                    <tr>
                        <td><strong>${escapeHtml(i.email)}</strong>${i.name ? `<div class="small text-muted">${escapeHtml(i.name)}</div>` : ''}</td>
                        <td>${INVITE_STATUS[i.status] || escapeHtml(i.status)}</td>
                        <td>${escapeHtml(i.invited_by || '—')}</td>
                        <td>${formatInviteDate(i.sent_at)}</td>
                        <td>${i.status === 'accepted' ? formatInviteDate(i.accepted_at) : formatInviteDate(i.expires_at)}</td>
                        <td class="text-nowrap">
                            ${canResend ? `<button type="button" class="btn-xs btn-xs-outline-neutral me-1" onclick="resendInvitation(${i.id}, this)"><i class="fas fa-paper-plane me-1"></i>Resend</button>` : ''}
                            ${canRevoke ? `<button type="button" class="btn-xs btn-xs-outline-neutral text-danger" onclick="revokeInvitation(${i.id}, '${escapeAttr(i.email)}')"><i class="fas fa-ban me-1"></i>Revoke</button>` : ''}
                        </td>
                    </tr>`;
            }).join(''));
        }).fail(function () {
            $('#adminInvitesTbody').html('<tr><td colspan="6" class="text-center py-4 text-danger">Could not load invitations.</td></tr>');
        });
    };

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

    // ── Init ──
    loadAdminUsers();
    loadAdminRequests();
    loadAdminCostHistory();
    loadInvitations();
    loadImageSettings(true);
    loadAdminPresets();
});
