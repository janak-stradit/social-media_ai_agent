$(document).ready(function () {
    let currentSocialAccounts = [];

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

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

                // Reset UI status badges
                $('#fbStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#igStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#liStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#ytStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');

                $('#fbAccountName').text('—'); $('#fbAccountId').text('—');
                $('#igAccountName').text('—'); $('#igAccountId').text('—');
                $('#liAccountName').text('—'); $('#liAccountId').text('—');
                $('#ytAccountName').text('—'); $('#ytAccountId').text('—');

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
                            if (acc.connection_type === 'mcp') {
                                $('#liStatusBadge').removeClass('badge-disconnected').addClass('badge-connected bg-purple text-white').html('<i class="fas fa-network-wired me-1"></i>MCP Connected');
                                $('#liAccountName').text((acc.account_name || 'LinkedIn Account') + ' (MCP)');
                                $('#liAccountId').text(acc.mcp_endpoint || acc.account_id || 'N/A');
                            } else {
                                $('#liStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                                $('#liAccountName').text(acc.account_name || 'LinkedIn Account');
                                $('#liAccountId').text(acc.account_id || 'N/A');
                            }
                        } else if (acc.platform === 'youtube') {
                            $('#ytStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#ytAccountName').text(acc.account_name || 'YouTube Account');
                            $('#ytAccountId').text(acc.account_id || 'N/A');
                        }
                    }
                });
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

        if (!accountName) {
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
                            <td colspan="6" class="text-center py-4 text-slate-400">No scheduled posts found. Click "+ Create Manual Post" above to schedule a post manually!</td>
                        </tr>
                    `);
                    return;
                }

                let html = '';
                posts.forEach(p => {
                    const platforms = Array.isArray(p.platforms) ? p.platforms.map(pl => {
                        const icons = { facebook: '<i class="fab fa-facebook color-fb me-1"></i>', instagram: '<i class="fab fa-instagram color-ig me-1"></i>', linkedin: '<i class="fab fa-linkedin color-li me-1"></i>' };
                        return `<span class="badge bg-light text-navy border me-1">${icons[pl] || ''}${pl.toUpperCase()}</span>`;
                    }).join('') : p.platforms;

                    let cJson = {};
                    if (typeof p.content_json === 'string') {
                        try { cJson = JSON.parse(p.content_json); } catch (e) {}
                    } else if (typeof p.content_json === 'object') {
                        cJson = p.content_json || {};
                    }

                    const mediaUrl = cJson.image_url || cJson.media_url || p.media_url;
                    const mediaTag = mediaUrl ? `<img src="${mediaUrl}" class="rounded-2 me-2 border shadow-xs" style="width: 38px; height: 38px; object-fit: cover;">` : '<div class="rounded-2 me-2 bg-light d-inline-flex align-items-center justify-content-center border" style="width: 38px; height: 38px;"><i class="fas fa-file-text text-slate-400"></i></div>';

                    const storyText = cJson.caption || cJson.story || p.story || 'Custom Manual Post';
                    const manualBadge = cJson.is_manual ? '<span class="badge bg-info text-dark ms-1">Manual</span>' : '';

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
                            <td style="max-width: 300px;">
                                <div class="d-flex align-items-center">
                                    ${mediaTag}
                                    <div class="text-truncate">
                                        <span class="fw-semibold text-slate-800" title="${escapeHtml(storyText)}">${escapeHtml(storyText)}</span>
                                        ${manualBadge}
                                    </div>
                                </div>
                            </td>
                            <td>${platforms}</td>
                            <td class="font-monospace text-navy font-semibold">${p.scheduled_at}</td>
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
        formData.append('scheduled_at', scheduledAt);
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

    // ── AI Models & Purpose Inspector Handler ─────────────────────────────
    $('#headerModelInfoBtn').on('click', function () {
        openModelArchitectureModal();
    });

    function openModelArchitectureModal() {
        const modalElem = document.getElementById('modelArchitectureModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
            loadModelsInfoData();
        }
    }

    function loadModelsInfoData() {
        $.ajax({
            url: '/api/models/info',
            type: 'GET',
            success: function (r) {
                if (!r.success || !r.models) return;

                const summary = r.summary || {};
                const categoryIcons = {
                    llm: { icon: 'fa-brain', class: 'cat-llm' },
                    vision: { icon: 'fa-eye', class: 'cat-vision' },
                    image: { icon: 'fa-image', class: 'cat-image' },
                    video: { icon: 'fa-video', class: 'cat-video' },
                    memory: { icon: 'fa-database', class: 'cat-memory' }
                };

                let html = `
                    <div class="col-12 mb-2">
                        <div class="runtime-metrics-bar p-3 bg-slate-50 border rounded-3 d-flex flex-wrap align-items-center justify-content-between gap-3">
                            <div class="d-flex align-items-center gap-2">
                                <span class="badge bg-success-subtle text-success border border-success-subtle px-2.5 py-1.5 font-bold fs-8">
                                    <i class="fas fa-circle-check me-1"></i>Runtime Active
                                </span>
                                ${summary.use_mock_llm ? '<span class="badge bg-purple text-white px-2.5 py-1.5 font-bold fs-8"><i class="fas fa-vial me-1"></i>MOCK LLM MODE (OFFLINE & FREE)</span>' : ''}
                                <span class="text-slate-600 font-semibold fs-7">
                                    AWS Region: <strong class="text-navy">${escapeHtml(summary.aws_region || 'us-east-1')}</strong>
                                </span>
                            </div>
                            <div class="d-flex gap-4 fs-7">
                                <div><span class="text-slate-500">User Runs:</span> <strong class="text-navy">${summary.total_user_runs || 0}</strong></div>
                                <div><span class="text-slate-500">Tokens:</span> <strong class="text-navy font-monospace">${Number(summary.total_tokens_used || 0).toLocaleString()}</strong></div>
                                <div><span class="text-slate-500">Cost USD:</span> <strong class="text-emerald font-monospace">$${Number(summary.total_cost_usd || 0).toFixed(4)}</strong></div>
                            </div>
                        </div>
                    </div>
                `;

                r.models.forEach(m => {
                    const iconMeta = categoryIcons[m.category] || { icon: 'fa-microchip', class: 'cat-llm' };
                    const agentTags = (m.agents || []).map(a => `<span class="agent-pill-tag"><i class="fas fa-robot me-1"></i>${a}</span>`).join(' ');

                    const statusBadges = {
                        ACTIVE: '<span class="badge bg-success text-white"><i class="fas fa-circle me-1 fs-9"></i>ACTIVE</span>',
                        ONLINE: '<span class="badge bg-primary text-white"><i class="fas fa-signal me-1 fs-9"></i>ONLINE</span>',
                        STANDBY: '<span class="badge bg-secondary text-white"><i class="fas fa-pause me-1 fs-9"></i>STANDBY</span>'
                    };

                    html += `
                        <div class="col-md-6 col-lg-4">
                            <div class="model-purpose-card">
                                <div class="d-flex align-items-center justify-content-between mb-3">
                                    <div class="d-flex align-items-center gap-3">
                                        <div class="cat-icon-box ${iconMeta.class}">
                                            <i class="fas ${iconMeta.icon}"></i>
                                        </div>
                                        <div>
                                            <h6 class="font-bold text-navy mb-0">${escapeHtml(m.purpose)}</h6>
                                            <small class="text-primary font-semibold">${escapeHtml(m.provider)}</small>
                                        </div>
                                    </div>
                                    ${statusBadges[m.status] || '<span class="badge bg-info text-white">ONLINE</span>'}
                                </div>
                                <div class="mb-3">
                                    <span class="small text-slate-500 d-block mb-1">Active Model Slug:</span>
                                    <span class="slug-badge"><i class="fas fa-code-branch me-1 text-slate-400"></i>${escapeHtml(m.model_name)}</span>
                                </div>
                                <p class="small text-slate-600 mb-3 flex-grow-1" style="font-size: 12px; line-height: 1.5;">${escapeHtml(m.description)}</p>
                                <div class="pt-2 border-top">
                                    <small class="text-slate-500 font-semibold d-block mb-2">Assigned Agents &amp; Services:</small>
                                    <div class="d-flex gap-1 flex-wrap">
                                        ${agentTags}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                });

                $('#modelsInfoCardsContainer').html(html);
            },
            error: function () {
                $('#modelsInfoCardsContainer').html(`
                    <div class="col-12 text-center py-5 text-danger">
                        <i class="fas fa-triangle-exclamation fa-2x mb-2"></i>
                        <p>Failed to load dynamic AI Model architecture information.</p>
                    </div>
                `);
            }
        });
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
