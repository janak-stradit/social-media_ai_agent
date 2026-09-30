// Approval reviewer email - shared by "My Brand Configuration" (/brand-profile)
// and "Brand Configuration" (/brand-configuration). Renders into
// #approvalSettingsCard and saves via GET/PUT /api/approval-settings. That
// address receives each "Send for Approval" email, and the reviewer signing
// in with it can see and decide those requests on /approve.
$(function () {
    const $card = $('#approvalSettingsCard');
    if (!$card.length) return;

    $card.html(`
        <h6><div class="card-icon-circle"><i class="fas fa-user-check"></i></div> Approval Reviewer</h6>
        <div class="brand-config-card-desc">Who approves your content. "Send for Approval" emails this person a Review &amp; Decide link; they sign in with this email to accept or reject. Only you and this reviewer can see your approval requests.</div>
        <label class="guideline-field-label" for="approvalReviewerEmail">Reviewer email</label>
        <div class="d-flex gap-2 align-items-start flex-wrap">
            <!-- Deliberately not .form-control: /brand-profile's unsaved-changes tracking watches every .form-control, and this field saves on its own -->
            <input type="email" id="approvalReviewerEmail" placeholder="reviewer@yourcompany.com" autocomplete="email"
                style="flex: 1; min-width: 240px; padding: 0.55rem 0.8rem; border: 1px solid var(--border-color, #d1d5db); border-radius: 8px; font-size: 0.9rem; background: var(--bg-card, #fff); color: inherit;">
            <button type="button" class="btn-primary-save" id="approvalReviewerSaveBtn">
                <i class="fas fa-check me-2"></i>Save Reviewer
            </button>
        </div>
        <div class="guideline-field-hint" id="approvalReviewerHint"></div>
        <div class="small mt-2" id="approvalReviewerStatus" role="status"></div>
    `);

    const $input = $('#approvalReviewerEmail');
    const $status = $('#approvalReviewerStatus');
    let defaultReviewer = null;

    function setHint(saved) {
        $('#approvalReviewerHint').text(saved
            ? ''
            : defaultReviewer
                ? `Not set - requests currently go to the default reviewer (${defaultReviewer}).`
                : 'Not set - you need a reviewer before you can send content for approval.');
    }

    function showStatus(message, ok) {
        $status.text(message).removeClass('text-success text-danger').addClass(ok ? 'text-success' : 'text-danger');
    }

    $.getJSON('/api/approval-settings').done(function (r) {
        defaultReviewer = r.default_reviewer_email || null;
        $input.val(r.reviewer_email || '');
        setHint(r.reviewer_email);
    });

    $('#approvalReviewerSaveBtn').on('click', function () {
        const email = $input.val().trim();
        if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
            showStatus('Please enter a valid email address.', false);
            return;
        }
        const $btn = $(this).prop('disabled', true);
        $.ajax({
            url: '/api/approval-settings',
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({ reviewer_email: email }),
            success: function (r) {
                $input.val(r.reviewer_email || '');
                setHint(r.reviewer_email);
                showStatus(r.reviewer_email ? 'Reviewer saved.' : 'Reviewer cleared.', true);
            },
            error: function (xhr) {
                showStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Could not save the reviewer.', false);
            },
            complete: function () { $btn.prop('disabled', false); }
        });
    });
});
