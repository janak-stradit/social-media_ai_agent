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

    function renderAdminUsersTable(users) {
        let html = '';
        if (!users.length) {
            html = '<tr><td colspan="10" class="text-center py-4 text-muted">No registered users found.</td></tr>';
        } else {
            users.forEach(u => {
                const roleBadge = u.is_admin ? '<span class="badge bg-purple">Admin</span>' : '<span class="badge bg-secondary">User</span>';
                const statusBadge = u.remaining_credits > 0 ? '<span class="badge bg-success">Credits OK</span>' : '<span class="badge bg-danger">Exhausted</span>';
                const pendingBadge = u.has_pending_request ? '<span class="badge bg-warning text-dark ms-1">Req Pending</span>' : '';

                // Account column: onboarding state + active/deactivated toggle.
                // Onboarding fields may be absent on rows loaded before this
                // feature existed - default to "onboarded/active" so old data
                // doesn't look broken.
                const isActive = u.is_active !== false;
                const activeBadge = isActive
                    ? '<span class="badge bg-success">Active</span>'
                    : '<span class="badge bg-danger">Deactivated</span>';
                const toggleBtn = isActive
                    ? `<button type="button" class="btn-xs btn-xs-outline-danger" onclick="adminSetUserActive(${u.id}, false)">Deactivate</button>`
                    : `<button type="button" class="btn-xs btn-xs-outline-success" onclick="adminSetUserActive(${u.id}, true)">Activate</button>`;

                html += `
                    <tr>
                        <td>${u.id}</td>
                        <td><strong>${escapeHtml(u.name)}</strong></td>
                        <td>${escapeHtml(u.email)}</td>
                        <td>${roleBadge}</td>
                        <td><span class="font-monospace text-dark">$${Number(u.credit_limit).toFixed(2)}</span></td>
                        <td><span class="font-monospace text-warning">$${Number(u.used_credits).toFixed(4)}</span></td>
                        <td><span class="font-monospace text-success">$${Number(u.remaining_credits).toFixed(4)}</span></td>
                        <td>${statusBadge} ${pendingBadge}</td>
                        <td>
                            <div class="admin-cell-stack">
                                ${accountTypeBadge(u)}
                                <div class="d-flex align-items-center gap-2">
                                    ${activeBadge}
                                    ${toggleBtn}
                                </div>
                            </div>
                        </td>
                        <td>
                            <div class="admin-actions-row">
                                <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 10)">+$10</button>
                                <button type="button" class="btn-xs btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 50)">+$50</button>
                                <button type="button" class="btn-xs btn-xs-outline-info" onclick="adminSetCustomCredit(${u.id}, ${u.credit_limit})">Set Limit</button>
                                <button type="button" class="btn-xs btn-xs-outline-neutral" onclick="adminEditUserProfile(${u.id})">Edit Profile</button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        }
        $('#adminUsersTbody').html(html);
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

                $('#adminTotalSystemCost').text('$' + Number(summary.total_system_cost_usd || 0).toFixed(4));
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
    loadAdminUsers();
    loadAdminRequests();
    loadAdminCostHistory();
});
