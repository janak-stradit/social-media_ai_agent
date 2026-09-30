// Standalone Approvals dashboard (/approve) - lists every review request
// (past and current) with its status, so a reviewer can see everything in
// one place instead of only following one-off email links.
$(document).ready(function () {

    let currentStatus = '';

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

    function statusBadgeHtml(status) {
        if (status === 'approved') return '<span class="ap-status ap-status-approved"><i class="fas fa-check-circle"></i>Accepted</span>';
        if (status === 'rejected') return '<span class="ap-status ap-status-rejected"><i class="fas fa-times-circle"></i>Rejected</span>';
        return '<span class="ap-status ap-status-pending"><i class="fas fa-hourglass-half"></i>Pending</span>';
    }

    // Brand-profile style empty/loading/error block (icon circle + text).
    function stateHtml(icon, title, text) {
        return `<div class="ap-empty"><div class="card-icon-circle"><i class="fas ${icon}"></i></div>${title ? `<h6>${escapeHtml(title)}</h6>` : ''}<p>${escapeHtml(text)}</p></div>`;
    }

    const FILTER_COPY = {
        '': ['All requests', 'Newest first. Pending requests are marked with an amber edge.'],
        pending: ['Pending requests', 'Waiting for a reviewer to accept or reject them.'],
        approved: ['Accepted requests', 'Approved content, with who accepted it and when.'],
        rejected: ['Rejected requests', 'Content sent back, with who rejected it and when.'],
    };

    // Counts in the side panel come from the unfiltered list, so they're
    // refreshed whenever "All" is loaded (on page load and when re-selected).
    function updateCounts(requests) {
        const count = (status) => requests.filter((r) => (r.status || 'pending') === status).length;
        $('#apCountAll').text(requests.length);
        $('#apCountPending').text(count('pending'));
        $('#apCountApproved').text(count('approved'));
        $('#apCountRejected').text(count('rejected'));
    }

    function renderList(requests) {
        const body = $('#approvalListBody');
        if (!requests.length) {
            body.html(stateHtml('fa-inbox', 'Nothing here yet', 'No approval requests match this filter.'));
            return;
        }

        const html = requests.map((req) => {
            const thumb = (req.image_urls && req.image_urls[0])
                ? `<img src="${req.image_urls[0]}" class="approval-thumb" alt="">`
                : `<div class="approval-thumb-placeholder"><i class="fas fa-align-left"></i></div>`;
            const caption = (req.caption || '').replace(/\s+/g, ' ').trim();
            const shortCaption = caption.length > 140 ? caption.slice(0, 140) + '…' : caption;
            const created = req.created_at ? new Date(req.created_at).toLocaleString() : '';
            const decidedLine = req.decided_at
                ? `<div class="ap-row-decided">${req.status === 'approved' ? 'Accepted' : 'Rejected'}${req.decided_by ? ' by ' + escapeHtml(req.decided_by) : ''} &middot; ${new Date(req.decided_at).toLocaleString()}</div>`
                : '';
            const pendingClass = (req.status || 'pending') === 'pending' ? ' is-pending' : '';

            return `
                <a href="/approve/${req.id}" class="approval-row${pendingClass}">
                    ${thumb}
                    <div class="flex-grow-1" style="min-width: 0;">
                        <div class="ap-row-meta">
                            <span class="ap-tag ap-tag-primary">${platformDisplayName(req.platform)}</span>
                            <span class="ap-tag">${escapeHtml(req.asset_type || 'content')}</span>
                            <span class="ap-row-date">${created}</span>
                        </div>
                        <div class="ap-row-caption">${escapeHtml(shortCaption) || '<em>No caption</em>'}</div>
                        ${decidedLine}
                    </div>
                    <div class="flex-shrink-0">${statusBadgeHtml(req.status)}</div>
                    <i class="fas fa-chevron-right ap-row-chevron"></i>
                </a>
            `;
        }).join('');

        body.html(html);
    }

    function loadRequests() {
        const copy = FILTER_COPY[currentStatus] || FILTER_COPY[''];
        $('#approvalListTitle').text(copy[0]);
        $('#approvalListDesc').text(copy[1]);
        $('#approvalListBody').html(stateHtml('fa-spinner fa-spin', '', 'Loading approval requests...'));
        const url = currentStatus ? `/api/approval-requests?status=${encodeURIComponent(currentStatus)}` : '/api/approval-requests';
        $.ajax({
            url: url,
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    if (!currentStatus) updateCounts(r.requests || []);
                    renderList(r.requests || []);
                } else {
                    $('#approvalListBody').html(stateHtml('fa-circle-exclamation', 'Could not load', r.error || 'Could not load approval requests.'));
                }
            },
            error: function (xhr) {
                const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not load approval requests.';
                $('#approvalListBody').html(stateHtml('fa-circle-exclamation', 'Could not load', msg));
            }
        });
    }

    $('#approvalStatusTabs').on('click', '.status-filter-tab', function () {
        $('.status-filter-tab').removeClass('active');
        $(this).addClass('active');
        currentStatus = $(this).data('status') || '';
        loadRequests();
    });

    loadRequests();
});
