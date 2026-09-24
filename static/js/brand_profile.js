// "My Brand Configuration" page (templates/brand_profile.html) -
// Individual/Small/Medium accounts only. Shows/edits the per-user
// UserBrandProfile scraped during onboarding (see db.py,
// services/brand_profile_service.py). Self-contained like admin.js - no
// dependency on app_v2.js.
$(document).ready(function () {
    const HEX_COLOR_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
    let initialFormState = '';

    function showToast(msg, type = 'info') {
        const icons = {
            success: '<i data-feather="check-circle" class="text-success me-2"></i>',
            error: '<i data-feather="alert-circle" class="text-danger me-2"></i>',
            warning: '<i data-feather="alert-triangle" class="text-warning me-2"></i>',
            info: '<i data-feather="info" class="text-info me-2"></i>'
        };
        $('#toastBody').html((icons[type] || '') + msg);
        const toastElem = document.getElementById('toast');
        const toast = new bootstrap.Toast(toastElem, { delay: 3000 });
        updateIcons();
        toast.show();
    }

    
    function updateIcons() {
        if (window.feather) {
            feather.replace();
        }
    }
    
    // Copy Hex helper
    window.copyHex = function(hex, elem) {
        navigator.clipboard.writeText(hex);
        const originalHtml = $(elem).html();
        $(elem).html('<span style="color:var(--success-icon);font-size:12px;margin:auto;display:flex;align-items:center;gap:4px;"><i data-feather="check"></i> Copied</span>');
        updateIcons();
        setTimeout(() => { $(elem).html(originalHtml); updateIcons(); }, 1500);
    };

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
                html += `<span class="bp-color-swatch" onclick="copyHex('${escapeHtml(hex)}', this)">
                            <span class="bp-color-dot" style="background:${escapeHtml(hex)};"></span>
                            <span class="bp-color-hex">${escapeHtml(hex)}</span>
                         </span>`;
            }
        });
        $('#bpColorsPreview').html(html);
        updateIcons();
    };

    window.renderFontPreview = function () {
        const lines = linesToList($('#bpFonts').val());
        let html = '';
        lines.forEach(font => {
            const fontName = escapeHtml(font);
            html += `<div class="bp-font-card" style="font-family: '${fontName}', sans-serif;">
                        <div class="bp-font-name">${fontName}</div>
                        <div class="bp-font-sample">Aa Bb Cc</div>
                     </div>`;
        });
        $('#bpFontsPreview').html(html);
    };

    function initTagInput(textareaId) {
        const $textarea = $('#' + textareaId);
        $textarea.hide();

        const $wrapper = $('<div class="ui-tag-input"></div>');
        const $input = $('<input type="text" class="ui-tag-input-field" placeholder="Add item and press Enter...">');
        
        $wrapper.append($input);
        $wrapper.insertAfter($textarea);

        function renderTags() {
            $wrapper.find('.ui-tag').remove();
            const tags = linesToList($textarea.val());
            tags.forEach((tag, index) => {
                const $tag = $(`<div class="ui-tag">
                    <span>${escapeHtml(tag)}</span>
                    <i class="fas fa-times ui-tag-remove" data-index="${index}"></i>
                </div>`);
                $tag.insertBefore($input);
            });
            updateIcons();
        }

        function addTag(val) {
            const trimmed = val.trim();
            if (!trimmed) return;
            const tags = linesToList($textarea.val());
            tags.push(trimmed);
            $textarea.val(tags.join('\n')).trigger('input');
            renderTags();
        }

        function removeTag(index) {
            const tags = linesToList($textarea.val());
            tags.splice(index, 1);
            $textarea.val(tags.join('\n')).trigger('input');
            renderTags();
        }

        $input.on('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault();
                addTag($(this).val());
                $(this).val('');
            } else if (e.key === 'Backspace' && $(this).val() === '') {
                e.preventDefault();
                const tags = linesToList($textarea.val());
                if (tags.length > 0) {
                    removeTag(tags.length - 1);
                }
            }
        });

        $input.on('focus', () => $wrapper.addClass('focus'));
        $input.on('blur', () => {
            $wrapper.removeClass('focus');
            if ($input.val().trim()) {
                addTag($input.val());
                $input.val('');
            }
        });

        $wrapper.on('click', '.ui-tag-remove', function() {
            removeTag($(this).data('index'));
        });

        $wrapper.on('click', function(e) {
            if (e.target === this) {
                $input.focus();
            }
        });

        // Initialize tags on load
        renderTags();

        // Listen for external updates (like populateForm)
        $textarea.on('change.tagInput update.tagInput', function() {
            renderTags();
        });
    }

    
    function initListInput(textareaId, type) {
        const $textarea = $('#' + textareaId);
        $textarea.hide();

        const $wrapper = $('<div class="list-ui-wrapper"></div>');
        $wrapper.insertAfter($textarea);

        function renderList() {
            $wrapper.empty();
            const items = linesToList($textarea.val());
            const icon = type === 'do' ? 'check-circle' : 'x-circle';
            const itemClass = type === 'do' ? 'list-ui-item-do' : 'list-ui-item-dont';

            items.forEach((item, index) => {
                const $item = $(`
                    <div class="list-ui-item ${itemClass}">
                        <i data-feather="${icon}"></i>
                        <input type="text" class="list-ui-input" value="${escapeHtml(item)}" data-index="${index}">
                        <i data-feather="trash-2" class="list-ui-remove" data-index="${index}"></i>
                    </div>
                `);
                $wrapper.append($item);
            });

            $wrapper.append(`
                <div class="list-ui-add" data-type="${type}">
                    <i data-feather="plus"></i> Add rule...
                </div>
            `);
            updateIcons();
        }

        $wrapper.on('input', '.list-ui-input', function() {
            const index = $(this).data('index');
            const items = linesToList($textarea.val());
            items[index] = $(this).val();
            $textarea.val(items.join('\n')).trigger('input');
        });

        $wrapper.on('blur', '.list-ui-input', function() {
            const items = [];
            $wrapper.find('.list-ui-input').each(function() {
                if ($(this).val().trim()) items.push($(this).val().trim());
            });
            $textarea.val(items.join('\n')).trigger('input');
            renderList();
        });

        $wrapper.on('click', '.list-ui-remove', function() {
            const index = $(this).data('index');
            const items = linesToList($textarea.val());
            items.splice(index, 1);
            $textarea.val(items.join('\n')).trigger('input');
            renderList();
        });

        $wrapper.on('click', '.list-ui-add', function() {
            const items = linesToList($textarea.val());
            items.push('New rule');
            $textarea.val(items.join('\n')).trigger('input');
            renderList();
            $wrapper.find('.list-ui-input').last().focus().select();
        });

        renderList();
        $textarea.on('change.listInput update.listInput', function() {
            renderList();
        });
    }

    function populateForm(profile) {
        $('#bpWebsiteUrl').val(profile.website || '');
        if (profile.analyzed_at) {
            $('#bpAnalyzedAt').html(`<span class="status-dot"></span> Last analyzed ${profile.analyzed_at}`);
        } else {
            $('#bpAnalyzedAt').empty();
        }
        $('#bpCompanyName').val(profile.company_name || '');
        $('#bpIndustry').val(profile.industry || '');
        $('#bpTagline').val(profile.tagline || '');
        $('#bpVisualStyle').val(profile.visual_style || '');
        $('#bpTargetAudience').val(profile.target_audience || '');
        $('#bpVoiceSummary').val(profile.brand_voice_summary || '');
        $('#bpKeyThemes').val((profile.key_themes || []).join('\n'));
        $('#bpPrimaryColors').val((profile.primary_colors || []).join('\n'));
        $('#bpFonts').val((profile.fonts || []).join('\n'));
        $('#bpCoreProducts').val((profile.core_products || []).join('\n'));
        $('#bpContentDos').val((profile.content_dos || []).join('\n'));
        $('#bpContentDonts').val((profile.content_donts || []).join('\n'));

        if (profile.logo_url) {
            $('#bpLogoPreview').attr('src', profile.logo_url).removeClass('d-none');
            $('#bpWebsiteIcon').addClass('d-none');
        } else {
            $('#bpLogoPreview').addClass('d-none');
            $('#bpWebsiteIcon').removeClass('d-none');
        }

        renderColorPreview();
        renderFontPreview();
        updateFontsHint();

        // Trigger updates on tag inputs
        $('#bpKeyThemes').trigger('update.tagInput');
        $('#bpCoreProducts').trigger('update.tagInput');
        $('#bpContentDos').trigger('update.listInput');
        $('#bpContentDonts').trigger('update.listInput');

        // Save initial state for dirty tracking
        setTimeout(() => {
            initialFormState = getFormState();
            checkDirtyState();
        }, 50);
    }

    function updateFontsHint() {
        const fonts = $('#bpFonts').val().trim();
        if (!fonts) {
            $('#bpFontsHint').text('No fonts detected from the website.');
        } else {
            $('#bpFontsHint').text("Detected from the site's CSS - one per line.");
        }
    }

    function getFormState() {
        return $('#bpFormWrap .form-control').map(function () { return $(this).val(); }).get().join('||');
    }

    function checkDirtyState() {
        if ($('#bpFormWrap').hasClass('d-none')) return;
        const currentState = getFormState();
        const isDirty = currentState !== initialFormState;

        if (isDirty) {
            $('#bpUnsavedWarning').css('opacity', '1');
        } else {
            $('#bpUnsavedWarning').css('opacity', '0');
        }
    }

    // Bind dirty checking
    $('#bpFormWrap').on('input', '.form-control', function () {
        checkDirtyState();
        if ($(this).attr('id') === 'bpFonts') updateFontsHint();
    });

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
            tagline: $('#bpTagline').val().trim(),
            visual_style: $('#bpVisualStyle').val().trim(),
            target_audience: $('#bpTargetAudience').val().trim(),
            brand_voice_summary: $('#bpVoiceSummary').val().trim(),
            key_themes: linesToList($('#bpKeyThemes').val()),
            primary_colors: linesToList($('#bpPrimaryColors').val()),
            fonts: linesToList($('#bpFonts').val()),
            core_products: linesToList($('#bpCoreProducts').val()),
            content_dos: linesToList($('#bpContentDos').val()),
            content_donts: linesToList($('#bpContentDonts').val())
        };

        $.ajax({
            url: '/api/brand-profile',
            type: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (r) {
                if (r.success) {
                    $btn.prop('disabled', false);
                    showToast('Brand configuration saved!', 'success');
                    initialFormState = getFormState();
                    checkDirtyState();
                } else {
                    $btn.prop('disabled', false);
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
        const website = isEmpty ? $('#bpEmptyWebsiteInput').val().trim() : $('#bpWebsiteUrl').val().trim();

        if (isEmpty && !website) {
            showToast('Enter your website first.', 'warning');
            return;
        }

        const originalHtml = $btn.html();
        $btn.prop('disabled', true).html('<i data-feather="loader" class="fa-spin me-1"></i>Analyzing...');
        updateIcons();

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

    // --- Navigation ScrollSpy ---
    const $bcContent = $('.bc-content');
    const $navItems = $('.bc-nav-item');
    let isClickScrolling = false;

    // Set initial active state
    $navItems.first().addClass('active');

    // Click to scroll
    $navItems.on('click', function (e) {
        e.preventDefault();
        const targetId = $(this).attr('href');
        const $target = $(targetId);

        if ($target.length) {
            isClickScrolling = true;
            $navItems.removeClass('active');
            $(this).addClass('active');

            const scrollTarget = $target.position().top - 30;

            $bcContent.animate({
                scrollTop: scrollTarget
            }, 300, function() {
                setTimeout(() => { isClickScrolling = false; }, 50);
            });
        }
    });

    // Scroll to highlight
    $bcContent.on('scroll', function () {
        if (isClickScrolling) return;
        
        const scrollPos = $bcContent.scrollTop() + 150;
        let bestItem = null;
        let maxTargetTop = -1;

        $navItems.each(function () {
            const targetId = $(this).attr('href');
            const $target = $(targetId);
            
            if ($target.length) {
                const targetTop = $target.position().top;
                if (scrollPos >= targetTop && targetTop > maxTargetTop) {
                    maxTargetTop = targetTop;
                    bestItem = this;
                }
            }
        });

        if (bestItem) {
            $navItems.removeClass('active');
            $(bestItem).addClass('active');
        }
    });

    // Initialize Tag Inputs
    initTagInput('bpKeyThemes');
    initTagInput('bpCoreProducts');
    initListInput('bpContentDos', 'do');
    initListInput('bpContentDonts', 'dont');

    loadBrandProfile();
    setTimeout(updateIcons, 100);
});
