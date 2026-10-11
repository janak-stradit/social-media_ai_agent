// Image Library (templates/library.html): every image the user has made, from
// /api/library/images. An image can be enlarged, downloaded, attached to a new
// post in Studio Chat, or its conversation reopened. Ticking several turns them
// into one post - a carousel - through the Schedule / Send for approval dialog
// (static/js/approval_send.js).
(function () {
    const PAGE = 48;
    const CONVERSATION_KEY = 'avir_active_conversation';  // Studio Chat reopens this conversation (app_v2.js)
    const PENDING_IMAGE_KEY = 'avir_pending_image';        // ...or starts a new post with this image attached
    const state = { images: [], total: 0, query: '', selected: [], loading: false };

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function toast(message) {
        $('#toastBody').text(message);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }

    function dateText(iso) {
        if (!iso) return '';
        // stored in UTC without an offset
        const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
        return isNaN(d) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function tileHtml(img) {
        const n = state.selected.indexOf(img.url);
        const label = img.description || 'Image';
        return `
            <figure class="lib-tile${n >= 0 ? ' is-on' : ''}" data-id="${img.id}">
                <button type="button" class="lib-open" data-open="${img.id}" aria-label="Enlarge: ${esc(label)}">
                    <img src="${esc(img.url)}" alt="${esc(label)}" loading="lazy">
                </button>
                <label class="lib-check" title="Select to make a post or a carousel">
                    <input type="checkbox" data-select="${img.id}" ${n >= 0 ? 'checked' : ''} aria-label="Select: ${esc(label)}">
                    <span>${n >= 0 ? n + 1 : ''}</span>
                </label>
                <figcaption>
                    <span class="lib-desc" title="${esc(label)}">${esc(label)}</span>
                    <span class="lib-meta">${esc(dateText(img.created_at))}${img.width ? ` · ${img.width}×${img.height}` : ''}${img.kind === 'edit' ? ' · edited' : ''}</span>
                </figcaption>
            </figure>`;
    }

    function render() {
        const grid = $('#libGrid');
        if (!state.images.length && !state.loading) {
            grid.html(state.query
                ? `<div class="lib-empty"><div class="card-icon-circle"><i class="fas fa-magnifying-glass"></i></div>
                       <h6>No images match "${esc(state.query)}"</h6><p>Try another word, or clear the search to see everything.</p></div>`
                : `<div class="lib-empty"><div class="card-icon-circle"><i class="fas fa-images"></i></div>
                       <h6>No images yet</h6>
                       <p>Images you create in Studio Chat are kept here: tick <strong>Image</strong> when you write a post, or type <strong>/carousel</strong> for a set of slides.</p>
                       <a class="lib-btn is-primary text-decoration-none" href="/dashboard"><i class="fas fa-comments me-1"></i>Open Studio Chat</a></div>`);
        } else {
            grid.html(state.images.map(tileHtml).join(''));
        }
        $('#libSummary').text(state.total
            ? `${state.total.toLocaleString()} image${state.total === 1 ? '' : 's'}${state.query ? ' matching "' + state.query + '"' : ''}, newest first. Tick images to make a post or a carousel from them.`
            : 'Every image you have created, newest first.');
        $('#libMore').toggleClass('d-none', state.images.length >= state.total);
        renderBar();
    }

    function renderBar() {
        const n = state.selected.length;
        $('#libBar').toggleClass('d-none', !n);
        $('#libBarCount').text(`${n} selected`);
        $('#libBarHint').text(n === 1 ? 'One image: a single-image post.' : n > 20 ? 'A carousel can have at most 20 slides.'
            : `A carousel of ${n} slides, in the order you ticked them.`);
        $('#libApprove').prop('disabled', n > 20);
        $('#libSchedule').prop('disabled', n > 20 || !window.SCHEDULING_ENABLED);
    }

    function load(reset) {
        if (state.loading) return;
        state.loading = true;
        if (reset) { state.images = []; state.total = 0; }
        $('#libMore').prop('disabled', true);
        $.get('/api/library/images', { limit: PAGE, offset: state.images.length, q: state.query })
            .done(function (r) {
                state.images = state.images.concat(r.images || []);
                state.total = r.total || 0;
            })
            .fail(function () { toast('Could not load your images. Please try again.'); })
            .always(function () {
                state.loading = false;
                $('#libMore').prop('disabled', false);
                render();
            });
    }

    // One post from the ticked images: a carousel for LinkedIn and Instagram
    // (Facebook can be ticked in the dialog), or a single image everywhere.
    function postSource() {
        const images = state.selected.slice();
        const many = images.length > 1;
        return {
            key: `library-${Date.now()}`, story: '', conversationId: null, runId: null,
            platforms: ['linkedin', 'instagram', 'facebook'].map(p => ({
                platform: p, caption: '', hashtags: [], images: images, media: many ? 'carousel' : 'single', on: p !== 'facebook',
            })),
        };
    }

    function openViewer(img) {
        const viewer = document.getElementById('libViewer');
        $('#libViewerImg').attr({ src: img.url, alt: img.description || 'Image' });
        $('#libViewerText').text(img.description || '');
        $('#libViewerActions').html(`
            <button type="button" class="lib-btn is-primary" data-use="${esc(img.url)}"><i class="fas fa-plus me-1"></i>Use in a new post</button>
            <a class="lib-btn text-decoration-none" href="${esc(img.url)}" download><i class="fas fa-download me-1"></i>Download</a>
            ${img.conversation_id ? `<button type="button" class="lib-btn" data-conversation="${img.conversation_id}"><i class="fas fa-comments me-1"></i>Open its conversation</button>` : ''}`);
        if (viewer.showModal) viewer.showModal(); else viewer.setAttribute('open', '');
    }

    $(function () {
        let searchTimer = null;
        $('#libSearch').on('input', function () {
            clearTimeout(searchTimer);
            const value = this.value.trim();
            searchTimer = setTimeout(function () {
                if (value === state.query) return;
                state.query = value;
                load(true);
            }, 350);
        });
        $('#libMore').on('click', function () { load(false); });

        $(document).on('click', '[data-open]', function () {
            const img = state.images.find(i => i.id === Number($(this).data('open')));
            if (img) openViewer(img);
        });
        $(document).on('change', '[data-select]', function () {
            const img = state.images.find(i => i.id === Number($(this).data('select')));
            if (!img) return;
            const n = state.selected.indexOf(img.url);
            if (this.checked && n < 0) state.selected.push(img.url);
            if (!this.checked && n >= 0) state.selected.splice(n, 1);
            // the numbers on the other ticked tiles change too
            const focusId = img.id;
            $('#libGrid').html(state.images.map(tileHtml).join(''));
            $(`[data-select="${focusId}"]`).trigger('focus');
            renderBar();
        });
        $('#libClear').on('click', function () { state.selected = []; render(); });
        if (!window.SCHEDULING_ENABLED) {  // "Soon" until STUDIO_SCHEDULING=true (config.py)
            $('#libSchedule').attr('title', 'Coming soon').append('<span class="soon-tag">Soon</span>');
        }
        $('#libSchedule').on('click', function () {
            if (!window.SCHEDULING_ENABLED) return;
            window.openSchedulePost(Object.assign(postSource(), {
                onScheduled: function () { state.selected = []; render(); },
            }));
        });
        $('#libApprove').on('click', function () {
            window.openSendForApproval(Object.assign(postSource(), {
                onSent: function () { state.selected = []; render(); },
            }));
        });

        // In the viewer
        $(document).on('click', '[data-use]', function () {
            try { sessionStorage.removeItem(CONVERSATION_KEY); sessionStorage.setItem(PENDING_IMAGE_KEY, $(this).data('use')); }
            catch (e) { /* storage blocked: Studio Chat opens without the image */ }
            window.location.href = '/dashboard';
        });
        $(document).on('click', '[data-conversation]', function () {
            try { sessionStorage.setItem(CONVERSATION_KEY, String($(this).data('conversation'))); } catch (e) { /* storage blocked */ }
            window.location.href = '/dashboard';
        });

        load(true);
    });
})();
