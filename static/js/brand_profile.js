// "My Brand Configuration" page (templates/brand_profile.html) -
// Individual/Small/Medium accounts only. Shows/edits the per-user
// UserBrandProfile scraped during onboarding (see db.py,
// services/brand_profile_service.py). Self-contained like admin.js - no
// dependency on app_v2.js.
$(document).ready(function () {
    const HEX_COLOR_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

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

    function linesToList(text) {
        return (text || '').split('\n').map(s => s.trim()).filter(Boolean);
    }

    window.renderColorPreview = function () {
        const lines = linesToList($('#bpPrimaryColors').val());
        let html = '';
        lines.forEach(hex => {
            if (HEX_COLOR_RE.test(hex)) {
                html += `<span class="bp-color-swatch"><span class="bp-color-dot" style="background:${escapeHtml(hex)};"></span>${escapeHtml(hex)}</span>`;
            }
        });
        $('#bpColorsPreview').html(html);
    };

    function populateForm(profile) {
        $('#bpWebsiteUrl').text(profile.website || 'No website on file');
        $('#bpAnalyzedAt').text(profile.analyzed_at ? `Last analyzed ${profile.analyzed_at}` : '');
        $('#bpCompanyName').val(profile.company_name || '');
        $('#bpIndustry').val(profile.industry || '');
        $('#bpTargetAudience').val(profile.target_audience || '');
        $('#bpVoiceSummary').val(profile.brand_voice_summary || '');
        $('#bpKeyThemes').val((profile.key_themes || []).join('\n'));
        $('#bpPrimaryColors').val((profile.primary_colors || []).join('\n'));
        $('#bpContentDos').val((profile.content_dos || []).join('\n'));
        $('#bpContentDonts').val((profile.content_donts || []).join('\n'));
        renderColorPreview();
    }

    function loadBrandProfile() {
        $('#bpLoading').removeClass('d-none');
        $('#bpEmptyState').addClass('d-none');
        $('#bpFormWrap').addClass('d-none');

        $.ajax({
            url: '/api/brand-profile',
            type: 'GET',
            success: function (r) {
                $('#bpLoading').addClass('d-none');
                if (!r.success) {
                    showToast(r.error || 'Failed to load brand profile.', 'error');
                    return;
                }
                if (!r.profile) {
                    $('#bpEmptyState').removeClass('d-none');
                    return;
                }
                populateForm(r.profile);
                $('#bpFormWrap').removeClass('d-none');
            },
            error: function (xhr) {
                $('#bpLoading').addClass('d-none');
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to load brand profile.', 'error');
            }
        });
    }

    window.saveBrandProfile = function () {
        const $btn = $('#bpSaveBtn');
        $btn.prop('disabled', true);

        const payload = {
            company_name: $('#bpCompanyName').val().trim(),
            industry: $('#bpIndustry').val().trim(),
            target_audience: $('#bpTargetAudience').val().trim(),
            brand_voice_summary: $('#bpVoiceSummary').val().trim(),
            key_themes: linesToList($('#bpKeyThemes').val()),
            primary_colors: linesToList($('#bpPrimaryColors').val()),
            content_dos: linesToList($('#bpContentDos').val()),
            content_donts: linesToList($('#bpContentDonts').val())
        };

        $.ajax({
            url: '/api/brand-profile',
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (r) {
                $btn.prop('disabled', false);
                if (r.success) {
                    showToast('Brand configuration saved!', 'success');
                    loadBrandProfile();
                } else {
                    showToast(r.error || 'Failed to save.', 'error');
                }
            },
            error: function (xhr) {
                $btn.prop('disabled', false);
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to save.', 'error');
            }
        });
    };

    window.rescanBrandProfile = function (source) {
        const isEmpty = source === 'empty';
        const $btn = isEmpty ? $('#bpEmptyAnalyzeBtn') : $('#bpRescanBtn');
        const website = isEmpty ? $('#bpEmptyWebsiteInput').val().trim() : undefined;

        if (isEmpty && !website) {
            showToast('Enter your website first.', 'warning');
            return;
        }

        const originalHtml = $btn.html();
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Analyzing...');

        $.ajax({
            url: '/api/brand-profile/rescan',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(website ? { website } : {}),
            success: function (r) {
                $btn.prop('disabled', false).html(originalHtml);
                if (r.success) {
                    showToast('Website analyzed!', 'success');
                    loadBrandProfile();
                } else {
                    showToast(r.error || 'Analysis failed.', 'error');
                }
            },
            error: function (xhr) {
                $btn.prop('disabled', false).html(originalHtml);
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Analysis failed.', 'error');
            }
        });
    };

    loadBrandProfile();
});
