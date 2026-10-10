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

    // The requester's company (from the request), shown in every preview
    function brandName() { return (currentReq && currentReq.company_name) || 'Your brand'; }
    function brandHandle() { return brandName().toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 24) || 'yourbrand'; }
    function brandInitials() {
        return brandName().split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'YB';
    }

    function previewMediaBlockHtml(item, squareAspect) {
        if (item && item.mediaHtml) return item.mediaHtml;
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
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#0a66c2,#004182);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">${escapeHtml(brandInitials())}</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#000;">${escapeHtml(brandName())}</div>
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
                        <div style="width:100%;height:100%;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.65rem;">${escapeHtml(brandInitials())}</div>
                    </div>
                    <div style="font-weight:600; font-size:0.85rem;">${escapeHtml(brandHandle())}</div>
                    <i class="fas fa-ellipsis ms-auto" style="color:#000;"></i>
                </div>
                ${previewMediaBlockHtml(item, true)}
                <div style="padding:10px 12px 4px; display:flex; gap:14px; font-size:1.25rem; color:#000;">
                    <i class="far fa-heart"></i><i class="far fa-comment"></i><i class="far fa-paper-plane"></i>
                    <i class="far fa-bookmark ms-auto"></i>
                </div>
                <div style="padding:4px 12px 2px; font-size:0.78rem; font-weight:600;">248 likes</div>
                <div style="padding:0 12px 12px; font-size:0.82rem; line-height:1.4;"><strong>${escapeHtml(brandHandle())}</strong> ${styleHashtagsHtml(item.caption || item.content)}</div>
            </div>
        `;
    }

    function buildFacebookPreviewHtml(item) {
        return `
            <div style="background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
                <div style="display:flex; align-items:flex-start; gap:10px; padding:12px 14px 8px;">
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#1877f2,#0d5cc9);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">${escapeHtml(brandInitials())}</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#050505;">${escapeHtml(brandName())}</div>
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
                    <div style="width:40px;height:40px;border-radius:50%;background:#c2410c;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">${escapeHtml(brandInitials())}</div>
                    <div>
                        <div style="font-weight:600; font-size:0.9rem;">${escapeHtml(brandName())}</div>
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
                    ${complianceCardHtml(req.compliance)}
                    <div class="brand-config-card">
                        <h6><div class="card-icon-circle"><i class="fas fa-gavel"></i></div> Decision</h6>
                        <div class="brand-config-card-desc">${status === 'pending' ? 'Accept to clear it for publishing, or reject with feedback.' : 'This request has already been decided.'}</div>
                        ${decisionHtml}
                    </div>
                </div>
            </div>
        `);
    }

    // Compliance review captured when approval was requested (see
    // services/compliance_service.py) - so the reviewer sees regulatory risks
    // before accepting. Absent when the requester has no compliance profile.
    function complianceCardHtml(compliance) {
        if (!compliance) return '';
        const flags = compliance.flags || [];
        const flagsHtml = flags.length
            ? `<ul class="list-unstyled small mb-2">${flags.map(f => `
                <li class="mb-2">
                    <span class="badge ${f.severity === 'high' ? 'bg-danger-subtle text-danger' : 'bg-warning-subtle text-warning'} text-uppercase me-1">${escapeHtml(f.severity)}</span>
                    <strong>${escapeHtml(f.framework)}</strong>
                    <div>${escapeHtml(f.issue)}</div>
                    ${f.fix ? `<div class="text-muted">Suggested fix: ${escapeHtml(f.fix)}</div>` : ''}
                </li>`).join('')}</ul>`
            : '<p class="small text-success mb-2"><i class="fas fa-check-circle me-1"></i>No issues found against the applicable rules.</p>';
        const disclaimers = compliance.disclaimers_added || [];
        const disclaimerHtml = disclaimers.length
            ? `<label class="guideline-field-label">Required disclaimers missing from the caption</label>
               <ul class="small mb-2">${disclaimers.map(d => `<li>${escapeHtml(d)}</li>`).join('')}</ul>`
            : '';
        const suggestedHtml = (flags.length || disclaimers.length) && compliance.suggested_caption
            ? `<label class="guideline-field-label">Compliant version (suggested)</label>
               <div class="ap-context">${escapeHtml(compliance.suggested_caption)}</div>`
            : '';
        return `
            <div class="brand-config-card">
                <h6><div class="card-icon-circle"><i class="fas fa-scale-balanced"></i></div> Compliance review</h6>
                <div class="brand-config-card-desc">Checked against ${compliance.rules_checked} advertising rules for this business's industry and markets. Guidance only, not legal advice.</div>
                ${flagsHtml}${disclaimerHtml}${suggestedHtml}
            </div>`;
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

    // ── A whole post for several platforms (Studio Chat "Send for approval") ──
    const DECISION_LABEL = { approved: 'Approved', changes: 'Changes requested', pending: 'To review' };
    let activeItem = 0;
    const slideIndex = {};
    const draft = {};  // reviewer's choices before submitting: {index: {decision, comment}}

    function carouselMediaHtml(item, idx, square) {
        const images = item.images || [];
        const i = Math.min(slideIndex[idx] || 0, images.length - 1);
        const fit = square ? 'aspect-ratio: 1/1; object-fit: cover;' : 'max-height: 460px; object-fit: contain;';
        const doc = item.linkedin_format === 'document';
        return `
            <div class="ap-carousel" data-item="${idx}">
                <img src="${escapeHtml(images[i])}" alt="Slide ${i + 1}" class="d-block w-100" style="${fit} background:#f3f4f6;">
                ${doc ? '<span class="ap-carousel-badge"><i class="fas fa-file-pdf me-1"></i>Document</span>' : ''}
                <span class="ap-carousel-count">${i + 1} / ${images.length}</span>
                <button type="button" class="ap-carousel-nav prev" data-item="${idx}" data-step="-1" ${i === 0 ? 'disabled' : ''} aria-label="Previous slide"><i class="fas fa-chevron-left"></i></button>
                <button type="button" class="ap-carousel-nav next" data-item="${idx}" data-step="1" ${i === images.length - 1 ? 'disabled' : ''} aria-label="Next slide"><i class="fas fa-chevron-right"></i></button>
                <div class="ap-carousel-dots">${images.map((_, d) => `<span class="${d === i ? 'on' : ''}"></span>`).join('')}</div>
            </div>
            ${(item.slide_titles || [])[i] ? `<div class="ap-slide-title">Slide ${i + 1}: ${escapeHtml(item.slide_titles[i])}</div>` : ''}`;
    }

    function bundlePreviewHtml(item, idx) {
        const caption = [item.caption, (item.hashtags || []).join(' ')].filter(Boolean).join('\n\n');
        const square = item.platform === 'instagram';
        let mediaHtml = '';
        if (item.media === 'carousel') mediaHtml = carouselMediaHtml(item, idx, square);
        else if ((item.images || []).length) mediaHtml = previewMediaBlockHtml({ type: 'image', content: item.images[0] }, square);
        const card = { type: 'image', content: item.images && item.images[0], caption: caption, mediaHtml: mediaHtml || ' ' };
        if (!mediaHtml) card.mediaHtml = ' ';
        const html = item.platform === 'linkedin' ? buildLinkedInPreviewHtml(card)
            : item.platform === 'instagram' ? buildInstagramPreviewHtml(card)
            : item.platform === 'facebook' ? buildFacebookPreviewHtml(card)
            : buildGenericPreviewHtml(card, item.platform);
        const fmt = item.media === 'carousel'
            ? `<span class="ap-tag ap-tag-primary"><i class="fas fa-images me-1"></i>Carousel · ${item.images.length} slides</span>`
              + (item.platform === 'linkedin' ? `<span class="ap-tag">${item.linkedin_format === 'document' ? 'Document (PDF)' : 'Multi-image post'}</span>` : '')
            : `<span class="ap-tag">${(item.images || []).length ? 'Single image' : 'Text only'}</span>`;
        const pdf = item.pdf_url ? `<a class="btn-xs-outline-info text-decoration-none ms-auto" href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener"><i class="fas fa-file-pdf me-1"></i>Open PDF</a>` : '';
        return `<div class="ap-row-meta mb-3 d-flex align-items-center flex-wrap gap-2">${fmt}${pdf}</div>${html}`;
    }

    function bundleTabsHtml(req) {
        return req.items.map((item, idx) => {
            const d = (draft[idx] && draft[idx].decision) || item.decision || 'pending';
            return `<button type="button" class="preview-platform-tab${idx === activeItem ? ' active' : ''}" data-item-tab="${idx}">
                <i class="fab fa-${escapeHtml(item.platform)} me-1"></i>${platformDisplayName(item.platform)}
                <span class="ap-dot ap-dot-${d}" title="${DECISION_LABEL[d]}"></span></button>`;
        }).join('');
    }

    function itemDecisionRowHtml(item, idx) {
        const d = draft[idx] || {};
        return `
            <div class="ap-item-decision" data-idx="${idx}">
                <div class="ap-item-decision-head">
                    <strong><i class="fab fa-${escapeHtml(item.platform)} me-1"></i>${platformDisplayName(item.platform)}</strong>
                    <div class="ap-item-buttons">
                        <button type="button" class="ap-choice${d.decision === 'approved' ? ' on approve' : ''}" data-choice="approved" data-idx="${idx}"><i class="fas fa-check me-1"></i>Approve</button>
                        <button type="button" class="ap-choice${d.decision === 'changes' ? ' on changes' : ''}" data-choice="changes" data-idx="${idx}"><i class="fas fa-pen me-1"></i>Request changes</button>
                    </div>
                </div>
                <textarea class="form-control form-control-sm mt-2 ${d.decision === 'changes' ? '' : 'd-none'}" rows="2" data-comment="${idx}"
                    placeholder="What should change on ${platformDisplayName(item.platform)}?">${escapeHtml(d.comment || '')}</textarea>
            </div>`;
    }

    function bundleDecisionHtml(req) {
        if (req.status === 'pending') {
            const allChosen = req.items.every((_, idx) => draft[idx] && draft[idx].decision);
            return `
                ${req.items.map(itemDecisionRowHtml).join('')}
                <label class="guideline-field-label mt-3" for="approvalDecisionComments">Overall comments <span style="font-weight: 400; color: #9CA3AF;">(optional)</span></label>
                <textarea id="approvalDecisionComments" class="form-control" rows="2" placeholder="Anything for the whole post...">${escapeHtml(draft.comments || '')}</textarea>
                <div class="ap-decision-actions">
                    <button type="button" class="ap-btn ap-btn-reject" id="apApproveAll"><i class="fas fa-check-double"></i>Approve all</button>
                    <button type="button" class="ap-btn ap-btn-accept" id="apSubmitDecisions" ${allChosen ? '' : 'disabled'}><i class="fas fa-paper-plane"></i>Submit decisions</button>
                </div>
                ${allChosen ? '' : '<div class="small text-muted mt-2">Choose Approve or Request changes for every platform, or use Approve all.</div>'}`;
        }
        const rows = req.items.map(item => `
            <div class="ap-item-result">
                <strong>${platformDisplayName(item.platform)}</strong>
                <span class="ap-result ap-result-${item.decision}">${item.decision === 'approved' ? '<i class="fas fa-check me-1"></i>Approved' : '<i class="fas fa-pen me-1"></i>Changes requested'}</span>
                ${item.comment ? `<div class="ap-decision-comments">${escapeHtml(item.comment)}</div>` : ''}
                ${publishHtml(req, item)}
            </div>`).join('');
        return `${rows}
            <div class="small text-muted mt-2">${req.decided_by ? 'Decided by ' + escapeHtml(req.decided_by) : ''}${req.decided_at ? ' · ' + new Date(req.decided_at).toLocaleString() : ''}</div>
            ${req.comments ? `<div class="ap-decision-comments"><strong>Comments:</strong> ${escapeHtml(req.comments)}</div>` : ''}`;
    }

    // Publishing an approved platform (Instagram for now) - only the requester sees the button
    function publishHtml(req, item) {
        if (item.published) {
            const p = item.published;
            return `<div class="ap-published"><i class="fab fa-instagram me-1"></i>Published${p.account ? ' to ' + escapeHtml(p.account) : ''}
                ${p.at ? ' · ' + new Date(p.at).toLocaleString() : ''}
                ${p.permalink ? ` · <a href="${escapeHtml(p.permalink)}" target="_blank" rel="noopener">View post</a>` : ''}</div>`;
        }
        const pub = req.publishing || {};
        if (item.platform !== 'instagram' || item.decision !== 'approved' || !pub.can_publish) return '';
        const error = item.publish_error ? `<div class="ap-publish-error"><i class="fas fa-triangle-exclamation me-1"></i>${escapeHtml(item.publish_error)}</div>` : '';
        if (!pub.instagram) {
            return `<div class="ap-publish-row"><a class="btn-xs-outline-info text-decoration-none" href="/settings"><i class="fab fa-instagram me-1"></i>Connect Instagram to publish</a></div>${error}`;
        }
        return `<div class="ap-publish-row">
                <button type="button" class="ap-btn ap-btn-accept ap-publish" data-platform="instagram">
                    <i class="fab fa-instagram"></i>Publish to ${escapeHtml(pub.instagram)}</button>
                <small>${item.media === 'carousel' ? `Carousel of ${item.images.length} slides` : 'One image'} · posts immediately</small>
            </div>${error}`;
    }

    $(document).on('click', '.ap-publish', function () {
        const $btn = $(this);
        const platform = $btn.data('platform');
        if (!window.confirm(`Publish this post to ${platformDisplayName(platform)} now? It goes live immediately.`)) return;
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i>Publishing...');
        $.ajax({ url: `/api/approval-requests/${window.APPROVAL_REQUEST_ID}/publish`, type: 'POST', contentType: 'application/json',
                 data: JSON.stringify({ platform: platform }) })
            .done(r => { showToast('Published to Instagram.', 'success'); renderBundle(r.request); })
            .fail(xhr => {
                const body = xhr.responseJSON || {};
                showToast(body.error || 'Could not publish.', 'danger');
                if (body.request) renderBundle(body.request);
                else $.get(`/api/approval-requests/${window.APPROVAL_REQUEST_ID}`).done(r => r.request && renderBundle(r.request));
            });
    });

    function bundleStatusPill(status) {
        return {
            approved: '<span class="ap-status ap-status-approved"><i class="fas fa-check-circle"></i>Approved</span>',
            rejected: '<span class="ap-status ap-status-rejected"><i class="fas fa-pen"></i>Changes requested</span>',
            partial: '<span class="ap-status ap-status-partial"><i class="fas fa-circle-half-stroke"></i>Partly approved</span>',
        }[status] || '<span class="ap-status ap-status-pending"><i class="fas fa-hourglass-half"></i>Pending</span>';
    }

    function renderBundle(req) {
        currentReq = req;
        activeItem = Math.min(activeItem, req.items.length - 1);
        const item = req.items[activeItem];
        $('#approvalReviewCard').html(`
            <div class="ap-review-grid">
                <div class="brand-config-card">
                    <h6><div class="card-icon-circle"><i class="fas fa-eye"></i></div> Post preview</h6>
                    <div class="brand-config-card-desc">One post for ${req.items.length} platform${req.items.length > 1 ? 's' : ''} - check each one.</div>
                    <div class="preview-platform-tabs">${bundleTabsHtml(req)}</div>
                    <div id="approvalPreviewArea">${bundlePreviewHtml(item, activeItem)}</div>
                </div>
                <div class="d-flex flex-column gap-4">
                    <div class="brand-config-card">
                        <h6><div class="card-icon-circle"><i class="fas fa-circle-info"></i></div> Request details</h6>
                        <div class="ap-row-meta mb-3">${bundleStatusPill(req.status)}
                            ${req.items.map(i => `<span class="ap-tag">${platformDisplayName(i.platform)}</span>`).join('')}</div>
                        ${req.story_context ? `<label class="guideline-field-label">Brief</label><div class="ap-context">${escapeHtml(req.story_context)}</div>` : ''}
                    </div>
                    ${complianceCardHtml(item.compliance)}
                    <div class="brand-config-card">
                        <h6><div class="card-icon-circle"><i class="fas fa-gavel"></i></div> Decision</h6>
                        <div class="brand-config-card-desc">${req.status === 'pending' ? 'Approve each platform, or ask for changes with a comment.' : 'This request has been decided.'}</div>
                        <div id="apBundleDecision">${bundleDecisionHtml(req)}</div>
                    </div>
                </div>
            </div>`);
    }

    function submitBundle(payload) {
        $('#apSubmitDecisions, #apApproveAll').prop('disabled', true);
        $.ajax({
            url: `/api/approval-requests/${window.APPROVAL_REQUEST_ID}/decide`, type: 'POST', contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (r) {
                if (!r.success) { showToast(r.error || 'Failed to submit.', 'danger'); return; }
                const told = r.requester_notified ? ' The requester has been emailed.' : '';
                showToast(({ approved: 'Approved.', rejected: 'Changes requested.', partial: 'Decisions saved.' }[r.request.status] || 'Saved.') + told,
                          r.request.status === 'approved' ? 'success' : 'warning');
                renderBundle(r.request);
            },
            error: function (xhr) {
                $('#apSubmitDecisions, #apApproveAll').prop('disabled', false);
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error submitting the decision.', 'danger');
            }
        });
    }

    $(document).on('click', '[data-item-tab]', function () {
        activeItem = Number($(this).data('item-tab'));
        renderBundle(currentReq);
    });
    $(document).on('click', '.ap-carousel-nav', function () {
        const idx = Number($(this).data('item'));
        const item = currentReq.items[idx];
        slideIndex[idx] = Math.max(0, Math.min(item.images.length - 1, (slideIndex[idx] || 0) + Number($(this).data('step'))));
        $('#approvalPreviewArea').html(bundlePreviewHtml(item, idx));
    });
    $(document).on('click', '.ap-choice', function () {
        const idx = Number($(this).data('idx'));
        draft[idx] = Object.assign(draft[idx] || {}, { decision: $(this).data('choice') });
        draft.comments = $('#approvalDecisionComments').val();
        renderBundle(currentReq);
        if (draft[idx].decision === 'changes') $(`[data-comment="${idx}"]`).trigger('focus');
    });
    $(document).on('input', '[data-comment]', function () {
        const idx = Number($(this).data('comment'));
        draft[idx] = Object.assign(draft[idx] || {}, { comment: $(this).val() });
    });
    $(document).on('click', '#apApproveAll', function () {
        submitBundle({ decision: 'approved', comments: $('#approvalDecisionComments').val() });
    });
    $(document).on('click', '#apSubmitDecisions', function () {
        const missing = currentReq.items.findIndex((_, idx) => draft[idx] && draft[idx].decision === 'changes' && !(draft[idx].comment || '').trim());
        if (missing >= 0) {
            showToast(`Say what should change on ${platformDisplayName(currentReq.items[missing].platform)}.`, 'warning');
            $(`[data-comment="${missing}"]`).trigger('focus');
            return;
        }
        const items = {};
        currentReq.items.forEach((_, idx) => { items[idx] = draft[idx]; });
        submitBundle({ items: items, comments: $('#approvalDecisionComments').val() });
    });

    $.ajax({
        url: `/api/approval-requests/${window.APPROVAL_REQUEST_ID}`,
        type: 'GET',
        success: function (r) {
            if (r.success && r.request) {
                if (r.request.items && r.request.items.length) renderBundle(r.request);
                else renderApprovalCard(r.request);
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
