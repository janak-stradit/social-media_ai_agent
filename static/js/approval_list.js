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
        if (status === 'approved') return '<span class="badge rounded-pill bg-success-subtle text-success px-3 py-2"><i class="fas fa-check-circle me-1"></i>Accepted</span>';
        if (status === 'rejected') return '<span class="badge rounded-pill bg-danger-subtle text-danger px-3 py-2"><i class="fas fa-times-circle me-1"></i>Rejected</span>';
        return '<span class="badge rounded-pill bg-warning-subtle text-warning px-3 py-2"><i class="fas fa-hourglass-half me-1"></i>Pending</span>';
    }

    function renderList(requests) {
        const body = $('#approvalListBody');
        if (!requests.length) {
            body.html('<div class="text-center text-muted py-5"><i class="fas fa-inbox mb-3" style="font-size: 1.75rem; opacity: 0.4;"></i><p class="small m-0">No approval requests here yet.</p></div>');
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
                ? `<div class="text-muted" style="font-size: 0.72rem;">${req.status === 'approved' ? 'Accepted' : 'Rejected'}${req.decided_by ? ' by ' + escapeHtml(req.decided_by) : ''} &middot; ${new Date(req.decided_at).toLocaleString()}</div>`
                : '';

            return `
                <a href="/approve/${req.id}" class="approval-row">
                    ${thumb}
                    <div class="flex-grow-1" style="min-width: 0;">
                        <div class="d-flex align-items-center gap-2 mb-1 flex-wrap">
                            <span class="badge rounded-pill bg-primary-subtle text-primary" style="font-size: 0.68rem;">${platformDisplayName(req.platform)}</span>
                            <span class="badge rounded-pill bg-light text-dark border" style="font-size: 0.68rem;">${escapeHtml(req.asset_type || 'content')}</span>
                            <span class="text-muted" style="font-size: 0.72rem;">${created}</span>
                        </div>
                        <div class="text-dark small text-truncate" style="max-width: 100%;">${escapeHtml(shortCaption) || '<em class="text-muted">No caption</em>'}</div>
                        ${decidedLine}
                    </div>
                    <div class="flex-shrink-0">${statusBadgeHtml(req.status)}</div>
                    <i class="fas fa-chevron-right text-muted flex-shrink-0"></i>
                </a>
            `;
        }).join('');

        body.html(html);
    }

    function loadRequests() {
        $('#approvalListBody').html('<div class="text-center text-muted py-5"><i class="fas fa-spinner fa-spin me-2"></i>Loading approval requests...</div>');
        const url = currentStatus ? `/api/approval-requests?status=${encodeURIComponent(currentStatus)}` : '/api/approval-requests';
        $.ajax({
            url: url,
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    renderList(r.requests || []);
                } else {
                    $('#approvalListBody').html(`<div class="text-center text-muted py-5"><i class="fas fa-circle-exclamation mb-3" style="font-size: 1.75rem; opacity: 0.4;"></i><p class="small m-0">${escapeHtml(r.error || 'Could not load approval requests.')}</p></div>`);
                }
            },
            error: function (xhr) {
                const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not load approval requests.';
                $('#approvalListBody').html(`<div class="text-center text-muted py-5"><i class="fas fa-circle-exclamation mb-3" style="font-size: 1.75rem; opacity: 0.4;"></i><p class="small m-0">${escapeHtml(msg)}</p></div>`);
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
