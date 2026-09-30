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

    function renderAdminUsersTable(users) {
        if (!users.length) {
            $('#adminUsersTbody').html('<tr><td colspan="5" class="text-center py-5 text-muted">No users match these filters.</td></tr>');
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
                    <td>
                        <div class="admin-credit">
                            <div class="admin-credit-top"><span><strong>${money(used)}</strong> of ${money(limit)}</span><span>${Math.round(pct)}%</span></div>
                            <div class="admin-credit-bar" title="${Math.round(pct)}% of the credit limit used"><span class="${barClass}" style="width:${pct}%"></span></div>
                            <div class="admin-credit-note">${note}</div>
                        </div>
                    </td>
                    <td><span class="admin-status${isActive ? '' : ' off'}">${isActive ? 'Active' : 'Deactivated'}</span></td>
                    <td>
                        <div class="admin-row-actions">
                            <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 10)" title="Add $10 to the credit limit">+$10</button>
                            <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 50)" title="Add $50 to the credit limit">+$50</button>
                            <div class="dropdown">
                                <button type="button" class="btn-xs btn-xs-outline-neutral admin-kebab" id="${menuId}" data-bs-toggle="dropdown"
                                        data-bs-popper-config='{"strategy":"fixed"}' aria-expanded="false" aria-label="More actions for ${escapeAttr(u.name)}">
                                    <i class="fas fa-ellipsis"></i>
                                </button>
                                <ul class="dropdown-menu dropdown-menu-end admin-menu" aria-labelledby="${menuId}">
                                    <li><button type="button" class="dropdown-item" onclick="adminSetCustomCredit(${u.id}, ${limit})"><i class="fas fa-sliders"></i>Set credit limit</button></li>
                                    <li><button type="button" class="dropdown-item" onclick="adminEditUserProfile(${u.id})"><i class="fas fa-user-pen"></i>Edit profile</button></li>
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
    function loadAdminCostHistory() {
        $.ajax({
            url: '/api/admin/cost-history?limit=100',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const history = r.history || [];
                const summary = r.summary || {};

                const totalCost = Number(summary.total_system_cost_usd || 0);
                const totalRuns = Number(summary.total_runs || history.length || 0);
                $('#adminTotalSystemCost').text(money(totalCost));
                $('#adminTotalRuns').text(totalRuns.toLocaleString());
                $('#adminAvgRunCost').text(totalRuns ? '$' + (totalCost / totalRuns).toFixed(3) : '$0.00');
                $('#adminTotalSystemTokens').text(Number(summary.total_tokens || 0).toLocaleString());

                let html = '';
                if (!history.length) {
                    html = '<tr><td colspan="7" class="text-center py-4 text-muted">No generation history recorded.</td></tr>';
                } else {
                    history.forEach(h => {
                        html += `
                            <tr>
                                <td>#${h.id}</td>
                                <td>${escapeHtml(h.user_email)}</td>
                                <td>${h.timestamp}</td>
                                <td style="max-width: 280px;" class="text-truncate" title="${escapeAttr(h.story)}">${escapeHtml(h.story)}</td>
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

    // ── Init ──────────────────────────────────────────────────────────────

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
});
