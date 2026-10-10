// Studio Chat "Send for approval": one request for a whole post - a section
// per platform with its caption, hashtags and media (one image, or a carousel
// of slides picked from this conversation's images; LinkedIn carousels as a
// multi-image post or a document/PDF). app_v2.js calls
// window.openSendForApproval(source) with what the post contains:
//   { key, story, conversationId, platforms: [{platform, caption, hashtags, images, media, slideTitles}], onSent(req) }
// The server checks each platform's rules again (services/approval_bundle_service.py).
(function () {
    const NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    const ICONS = { linkedin: 'fa-linkedin', instagram: 'fa-instagram', facebook: 'fa-facebook', youtube: 'fa-youtube' };
    const LIMITS = { instagram: [2, 10], linkedin: [2, 20], facebook: [2, 10] };
    const ORDER = ['linkedin', 'instagram', 'facebook', 'youtube'];

    let state = null;   // { source, sections: {platform: {...}}, images: [...], reviewer }
    let picker = null;  // { platform, multi, chosen: [] }

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function toast(msg, type) {
        const icons = { success: 'fa-check-circle text-success', error: 'fa-exclamation-circle text-danger', warning: 'fa-exclamation-triangle text-warning', info: 'fa-info-circle text-info' };
        $('#toastBody').html(`<i class="fas ${icons[type] || icons.info} me-2"></i>${esc(msg)}`);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }

    function ensureModal() {
        if (document.getElementById('sendApprovalModal')) return;
        $('body').append(`
            <div class="modal fade" id="sendApprovalModal" tabindex="-1" aria-labelledby="sendApprovalTitle" aria-hidden="true">
                <div class="modal-dialog modal-lg modal-dialog-scrollable modal-dialog-centered">
                    <div class="modal-content sa-modal">
                        <div class="modal-header">
                            <div>
                                <h5 class="modal-title" id="sendApprovalTitle"><i class="fas fa-paper-plane me-2"></i>Send for approval</h5>
                                <div class="sa-reviewer" id="saReviewer">Loading reviewer...</div>
                            </div>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body" id="saBody"></div>
                        <div class="modal-footer">
                            <div class="sa-footer-note" id="saFooterNote"></div>
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-sm btn-primary" id="saSubmit"><i class="fas fa-paper-plane me-1"></i>Send for approval</button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal fade" id="saPickerModal" tabindex="-1" aria-labelledby="saPickerTitle" aria-hidden="true">
                <div class="modal-dialog modal-lg modal-dialog-scrollable modal-dialog-centered">
                    <div class="modal-content sa-modal">
                        <div class="modal-header">
                            <h5 class="modal-title" id="saPickerTitle">Choose images</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body"><div class="sa-picker-hint" id="saPickerHint"></div><div class="sa-picker-grid" id="saPickerGrid"></div></div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-sm btn-primary" id="saPickerDone">Use these</button>
                        </div>
                    </div>
                </div>
            </div>`);
    }

    // ── Rendering ────────────────────────────────────────────────────────
    function problems(platform, sec) {
        const out = [];
        if (sec.media !== 'none' && !sec.images.length) out.push('Pick an image.');
        if (platform === 'instagram' && (sec.media === 'none' || !sec.images.length)) out.push('Instagram posts need an image.');
        if (sec.media === 'carousel') {
            const [low, high] = LIMITS[platform] || [2, 10];
            if (sec.images.length < low || sec.images.length > high) out.push(`${NAMES[platform]} carousels need ${low}-${high} slides (now ${sec.images.length}).`);
            if (platform === 'instagram') {
                const ratios = sec.images.map(u => (state.dims[u] || {}).ratio).filter(Boolean);
                if (ratios.length > 1 && Math.max(...ratios) - Math.min(...ratios) > 0.02 * Math.max(...ratios)) {
                    out.push('Instagram carousels need every slide in the same shape.');
                }
            }
        }
        if (platform === 'instagram' && sec.hashtags.length > 30) out.push('Instagram allows at most 30 hashtags.');
        if (!sec.caption.trim() && !sec.images.length) out.push('Add a caption or an image.');
        return out;
    }

    function slidesHtml(platform, sec) {
        if (sec.media === 'none') return '';
        const tiles = sec.images.map((url, i) => `
            <div class="sa-slide">
                <img src="${esc(url)}" alt="${sec.media === 'carousel' ? 'Slide ' + (i + 1) : 'Post image'}">
                ${sec.media === 'carousel' ? `<span class="sa-slide-num">${i + 1}</span>
                <div class="sa-slide-tools">
                    <button type="button" data-move="-1" data-platform="${platform}" data-i="${i}" ${i === 0 ? 'disabled' : ''} title="Move left"><i class="fas fa-arrow-left"></i></button>
                    <button type="button" data-move="1" data-platform="${platform}" data-i="${i}" ${i === sec.images.length - 1 ? 'disabled' : ''} title="Move right"><i class="fas fa-arrow-right"></i></button>
                    <button type="button" data-remove="${i}" data-platform="${platform}" title="Remove"><i class="fas fa-xmark"></i></button>
                </div>` : ''}
            </div>`).join('');
        const add = `<button type="button" class="sa-slide sa-slide-add" data-pick="${platform}">
                <i class="fas ${sec.media === 'carousel' ? 'fa-plus' : 'fa-image'}"></i><span>${sec.media === 'carousel' ? 'Add slides' : (sec.images.length ? 'Change image' : 'Pick image')}</span></button>`;
        return `<div class="sa-slides">${tiles}${add}</div>`;
    }

    function sectionHtml(platform) {
        const sec = state.sections[platform];
        const issues = sec.on ? problems(platform, sec) : [];
        const mediaChoice = (value, label) => `<label class="sa-pill${sec.media === value ? ' on' : ''}">
            <input type="radio" name="saMedia_${platform}" value="${value}" ${sec.media === value ? 'checked' : ''} data-media="${platform}"> ${label}</label>`;
        return `
            <section class="sa-section${sec.on ? '' : ' off'}">
                <label class="sa-section-head">
                    <input type="checkbox" class="form-check-input" data-include="${platform}" ${sec.on ? 'checked' : ''}>
                    <i class="fab ${ICONS[platform]} sa-icon-${platform}"></i><strong>${NAMES[platform]}</strong>
                    ${sec.on && !issues.length ? '<span class="sa-ok"><i class="fas fa-check"></i> Ready</span>' : ''}
                </label>
                ${sec.on ? `
                <div class="sa-media-row">
                    ${platform === 'instagram' ? '' : mediaChoice('none', 'Text only')}
                    ${mediaChoice('single', 'One image')}
                    ${platform === 'youtube' ? '' : mediaChoice('carousel', 'Carousel')}
                </div>
                ${slidesHtml(platform, sec)}
                ${platform === 'linkedin' && sec.media === 'carousel' ? `
                <div class="sa-format">
                    <span>LinkedIn format:</span>
                    <label class="sa-pill${sec.format === 'multi_image' ? ' on' : ''}"><input type="radio" name="saFmt" value="multi_image" data-format ${sec.format === 'multi_image' ? 'checked' : ''}> Multi-image post</label>
                    <label class="sa-pill${sec.format === 'document' ? ' on' : ''}"><input type="radio" name="saFmt" value="document" data-format ${sec.format === 'document' ? 'checked' : ''}> Document carousel (PDF)</label>
                </div>
                <div class="sa-help">${sec.format === 'document'
                    ? 'The slides become a PDF people swipe through (LinkedIn\'s most popular carousel). The reviewer gets the PDF.'
                    : 'A post with several images - LinkedIn shows them as a grid that opens into a swipeable view.'}</div>` : ''}
                <label class="sa-label" for="saCaption_${platform}">Caption</label>
                <textarea class="form-control form-control-sm" id="saCaption_${platform}" rows="4" data-caption="${platform}">${esc(sec.caption)}</textarea>
                <label class="sa-label" for="saTags_${platform}">Hashtags</label>
                <input class="form-control form-control-sm" id="saTags_${platform}" data-tags="${platform}" value="${esc(sec.hashtags.join(' '))}" placeholder="#marketing #launch">
                ${issues.length ? `<div class="sa-issues">${issues.map(t => `<div><i class="fas fa-triangle-exclamation me-1"></i>${esc(t)}</div>`).join('')}</div>` : ''}
                ` : ''}
            </section>`;
    }

    function render() {
        const platforms = ORDER.filter(p => state.sections[p]);
        $('#saBody').html(platforms.map(sectionHtml).join(''));
        const on = platforms.filter(p => state.sections[p].on);
        const blocked = !state.reviewer || !on.length || on.some(p => problems(p, state.sections[p]).length);
        $('#saSubmit').prop('disabled', blocked);
        $('#saFooterNote').text(!on.length ? 'Choose at least one platform.'
            : blocked && state.reviewer ? 'Fix the notes above to send.' : `${on.length} platform${on.length > 1 ? 's' : ''} in one email.`);
    }

    function renderReviewer() {
        if (state.reviewer) {
            $('#saReviewer').html(`Goes to <strong>${esc(state.reviewer)}</strong> <button type="button" class="sa-link" id="saChangeReviewer">change</button>`);
        } else {
            $('#saReviewer').html(`<span class="text-danger">Who should approve it?</span>
                <span class="sa-reviewer-form"><input type="email" class="form-control form-control-sm" id="saReviewerInput" placeholder="reviewer@company.com" aria-label="Reviewer email">
                <button type="button" class="btn btn-sm btn-outline-primary" id="saSaveReviewer">Save</button></span>`);
        }
    }

    // Image shapes, for Instagram's "same shape" rule
    function measure(urls) {
        urls.filter(u => !state.dims[u]).forEach(u => {
            const img = new Image();
            img.onload = () => { state.dims[u] = { ratio: img.naturalWidth / img.naturalHeight }; render(); };
            img.src = u;
        });
    }

    // ── Picker: images from this conversation ────────────────────────────
    function openPicker(platform) {
        const sec = state.sections[platform];
        picker = { platform, multi: sec.media === 'carousel', chosen: sec.media === 'carousel' ? sec.images.slice() : [] };
        $('#saPickerTitle').text(picker.multi ? `Carousel slides for ${NAMES[platform]}` : `Image for ${NAMES[platform]}`);
        const [low, high] = LIMITS[platform] || [2, 10];
        $('#saPickerHint').text(picker.multi ? `Click images in the order they should appear (${low}-${high} slides).` : 'Click the image to use.');
        renderPicker();
        bootstrap.Modal.getOrCreateInstance(document.getElementById('saPickerModal')).show();
    }

    function renderPicker() {
        const images = state.images;
        $('#saPickerGrid').html(images.length ? images.map(img => {
            const n = picker.chosen.indexOf(img.url);
            return `<button type="button" class="sa-pick${n >= 0 ? ' on' : ''}" data-url="${esc(img.url)}">
                <img src="${esc(img.url)}" alt="${esc(img.description || 'Image')}" loading="lazy">
                ${n >= 0 ? `<span class="sa-pick-num">${picker.multi ? n + 1 : '<i class="fas fa-check"></i>'}</span>` : ''}
            </button>`;
        }).join('') : '<div class="text-muted small">No images in this conversation yet - create some first (for example with /carousel).</div>');
    }

    function loadImages() {
        const own = [];
        Object.values(state.sections).forEach(sec => sec.images.forEach(u => { if (!own.includes(u)) own.push(u); }));
        state.images = own.map(u => ({ url: u }));
        if (!state.source.conversationId) return;
        $.get(`/api/conversations/${state.source.conversationId}/images`).done(r => {
            (r.images || []).forEach(img => { if (!state.images.some(i => i.url === img.url)) state.images.push(img); });
            if (picker) renderPicker();
        });
    }

    // ── Events ───────────────────────────────────────────────────────────
    function bindOnce() {
        if (bindOnce.done) return;
        bindOnce.done = true;
        const $doc = $(document);
        $doc.on('change', '[data-include]', function () { state.sections[$(this).data('include')].on = this.checked; render(); });
        $doc.on('change', '[data-media]', function () {
            const sec = state.sections[$(this).data('media')];
            sec.media = this.value;
            if (sec.media === 'single') sec.images = sec.images.slice(0, 1);
            if (sec.media === 'none') sec.images = [];
            if (sec.media !== 'none' && !sec.images.length && sec.lastImages.length) sec.images = sec.media === 'single' ? sec.lastImages.slice(0, 1) : sec.lastImages.slice();
            render();
        });
        $doc.on('change', '[data-format]', function () { state.sections.linkedin.format = this.value; render(); });
        $doc.on('input', '[data-caption]', function () { state.sections[$(this).data('caption')].caption = this.value; });
        $doc.on('change', '[data-caption]', render);
        $doc.on('change', '[data-tags]', function () {
            state.sections[$(this).data('tags')].hashtags = this.value.split(/[\s,]+/).filter(Boolean).map(t => t.startsWith('#') ? t : '#' + t);
            render();
        });
        $doc.on('click', '[data-move]', function () {
            const sec = state.sections[$(this).data('platform')], i = Number($(this).data('i')), j = i + Number($(this).data('move'));
            [sec.images[i], sec.images[j]] = [sec.images[j], sec.images[i]];
            render();
        });
        $doc.on('click', '[data-remove]', function () {
            state.sections[$(this).data('platform')].images.splice(Number($(this).data('remove')), 1);
            render();
        });
        $doc.on('click', '[data-pick]', function () { openPicker($(this).data('pick')); });
        $doc.on('click', '.sa-pick', function () {
            const url = $(this).data('url');
            const n = picker.chosen.indexOf(url);
            if (picker.multi) { if (n >= 0) picker.chosen.splice(n, 1); else picker.chosen.push(url); }
            else picker.chosen = [url];
            renderPicker();
        });
        $doc.on('click', '#saPickerDone', function () {
            const sec = state.sections[picker.platform];
            sec.images = picker.chosen.slice();
            sec.lastImages = sec.images.slice();
            measure(sec.images);
            bootstrap.Modal.getOrCreateInstance(document.getElementById('saPickerModal')).hide();
            render();
        });
        $doc.on('click', '#saChangeReviewer', function () { state.reviewer = null; renderReviewer(); render(); });
        $doc.on('click', '#saSaveReviewer', function () {
            const email = ($('#saReviewerInput').val() || '').trim();
            $.ajax({ url: '/api/approval-settings', type: 'PUT', contentType: 'application/json', data: JSON.stringify({ reviewer_email: email }) })
                .done(r => { state.reviewer = r.reviewer_email; renderReviewer(); render(); })
                .fail(xhr => toast((xhr.responseJSON || {}).error || 'Could not save the reviewer.', 'error'));
        });
        $doc.on('click', '#saSubmit', submit);
    }

    function submit() {
        const items = ORDER.filter(p => state.sections[p] && state.sections[p].on).map(p => {
            const sec = state.sections[p];
            const item = { platform: p, caption: sec.caption, hashtags: sec.hashtags, media: sec.media, images: sec.images,
                           slide_titles: sec.images.map(u => sec.titles[u] || '') };
            if (p === 'linkedin' && sec.media === 'carousel') item.linkedin_format = sec.format;
            return item;
        });
        const $btn = $('#saSubmit').prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Sending...');
        $.ajax({
            url: '/api/approval-requests', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ pipeline_client_id: state.source.key, story: state.source.story || '', items: items }),
        }).done(r => {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('sendApprovalModal')).hide();
            toast(r.email && r.email.success ? `Sent to ${state.reviewer} for approval.` : 'Request saved, but the email could not be sent - the reviewer can open it from Approval Requests.',
                  r.email && r.email.success ? 'success' : 'warning');
            if (state.source.onSent) state.source.onSent(r.request);
        }).fail(xhr => {
            toast((xhr.responseJSON || {}).error || 'Could not send for approval.', 'error');
        }).always(() => $btn.html('<i class="fas fa-paper-plane me-1"></i>Send for approval') && render());
    }

    window.openSendForApproval = function (source) {
        ensureModal();
        bindOnce();
        const sections = {};
        (source.platforms || []).forEach(p => {
            if (!NAMES[p.platform]) return;
            const images = (p.images || []).filter(Boolean);
            const titles = {};
            images.forEach((u, i) => { titles[u] = (p.slideTitles || [])[i] || ''; });
            sections[p.platform] = {
                on: p.on !== false, caption: p.caption || '', hashtags: p.hashtags || [], images: images, lastImages: images.slice(),
                media: p.media || (images.length > 1 ? 'carousel' : images.length ? 'single' : (p.platform === 'instagram' ? 'single' : 'none')),
                format: 'multi_image', titles: titles,
            };
        });
        state = { source, sections, images: [], reviewer: null, dims: {} };
        loadImages();
        measure(Object.values(sections).flatMap(s => s.images));
        render();
        $('#saReviewer').text('Loading reviewer...');
        $.get('/api/approval-settings').done(r => {
            state.reviewer = r.reviewer_email || r.default_reviewer_email || null;
            renderReviewer();
            render();
        });
        bootstrap.Modal.getOrCreateInstance(document.getElementById('sendApprovalModal')).show();
    };
})();
