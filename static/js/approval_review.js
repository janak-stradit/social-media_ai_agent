// Standalone Content Approval page (opened from the "Review & Decide" link in
// the approval-request email). Self-contained - does not load dashboard.js,
// since that file initializes the whole Analysis Dashboard (posts, festive
// storylines, etc.) which isn't relevant here and would fire a lot of
// unnecessary requests for a page a reviewer opens once from email.
$(document).ready(function () {

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function platformDisplayName(p) {
        const names = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', twitter: 'Twitter / X', blog: 'Blog', youtube: 'YouTube' };
        return names[p] || (p ? p.charAt(0).toUpperCase() + p.slice(1) : 'Social');
    }

    function styleHashtagsHtml(text) {
        return escapeHtml(text || '')
            .replace(/#(\w+)/g, '<span style="color:#0a66c2;font-weight:600;">#$1</span>')
            .replace(/\n/g, '<br>');
    }

    function previewMediaBlockHtml(item, squareAspect) {
        if (!item || !item.content) return '';
        if (item.type === 'video') {
            return `<video controls class="d-block w-100" style="${squareAspect ? 'aspect-ratio: 1/1; object-fit: cover;' : 'max-height: 420px; object-fit: cover;'} background:#000;"><source src="${item.content}" type="video/mp4"></video>`;
        }
        return `<img src="${item.content}" class="d-block w-100" style="${squareAspect ? 'aspect-ratio: 1/1; object-fit: cover;' : 'max-height: 420px; object-fit: cover;'} background:#f3f4f6;">`;
    }

    function buildLinkedInPreviewHtml(item) {
        return `
            <div style="background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
                <div style="display:flex; align-items:flex-start; gap:10px; padding:12px 14px 8px;">
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#0a66c2,#004182);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">SI</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#000;">StradIT</div>
                        <div style="font-size:0.75rem; color:#666;">12,483 followers</div>
                        <div style="font-size:0.72rem; color:#666;">Just now &middot; <i class="fas fa-earth-americas"></i></div>
                    </div>
                    <i class="fas fa-ellipsis" style="color:#666;"></i>
                </div>
                <div style="padding:0 14px 12px; font-size:0.85rem; color:#000; line-height:1.45;">${styleHashtagsHtml(item.caption || item.content)}</div>
                ${previewMediaBlockHtml(item, false)}
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 14px; font-size:0.72rem; color:#666; border-bottom:1px solid #eee;">
                    <span><i class="fas fa-thumbs-up" style="color:#0a66c2;"></i> 128</span>
                    <span>24 comments &middot; 6 reposts</span>
                </div>
                <div style="display:flex; padding:2px 4px;">
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#666;font-weight:600;font-size:0.78rem;"><i class="far fa-thumbs-up me-1"></i>Like</div>
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#666;font-weight:600;font-size:0.78rem;"><i class="far fa-comment me-1"></i>Comment</div>
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#666;font-weight:600;font-size:0.78rem;"><i class="fas fa-retweet me-1"></i>Repost</div>
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#666;font-weight:600;font-size:0.78rem;"><i class="far fa-paper-plane me-1"></i>Send</div>
                </div>
            </div>
        `;
    }

    function buildInstagramPreviewHtml(item) {
        return `
            <div style="background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
                <div style="display:flex; align-items:center; gap:10px; padding:10px 12px;">
                    <div style="width:34px;height:34px;border-radius:50%;background:linear-gradient(45deg,#f58529,#dd2a7b,#8134af,#515bd4);padding:2px;flex-shrink:0;">
                        <div style="width:100%;height:100%;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.65rem;">SI</div>
                    </div>
                    <div style="font-weight:600; font-size:0.85rem;">stradit_official</div>
                    <i class="fas fa-ellipsis ms-auto" style="color:#000;"></i>
                </div>
                ${previewMediaBlockHtml(item, true)}
                <div style="padding:10px 12px 4px; display:flex; gap:14px; font-size:1.25rem; color:#000;">
                    <i class="far fa-heart"></i><i class="far fa-comment"></i><i class="far fa-paper-plane"></i>
                    <i class="far fa-bookmark ms-auto"></i>
                </div>
                <div style="padding:4px 12px 2px; font-size:0.78rem; font-weight:600;">248 likes</div>
                <div style="padding:0 12px 12px; font-size:0.82rem; line-height:1.4;"><strong>stradit_official</strong> ${styleHashtagsHtml(item.caption || item.content)}</div>
            </div>
        `;
    }

    function buildFacebookPreviewHtml(item) {
        return `
            <div style="background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
                <div style="display:flex; align-items:flex-start; gap:10px; padding:12px 14px 8px;">
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#1877f2,#0d5cc9);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">SI</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#050505;">StradIT</div>
                        <div style="font-size:0.72rem; color:#65676b;">Just now &middot; <i class="fas fa-earth-americas"></i></div>
                    </div>
                    <i class="fas fa-ellipsis" style="color:#65676b;"></i>
                </div>
                <div style="padding:0 14px 12px; font-size:0.85rem; color:#050505; line-height:1.45;">${styleHashtagsHtml(item.caption || item.content)}</div>
                ${previewMediaBlockHtml(item, false)}
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 14px; font-size:0.72rem; color:#65676b; border-bottom:1px solid #eee;">
                    <span><i class="fas fa-thumbs-up" style="color:#1877f2;"></i> 96 &middot; <i class="fas fa-heart" style="color:#f33e58;"></i></span>
                    <span>18 comments &middot; 4 shares</span>
                </div>
                <div style="display:flex; padding:2px 4px;">
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#65676b;font-weight:600;font-size:0.78rem;"><i class="far fa-thumbs-up me-1"></i>Like</div>
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#65676b;font-weight:600;font-size:0.78rem;"><i class="far fa-comment me-1"></i>Comment</div>
                    <div style="flex:1;text-align:center;padding:8px 4px;color:#65676b;font-weight:600;font-size:0.78rem;"><i class="fas fa-share me-1"></i>Share</div>
                </div>
            </div>
        `;
    }

    function buildGenericPreviewHtml(item, platform) {
        return `
            <div style="background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
                <div style="display:flex; align-items:center; gap:10px; padding:12px 14px;">
                    <div style="width:40px;height:40px;border-radius:50%;background:#4f46e5;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">SI</div>
                    <div>
                        <div style="font-weight:600; font-size:0.9rem;">StradIT</div>
                        <div style="font-size:0.72rem; color:#666;">${platformDisplayName(platform)} &middot; Just now</div>
                    </div>
                </div>
                ${previewMediaBlockHtml(item, false)}
                <div style="padding:12px 14px; font-size:0.85rem; line-height:1.45;">${styleHashtagsHtml(item.caption || item.content)}</div>
            </div>
        `;
    }

    function showToast(message, type) {
        const bgClass = type === 'success' ? 'bg-success' : type === 'danger' ? 'bg-danger' : type === 'warning' ? 'bg-warning' : 'bg-primary';
        const toastHtml = `
            <div class="toast align-items-center text-white ${bgClass} border-0 show" role="alert" style="position: fixed; bottom: 20px; right: 20px; z-index: 1055; min-width: 250px;">
                <div class="d-flex">
                    <div class="toast-body">${escapeHtml(message)}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="$(this).closest('.toast').remove()"></button>
                </div>
            </div>
        `;
        const $toast = $(toastHtml).appendTo('body');
        setTimeout(() => $toast.remove(), 6000);
    }

    let currentReq = null;

    // Builds the preview card(s) for one platform - shared by the initial
    // render and by switchPreviewPlatform() so a reviewer can check how the
    // same content looks across LinkedIn/Facebook/Instagram, not just
    // whichever platform it was actually generated for.
    function buildPreviewsHtml(req, platform) {
        const imageUrls = req.image_urls || [];
        if (!imageUrls.length) {
            return buildGenericPreviewHtml({ type: 'Text (Caption)', content: req.caption, caption: req.caption, platform: platform }, platform);
        }
        return imageUrls.map((url) => {
            const item = { type: 'image', content: url, caption: req.caption, platform: platform };
            if (platform === 'linkedin') return buildLinkedInPreviewHtml(item);
            if (platform === 'instagram') return buildInstagramPreviewHtml(item);
            if (platform === 'facebook') return buildFacebookPreviewHtml(item);
            return buildGenericPreviewHtml(item, platform);
        }).join('<div class="mb-3"></div>');
    }

    window.switchPreviewPlatform = function (platform) {
        if (!currentReq) return;
        $('.preview-platform-tab').removeClass('active');
        $(`.preview-platform-tab[data-platform="${platform}"]`).addClass('active');
        $('#approvalPreviewArea').html(buildPreviewsHtml(currentReq, platform));
    };

    function renderApprovalCard(req) {
        currentReq = req;
        const platform = (req.platform || 'linkedin').toLowerCase();
        const previewsHtml = buildPreviewsHtml(req, platform);
        const platformTabsHtml = ['linkedin', 'facebook', 'instagram'].map(p => `
            <button type="button" class="preview-platform-tab${p === platform ? ' active' : ''}" data-platform="${p}" onclick="switchPreviewPlatform('${p}')">
                <i class="fab fa-${p} me-1"></i>${platformDisplayName(p)}
            </button>
        `).join('');
        const status = req.status || 'pending';
        const statusPill = status === 'approved'
            ? '<span class="ap-status ap-status-approved"><i class="fas fa-check-circle"></i>Accepted</span>'
            : status === 'rejected'
                ? '<span class="ap-status ap-status-rejected"><i class="fas fa-times-circle"></i>Rejected</span>'
                : '<span class="ap-status ap-status-pending"><i class="fas fa-hourglass-half"></i>Pending</span>';

        let decisionHtml;
        if (req.status === 'pending') {
            decisionHtml = `
                <label class="guideline-field-label" for="approvalDecisionComments">Comments <span style="font-weight: 400; color: #9CA3AF;">(optional)</span></label>
                <textarea id="approvalDecisionComments" class="form-control" rows="3" placeholder="Add any feedback for the requester..."></textarea>
                <div class="ap-decision-actions">
                    <button type="button" class="ap-btn ap-btn-reject" onclick="submitApprovalDecision('rejected')"><i class="fas fa-times"></i>Reject</button>
                    <button type="button" class="ap-btn ap-btn-accept" onclick="submitApprovalDecision('approved')"><i class="fas fa-check"></i>Accept</button>
                </div>
            `;
        } else {
            const isApproved = req.status === 'approved';
            decisionHtml = `
                <div class="ap-decision ${isApproved ? 'approved' : 'rejected'}">
                    <div class="card-icon-circle"><i class="fas ${isApproved ? 'fa-check' : 'fa-times'}"></i></div>
                    <div>
                        <strong>${isApproved ? 'Accepted' : 'Rejected'}${req.decided_by ? ' by ' + escapeHtml(req.decided_by) : ''}</strong>
                        <small>${req.decided_at ? new Date(req.decided_at).toLocaleString() : ''}</small>
                        ${req.comments ? `<div class="ap-decision-comments"><strong>Comments:</strong> ${escapeHtml(req.comments)}</div>` : ''}
                    </div>
                </div>
            `;
        }

        // Two brand-profile cards: post preview (left), details + decision (right).
        $('#approvalReviewCard').html(`
            <div class="ap-review-grid">
                <div class="brand-config-card">
                    <h6><div class="card-icon-circle"><i class="fas fa-eye"></i></div> Post preview</h6>
                    <div class="brand-config-card-desc">How this content looks on each platform.</div>
                    <div class="preview-platform-tabs" id="previewPlatformTabs">${platformTabsHtml}</div>
                    <div id="approvalPreviewArea">${previewsHtml}</div>
                </div>
                <div class="d-flex flex-column gap-4">
                    <div class="brand-config-card">
                        <h6><div class="card-icon-circle"><i class="fas fa-circle-info"></i></div> Request details</h6>
                        <div class="brand-config-card-desc">What was generated and why.</div>
                        <div class="ap-row-meta mb-3">
                            ${statusPill}
                            <span class="ap-tag ap-tag-primary">Generated for ${platformDisplayName(platform)}</span>
                            <span class="ap-tag">${escapeHtml(req.asset_type || 'content')}</span>
                        </div>
                        <label class="guideline-field-label">Story / Strategy Context</label>
                        <div class="ap-context">${escapeHtml(req.story_context || 'No context captured.')}</div>
                    </div>
                    <div class="brand-config-card">
                        <h6><div class="card-icon-circle"><i class="fas fa-gavel"></i></div> Decision</h6>
                        <div class="brand-config-card-desc">${status === 'pending' ? 'Accept to clear it for publishing, or reject with feedback.' : 'This request has already been decided.'}</div>
                        ${decisionHtml}
                    </div>
                </div>
            </div>
        `);
    }

    window.submitApprovalDecision = function (decision) {
        const comments = $('#approvalDecisionComments').val();
        $.ajax({
            url: `/api/approval-requests/${window.APPROVAL_REQUEST_ID}/decide`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ decision: decision, comments: comments }),
            success: function (r) {
                if (r.success) {
                    showToast(`Content ${decision === 'approved' ? 'accepted' : 'rejected'}.`, decision === 'approved' ? 'success' : 'warning');
                    renderApprovalCard(r.request);
                } else {
                    showToast(r.error || 'Failed to submit decision.', 'danger');
                }
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error submitting decision.', 'danger');
            }
        });
    };

    $.ajax({
        url: `/api/approval-requests/${window.APPROVAL_REQUEST_ID}`,
        type: 'GET',
        success: function (r) {
            if (r.success && r.request) {
                renderApprovalCard(r.request);
            } else {
                $('#approvalReviewCard').html('<div class="brand-config-card"><div class="ap-empty"><div class="card-icon-circle"><i class="fas fa-circle-exclamation"></i></div><h6>Not found</h6><p>Approval request not found.</p></div></div>');
            }
        },
        error: function (xhr) {
            const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not load this approval request.';
            $('#approvalReviewCard').html(`<div class="brand-config-card"><div class="ap-empty"><div class="card-icon-circle"><i class="fas fa-circle-exclamation"></i></div><h6>Could not load</h6><p>${escapeHtml(msg)}</p></div></div>`);
        }
    });
});
