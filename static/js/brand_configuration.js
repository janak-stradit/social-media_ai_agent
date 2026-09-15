// Standalone Brand Configuration page (/brand-configuration) - structured
// Content Guidelines (Colors/Typography/Voice & Tone/Content Rules/Imagery
// Style/Persona Rules/Messaging), Products & Service, and Logo & Character
// assets. All stored server-side (see /api/settings/<key>, /api/brand-assets/
// <key>) and read live by generation, so edits here actually change output.
$(document).ready(function () {

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function showToast(message, type) {
        const bgClass = type === 'success' ? 'bg-success' : type === 'danger' ? 'bg-danger' : 'bg-primary';
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

    window.switchBrandConfigTab = function (tab) {
        $('.bc-nav-item').removeClass('active');
        $(`.bc-nav-item[data-tab="${tab}"]`).addClass('active');
        $('[id^="brandConfigPane"]').addClass('d-none');
        $(`#brandConfigPane${tab.charAt(0).toUpperCase() + tab.slice(1)}`).removeClass('d-none');
    };

    // ── Structured Content Guidelines ───────────────────────────────────────
    // Each section maps 1:1 to a key in the JSON stored under the
    // "content_guidelines" setting (see services/stradit_service.py's
    // DEFAULT_CONTENT_GUIDELINES and agents/story_agent.py's
    // _build_guidelines_block, which turns this same structure into the
    // actual prompt text).
    const GUIDELINE_SCHEMA = [
        {
            key: 'colors', label: 'Colors', icon: 'fa-palette',
            fields: [
                { key: 'primary', label: 'Primary Color', type: 'color-pair', nameKey: 'primary_name' },
                { key: 'secondary', label: 'Secondary Color', type: 'color-pair', nameKey: 'secondary_name' },
                { key: 'accent', label: 'Accent Color', type: 'color-pair', nameKey: 'accent_name' },
                { key: 'usage_notes', label: 'Usage Notes', type: 'textarea' },
            ],
        },
        {
            key: 'typography', label: 'Typography', icon: 'fa-font',
            fields: [
                { key: 'font_family', label: 'Font Family', type: 'text' },
                { key: 'heading_style', label: 'Heading Style', type: 'text' },
                { key: 'body_style', label: 'Body Style', type: 'text' },
                { key: 'restrictions', label: 'Restrictions', type: 'textarea' },
            ],
        },
        {
            key: 'voice_tone', label: 'Voice & Tone', icon: 'fa-comment-dots',
            fields: [
                { key: 'descriptors', label: 'Tone Descriptors', type: 'text' },
                { key: 'formality', label: 'Formality Level', type: 'text' },
                { key: 'jargon_policy', label: 'Jargon Policy', type: 'textarea' },
                { key: 'avoid_words', label: 'Words/Phrases to Avoid', type: 'textarea' },
                { key: 'key_terms', label: 'Preferred Terms', type: 'textarea' },
            ],
        },
        {
            key: 'content_rules', label: 'Content Rules', icon: 'fa-list-check',
            fields: [
                { key: 'caption_length', label: 'Caption Length', type: 'text' },
                { key: 'hashtag_policy', label: 'Hashtag Policy', type: 'text' },
                { key: 'emoji_policy', label: 'Emoji Policy', type: 'text' },
                { key: 'cta_style', label: 'Call-to-Action Style', type: 'text' },
            ],
        },
        {
            key: 'imagery_style', label: 'Imagery Style', icon: 'fa-image',
            fields: [
                { key: 'aesthetic', label: 'Aesthetic', type: 'textarea' },
                { key: 'avoid', label: 'Avoid', type: 'textarea' },
            ],
        },
        {
            key: 'persona_rules', label: 'Character/Persona Rules', icon: 'fa-user-tie',
            fields: [
                { key: 'clothing', label: 'Clothing', type: 'text' },
                { key: 'demeanor', label: 'Demeanor', type: 'text' },
                { key: 'consistency', label: 'Consistency', type: 'text' },
            ],
        },
        {
            key: 'messaging', label: 'Messaging', icon: 'fa-bullhorn',
            fields: [
                { key: 'tagline', label: 'Official Tagline', type: 'text' },
                { key: 'value_props', label: 'Key Value Props', type: 'textarea' },
                { key: 'prohibited_claims', label: 'Prohibited Claims/Terms', type: 'textarea' },
            ],
        },
    ];

    function fieldInputHtml(sectionKey, field) {
        const id = `g_${sectionKey}_${field.key}`;
        if (field.type === 'color-pair') {
            const nameId = `g_${sectionKey}_${field.nameKey}`;
            return `
                <label class="guideline-field-label">${field.label}</label>
                <div class="d-flex gap-2 mb-2">
                    <input type="color" class="form-control form-control-color color-pair-input" id="${id}">
                    <input type="text" class="form-control form-control-sm" id="${nameId}" placeholder="Name (e.g. Strad Orange)">
                </div>
            `;
        }
        if (field.type === 'textarea') {
            return `
                <label class="guideline-field-label" for="${id}">${field.label}</label>
                <textarea class="form-control form-control-sm mb-2" id="${id}" rows="2"></textarea>
            `;
        }
        return `
            <label class="guideline-field-label" for="${id}">${field.label}</label>
            <input type="text" class="form-control form-control-sm mb-2" id="${id}">
        `;
    }

    function renderGuidelinesForm() {
        // #guidelinesFormContainer is itself a Bootstrap .row (see
        // brand_configuration.html), so each section becomes its own
        // responsive column + card - a 2-column grid on wide screens instead
        // of one long stacked list, making better use of the full-width layout.
        const html = GUIDELINE_SCHEMA.map((section) => `
            <div class="col-lg-6">
                <div class="guideline-card">
                    <h6 class="fw-bold text-dark mb-3"><i class="fas ${section.icon} text-primary me-2"></i>${section.label}</h6>
                    <div class="row g-2">
                        ${section.fields.map((f) => `<div class="col-md-6">${fieldInputHtml(section.key, f)}</div>`).join('')}
                    </div>
                </div>
            </div>
        `).join('');
        $('#guidelinesFormContainer').html(html);
    }

    function populateGuidelinesForm(data) {
        GUIDELINE_SCHEMA.forEach((section) => {
            const sectionData = data[section.key] || {};
            section.fields.forEach((f) => {
                const id = `g_${section.key}_${f.key}`;
                $(`#${id}`).val(sectionData[f.key] || (f.type === 'color-pair' ? '#4f46e5' : ''));
                if (f.type === 'color-pair') {
                    $(`#g_${section.key}_${f.nameKey}`).val(sectionData[f.nameKey] || '');
                }
            });
        });
    }

    function collectGuidelinesForm() {
        const data = {};
        GUIDELINE_SCHEMA.forEach((section) => {
            const sectionData = {};
            section.fields.forEach((f) => {
                sectionData[f.key] = $(`#g_${section.key}_${f.key}`).val() || '';
                if (f.type === 'color-pair') {
                    sectionData[f.nameKey] = $(`#g_${section.key}_${f.nameKey}`).val() || '';
                }
            });
            data[section.key] = sectionData;
        });
        return data;
    }

    window.saveGuidelines = function () {
        const btn = $('#saveGuidelinesBtn');
        const originalHtml = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Saving...');

        $.ajax({
            url: '/api/settings/content_guidelines',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ value: JSON.stringify(collectGuidelinesForm()) }),
            success: function (r) {
                btn.prop('disabled', false).html(originalHtml);
                if (r.success) {
                    showToast('Saved - future generations will use this.', 'success');
                } else {
                    showToast(r.error || 'Failed to save.', 'danger');
                }
            },
            error: function (xhr) {
                btn.prop('disabled', false).html(originalHtml);
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error saving.', 'danger');
            }
        });
    };

    function loadGuidelines() {
        renderGuidelinesForm();
        $.ajax({
            url: '/api/settings/content_guidelines',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                try {
                    populateGuidelinesForm(JSON.parse(r.value));
                } catch (e) {
                    // Legacy freeform text saved before the structured format existed -
                    // nothing to populate into the structured fields.
                    console.warn('Could not parse stored guidelines as structured JSON:', e);
                }
            },
            error: function () {
                showToast('Could not load guidelines.', 'danger');
            }
        });
    }

    // ── Products & Service (plain text) ─────────────────────────────────────
    function loadAppSetting(key, textareaId) {
        $.ajax({
            url: `/api/settings/${key}`,
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    $(`#${textareaId}`).val(r.value);
                }
            },
            error: function () {
                showToast(`Could not load ${key}.`, 'danger');
            }
        });
    }

    window.saveAppSetting = function (key, textareaId, btnId) {
        const value = $(`#${textareaId}`).val();
        const btn = $(`#${btnId}`);
        const originalHtml = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Saving...');

        $.ajax({
            url: `/api/settings/${key}`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ value: value }),
            success: function (r) {
                btn.prop('disabled', false).html(originalHtml);
                if (r.success) {
                    showToast('Saved - future generations will use this.', 'success');
                } else {
                    showToast(r.error || 'Failed to save.', 'danger');
                }
            },
            error: function (xhr) {
                btn.prop('disabled', false).html(originalHtml);
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error saving.', 'danger');
            }
        });
    };

    // ── Logo & Character assets (extensible list, not a fixed pair) ────────
    function renderBrandAssetsGrid(assets) {
        const cardsHtml = assets.map((a) => `
            <div class="col-md-6 col-xl-4" data-asset-key="${a.key}">
                <div class="brand-asset-card">
                    <img src="${a.url}" class="brand-asset-preview" alt="${escapeHtml(a.label)}">
                    <h6 class="fw-bold mb-2">${escapeHtml(a.label)}</h6>
                    <input type="file" class="form-control form-control-sm mb-2 brand-asset-file-input" accept="image/*">
                    <div class="d-flex gap-2">
                        <button class="btn btn-sm btn-primary fw-bold rounded-pill px-3 flex-grow-1" onclick="uploadBrandAsset('${a.key}', this)">
                            <i class="fas fa-upload me-1"></i>Replace
                        </button>
                        <button class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="deleteBrandAsset('${a.key}', '${escapeHtml(a.label).replace(/'/g, "\\'")}')" title="Remove">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        `).join('');

        const addCardHtml = `
            <div class="col-md-6 col-xl-4">
                <div class="brand-asset-card" style="border-style: dashed;">
                    <div class="brand-asset-preview d-flex align-items-center justify-content-center text-muted">
                        <i class="fas fa-plus fa-2x opacity-50"></i>
                    </div>
                    <input type="text" class="form-control form-control-sm mb-2" id="newAssetLabel" placeholder="Name (e.g. Marcus - Support Persona)">
                    <input type="file" class="form-control form-control-sm mb-2" id="newAssetFile" accept="image/*">
                    <button class="btn btn-sm btn-success fw-bold rounded-pill px-3 w-100" onclick="createBrandAsset()">
                        <i class="fas fa-plus me-1"></i>Add New
                    </button>
                </div>
            </div>
        `;

        $('#brandAssetsGrid').html(cardsHtml + addCardHtml);
    }

    function loadBrandAssets() {
        $.ajax({
            url: '/api/brand-assets',
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    renderBrandAssetsGrid(r.assets || []);
                } else {
                    showToast(r.error || 'Could not load brand assets.', 'danger');
                }
            },
            error: function () {
                showToast('Could not load brand assets.', 'danger');
            }
        });
    }

    window.uploadBrandAsset = function (key, btnEl) {
        const card = $(btnEl).closest('.brand-asset-card');
        const file = card.find('.brand-asset-file-input')[0].files[0];
        if (!file) {
            showToast('Choose a file first.', 'warning');
            return;
        }
        const formData = new FormData();
        formData.append('image', file);

        $.ajax({
            url: `/api/brand-assets/${key}`,
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function (r) {
                if (r.success) {
                    card.find('img').attr('src', r.url);
                    showToast('Uploaded - used everywhere this character/logo is selected.', 'success');
                } else {
                    showToast(r.error || 'Upload failed.', 'danger');
                }
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error uploading.', 'danger');
            }
        });
    };

    window.createBrandAsset = function () {
        const label = $('#newAssetLabel').val().trim();
        const file = document.getElementById('newAssetFile').files[0];
        if (!label) {
            showToast('Give the new character/logo a name first.', 'warning');
            return;
        }
        if (!file) {
            showToast('Choose a file first.', 'warning');
            return;
        }
        const formData = new FormData();
        formData.append('label', label);
        formData.append('image', file);

        $.ajax({
            url: '/api/brand-assets',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function (r) {
                if (r.success) {
                    showToast(`"${label}" added - it now shows up in Character Setup.`, 'success');
                    loadBrandAssets();
                } else {
                    showToast(r.error || 'Failed to add.', 'danger');
                }
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error adding.', 'danger');
            }
        });
    };

    window.deleteBrandAsset = function (key, label) {
        if (!window.confirm(`Remove "${label}"? This can't be undone.`)) return;

        $.ajax({
            url: `/api/brand-assets/${key}`,
            type: 'DELETE',
            success: function (r) {
                if (r.success) {
                    showToast(`"${label}" removed.`, 'success');
                    loadBrandAssets();
                } else {
                    showToast(r.error || 'Failed to remove.', 'danger');
                }
            },
            error: function (xhr) {
                showToast((xhr.responseJSON && xhr.responseJSON.error) || 'Network error removing.', 'danger');
            }
        });
    };

    loadGuidelines();
    loadAppSetting('products_services', 'servicesTextarea');
    loadBrandAssets();
});
