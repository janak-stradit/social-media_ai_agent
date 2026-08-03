$(document.body).ready(function () {
    let currentSocialAccounts = [];

    // Load current user info
    $.ajax({
        url: '/api/auth/me',
        type: 'GET',
        success: function (r) {
            if (r.user) {
                $('#headerUserLabel').text(r.user.name);
                $('#headerUserEmail').text(r.user.email);
                $('#headerUserAvatar').text(r.user.name.charAt(0).toUpperCase());
            }
        }
    });

    // Load social accounts and scheduled posts on page load
    loadUserSocialAccounts();
    loadScheduledPosts();

    function loadUserSocialAccounts() {
        $.ajax({
            url: '/api/social/accounts',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                currentSocialAccounts = r.accounts || [];

                // Reset UI status badges
                $('#fbStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#igStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#liStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');

                $('#fbAccountName').text('—'); $('#fbAccountId').text('—');
                $('#igAccountName').text('—'); $('#igAccountId').text('—');
                $('#liAccountName').text('—'); $('#liAccountId').text('—');

                currentSocialAccounts.forEach(acc => {
                    if (acc.status === 'connected') {
                        if (acc.platform === 'facebook') {
                            $('#fbStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#fbAccountName').text(acc.account_name || 'Facebook Page');
                            $('#fbAccountId').text(acc.account_id || 'N/A');
                        } else if (acc.platform === 'instagram') {
                            $('#igStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#igAccountName').text(acc.account_name || 'Instagram Account');
                            $('#igAccountId').text(acc.account_id || 'N/A');
                        } else if (acc.platform === 'linkedin') {
                            $('#liStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#liAccountName').text(acc.account_name || 'LinkedIn Account');
                            $('#liAccountId').text(acc.account_id || 'N/A');
                        }
                    }
                });
            }
        });
    }

    window.openConnectModal = function (platform) {
        const titles = {
            facebook: 'Connect Facebook Page',
            instagram: 'Connect Instagram Business',
            linkedin: 'Connect LinkedIn Account'
        };
        const icons = {
            facebook: '<i class="fab fa-facebook color-fb"></i>',
            instagram: '<i class="fab fa-instagram color-ig"></i>',
            linkedin: '<i class="fab fa-linkedin color-li"></i>'
        };

        $('#connectPlatformInput').val(platform);
        $('#connectModalTitle').text(titles[platform] || 'Connect Account');
        $('#connectModalIcon').html(icons[platform] || '<i class="fas fa-plug"></i>');

        const existing = currentSocialAccounts.find(a => a.platform === platform) || {};
        $('#connectAccountNameInput').val(existing.account_name || '');
        $('#connectAccountIdInput').val(existing.account_id || '');
        $('#connectAccessTokenInput').val(existing.access_token || '');

        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('connectAccountModal'));
        modal.show();
    };

    $('#saveConnectAccountBtn').on('click', function () {
        const platform = $('#connectPlatformInput').val();
        const accountName = $('#connectAccountNameInput').val().trim();
        const accountId = $('#connectAccountIdInput').val().trim();
        const accessToken = $('#connectAccessTokenInput').val().trim();

        if (!accountName) {
            showToast('Please enter an Account Name or Handle', 'error');
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
                access_token: accessToken
            }),
            success: function (r) {
                if (r.success) {
                    showToast(`${platform.toUpperCase()} account connected successfully!`, 'success');
                    bootstrap.Modal.getInstance(document.getElementById('connectAccountModal')).hide();
                    loadUserSocialAccounts();
                }
            },
            error: function (xhr) {
                showToast('Failed to connect: ' + (xhr.responseJSON?.error || 'Error'), 'error');
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
                            <td colspan="6" class="text-center py-4 text-slate-400">No scheduled posts found. Use "Schedule Campaign Post" on any generated run in the Studio.</td>
                        </tr>
                    `);
                    return;
                }

                let html = '';
                posts.forEach(p => {
                    const platforms = Array.isArray(p.platforms) ? p.platforms.map(pl => {
                        const icons = { facebook: '<i class="fab fa-facebook color-fb me-1"></i>', instagram: '<i class="fab fa-instagram color-ig me-1"></i>', linkedin: '<i class="fab fa-linkedin color-li me-1"></i>' };
                        return `${icons[pl] || ''}${pl.toUpperCase()}`;
                    }).join(' ') : p.platforms;

                    const statusBadges = {
                        pending: '<span class="badge bg-warning text-dark"><i class="fas fa-clock me-1"></i>Pending</span>',
                        published: '<span class="badge bg-success"><i class="fas fa-check-circle me-1"></i>Published</span>',
                        failed: '<span class="badge bg-danger"><i class="fas fa-triangle-exclamation me-1"></i>Failed</span>',
                        cancelled: '<span class="badge bg-secondary"><i class="fas fa-ban me-1"></i>Cancelled</span>'
                    };

                    const cancelBtn = p.status === 'pending'
                        ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelScheduledPostItem(${p.id})"><i class="fas fa-ban me-1"></i>Cancel</button>`
                        : '—';

                    html += `
                        <tr>
                            <td class="font-monospace">#${p.id}</td>
                            <td style="max-width: 280px;" class="text-truncate" title="${escapeAttr(p.story)}">${escapeHtml(p.story)}</td>
                            <td>${platforms}</td>
                            <td class="font-monospace text-navy font-semibold">${p.scheduled_at}</td>
                            <td>${statusBadges[p.status] || p.status}</td>
                            <td class="text-end">${cancelBtn}</td>
                        </tr>
                    `;
                });
                $('#scheduledPostsTbody').html(html);
            }
        });
    }

    window.cancelScheduledPostItem = function (postId) {
        if (!confirm('Are you sure you want to cancel this scheduled post?')) return;
        $.ajax({
            url: `/api/social/scheduled/${postId}/cancel`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Scheduled post cancelled', 'info');
                    loadScheduledPosts();
                }
            },
            error: function () {
                showToast('Failed to cancel scheduled post', 'error');
            }
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

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
});
