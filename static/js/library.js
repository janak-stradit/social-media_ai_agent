// Image Library (templates/library.html). Three views, kept in the address so
// Back works:
//   (no hash)   Campaigns - a folder per Studio Chat conversation that made images
//   #c=<id>     one campaign's images ("none" = images made outside a conversation)
//   #all        every image;  #favorites  the starred ones
// An image is one tile however often it was edited (its newest version; the
// viewer shows the others). Ticked images go into the carousel tray, where they
// can be reordered and made into one post through the Schedule / Send for
// approval dialog (static/js/approval_send.js). Deleting offers Undo first
// (static/js/undo.js).
(function () {
    const PAGE = 48;
    const MAX_SLIDES = 20;
    const CONVERSATION_KEY = 'avir_active_conversation';  // Studio Chat reopens this conversation (app_v2.js)
    const PENDING_IMAGE_KEY = 'avir_pending_image';        // ...or starts a new post with this image attached
    const state = {
        view: 'folders',      // folders | folder | all | favorites
        folder: null,         // {conversation_id, title, count} inside a campaign
        folders: [],
        images: [], total: 0, query: '', loading: false,
        selected: [],         // [{id, url}] in carousel order
        viewing: null,        // the image open in the viewer
    };
    let request = null;

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
    function shapeText(img) {
        if (!img.width || !img.height) return '';
        const r = img.width / img.height;
        return r > 1.15 ? 'Landscape' : r < 0.87 ? 'Portrait' : 'Square';
    }
    const PLATFORMS = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube' };
    const selectedIndex = img => state.selected.findIndex(s => s.id === img.id);

    // ── Where we are: read and write the address ─────────────────────────
    function readHash() {
        const h = window.location.hash.replace(/^#/, '');
        const m = /^c=(none|\d+)$/.exec(h);
        if (m) return { view: 'folder', conversation: m[1] === 'none' ? 'none' : Number(m[1]) };
        if (h === 'all' || h === 'favorites') return { view: h };
        return { view: 'folders' };
    }
    function go(hash) {
        if (window.location.hash.replace(/^#/, '') === hash) route(); else window.location.hash = hash;
    }
    function route() {
        const where = readHash();
        state.view = where.view;
        if (where.view === 'folder') {
            const known = state.folders.find(f => String(f.conversation_id == null ? 'none' : f.conversation_id) === String(where.conversation));
            state.folder = known || { conversation_id: where.conversation === 'none' ? null : where.conversation, title: 'Campaign', count: null };
            if (!known) {  // opened from the address (a refresh, Back): find its name
                const folder = state.folder;
                $.get('/api/library/folders').done(function (r) {
                    state.folders = r.folders || [];
                    const found = state.folders.find(f => f.conversation_id === folder.conversation_id);
                    if (found && state.folder === folder) { folder.title = found.title; folder.count = found.count; renderCrumb(); }
                });
            }
        } else {
            state.folder = null;
        }
        $('.lib-view').each(function () {
            const on = $(this).data('lib-view') === (where.view === 'folder' ? 'folders' : where.view);
            $(this).toggleClass('active', on).attr('aria-selected', String(on));
        });
        $('#libSearch').attr('placeholder', where.view === 'folders' ? 'Search campaigns and images' : 'Search by what the image shows');
        if (where.view === 'folders') loadFolders(); else loadImages(true);
    }

    // ── Campaign folders ─────────────────────────────────────────────────
    function folderHash(f) { return 'c=' + (f.conversation_id == null ? 'none' : f.conversation_id); }
    function folderHtml(f) {
        const covers = f.covers.concat(['', '', '', '']).slice(0, 4);
        return `
            <a class="lib-folder" href="#${folderHash(f)}" aria-label="${esc(f.title)}, ${f.count} image${f.count === 1 ? '' : 's'}">
                <div class="lib-folder-covers${f.covers.length === 1 ? ' is-single' : ''}">
                    ${covers.map(u => u ? `<img src="${esc(u)}" alt="" loading="lazy">` : '<span></span>').join('')}
                </div>
                <div class="lib-folder-body">
                    <strong>${f.conversation_id == null ? '<i class="far fa-images me-1"></i>' : ''}${esc(f.title)}</strong>
                    <span>${f.count} image${f.count === 1 ? '' : 's'} &middot; ${esc(dateText(f.updated_at))}</span>
                </div>
            </a>`;
    }
    function loadFolders() {
        if (request) request.abort();
        $('#libGrid, #libCrumb').addClass('d-none');
        $('#libMore').addClass('d-none');
        $('#libFolders').removeClass('d-none').html(skeletons('lib-folder-skeleton', 6));
        request = $.get('/api/library/folders', { q: state.query })
            .done(function (r) {
                state.folders = r.folders || [];
                renderFolders();
            })
            .fail(function (xhr, status) { if (status !== 'abort') toast('Could not load your images. Please try again.'); })
            .always(function () { request = null; });
    }
    function renderFolders() {
        const n = state.folders.length;
        const images = state.folders.reduce((sum, f) => sum + f.count, 0);
        $('#libFolders').html(n ? state.folders.map(folderHtml).join('') : emptyHtml());
        $('#libSummary').text(n
            ? `${images.toLocaleString()} image${images === 1 ? '' : 's'} in ${n} campaign${n === 1 ? '' : 's'}${state.query ? ` matching "${state.query}"` : ''}.`
            : 'Your images, grouped by the campaign that made them.');
    }

    // ── Images (a campaign, everything, or favourites) ───────────────────
    function imageParams(offset, limit) {
        const params = { limit: limit || PAGE, offset: offset, q: state.query };
        if (state.view === 'folder') params.conversation = state.folder.conversation_id == null ? 'none' : state.folder.conversation_id;
        if (state.view === 'favorites') params.favorites = 1;
        return params;
    }
    function loadImages(reset) {
        if (state.loading && !reset) return;
        if (request) request.abort();
        state.loading = true;
        if (reset) {
            state.images = []; state.total = 0;
            $('#libFolders').addClass('d-none');
            $('#libGrid').removeClass('d-none').html(skeletons('lib-tile-skeleton', 8));
            renderCrumb();
        }
        $('#libMore').prop('disabled', true);
        request = $.get('/api/library/images', imageParams(state.images.length))
            .done(function (r) {
                state.images = state.images.concat(r.images || []);
                state.total = r.total || 0;
            })
            .fail(function (xhr, status) { if (status !== 'abort') toast('Could not load your images. Please try again.'); })
            .always(function (r, status) {
                request = null;
                if (status === 'abort') return;
                state.loading = false;
                $('#libMore').prop('disabled', false);
                renderImages();
            });
    }
    function renderCrumb() {
        const inFolder = state.view === 'folder';
        $('#libCrumb').toggleClass('d-none', !inFolder);
        if (!inFolder) return;
        const f = state.folder;
        $('#libFolderTitle').text(f.title);
        $('#libFolderCount').text(f.count != null ? ` · ${f.count} image${f.count === 1 ? '' : 's'}` : '');
        $('#libFolderChat').toggleClass('d-none', f.conversation_id == null);
    }
    function tileHtml(img) {
        const n = selectedIndex(img);
        const label = img.description || 'Image';
        return `
            <figure class="lib-tile${n >= 0 ? ' is-on' : ''}" data-id="${img.id}">
                <button type="button" class="lib-open" data-open="${img.id}" aria-label="Open: ${esc(label)}">
                    <img src="${esc(img.url)}" alt="${esc(label)}" loading="lazy">
                </button>
                <label class="lib-check" title="Add to the carousel tray">
                    <input type="checkbox" data-select="${img.id}" ${n >= 0 ? 'checked' : ''} aria-label="Select: ${esc(label)}">
                    <span>${n >= 0 ? n + 1 : ''}</span>
                </label>
                <button type="button" class="lib-fav${img.favorite ? ' is-on' : ''}" data-fav="${img.id}"
                    aria-pressed="${img.favorite ? 'true' : 'false'}" title="${img.favorite ? 'Remove from favourites' : 'Add to favourites'}">
                    <i class="${img.favorite ? 'fas' : 'far'} fa-star" aria-hidden="true"></i></button>
                ${img.versions > 1 ? `<span class="lib-versions-badge"><i class="fas fa-layer-group me-1" aria-hidden="true"></i>${img.versions} versions</span>` : ''}
                <figcaption>
                    <span class="lib-desc" title="${esc(label)}">${esc(label)}</span>
                    <span class="lib-meta">${esc(dateText(img.created_at))}${img.width ? ` · ${img.width}×${img.height}` : ''}</span>
                </figcaption>
            </figure>`;
    }
    function renderImages() {
        $('#libGrid').html(state.images.length ? state.images.map(tileHtml).join('') : emptyHtml());
        const what = state.view === 'favorites' ? 'favourite' : 'image';
        $('#libSummary').text(state.total
            ? `${state.total.toLocaleString()} ${what}${state.total === 1 ? '' : 's'}${state.query ? ` matching "${state.query}"` : ''}, newest first. Tick images to put them in the carousel tray.`
            : state.view === 'favorites' ? 'Star an image to find it here.' : 'Your images, newest first.');
        if (state.view === 'folder' && state.folder.count == null && !state.query) {
            state.folder.count = state.total;
            renderCrumb();
        }
        $('#libMore').toggleClass('d-none', state.images.length >= state.total);
        renderTray();
    }
    function skeletons(cls, n) { return Array.from({ length: n }, () => `<div class="${cls}" aria-hidden="true"></div>`).join(''); }
    function emptyHtml() {
        if (state.query) {
            return `<div class="lib-empty"><div class="card-icon-circle"><i class="fas fa-magnifying-glass"></i></div>
                <h6>Nothing matches "${esc(state.query)}"</h6><p>Try another word, or clear the search to see everything.</p></div>`;
        }
        if (state.view === 'favorites') {
            return `<div class="lib-empty"><div class="card-icon-circle"><i class="far fa-star"></i></div>
                <h6>No favourites yet</h6><p>Click the star on any image to keep it here, ready to use again.</p></div>`;
        }
        return `<div class="lib-empty"><div class="card-icon-circle"><i class="fas fa-images"></i></div>
            <h6>No images yet</h6>
            <p>Images you create in Studio Chat are kept here, grouped by campaign: tick <strong>Image</strong> when you write a post, or type <strong>/carousel</strong> for a set of slides.</p>
            <a class="lib-btn is-primary text-decoration-none" href="/dashboard"><i class="fas fa-comments me-1"></i>Open Studio Chat</a></div>`;
    }

    // ── The carousel tray ───────────────────────────────────────────────
    function renderTray() {
        const n = state.selected.length;
        $('#libTray').toggleClass('d-none', !n);
        $('body').toggleClass('lib-has-tray', n > 0);
        $('#libTrayCount').text(`${n} selected`);
        $('#libTrayHint').text(n === 1 ? 'A single-image post. Add more for a carousel.'
            : n > MAX_SLIDES ? `A carousel can have at most ${MAX_SLIDES} slides - remove ${n - MAX_SLIDES}.`
                : `A carousel of ${n} slides. Drag to change the order.`);
        $('#libTrayItems').html(state.selected.map((s, i) => `
            <li class="lib-tray-item" draggable="true" data-tray="${i}">
                <img src="${esc(s.url)}" alt="Slide ${i + 1}">
                <span class="lib-tray-num">${i + 1}</span>
                <div class="lib-tray-tools">
                    <button type="button" data-tray-move="${i}" data-dir="-1" aria-label="Move slide ${i + 1} earlier" ${i === 0 ? 'disabled' : ''}><i class="fas fa-chevron-left"></i></button>
                    <button type="button" data-tray-remove="${i}" aria-label="Remove slide ${i + 1}"><i class="fas fa-xmark"></i></button>
                    <button type="button" data-tray-move="${i}" data-dir="1" aria-label="Move slide ${i + 1} later" ${i === n - 1 ? 'disabled' : ''}><i class="fas fa-chevron-right"></i></button>
                </div>
            </li>`).join(''));
        $('#libApprove').prop('disabled', n > MAX_SLIDES);
        $('#libSchedule').prop('disabled', n > MAX_SLIDES || !window.SCHEDULING_ENABLED);
    }
    function selectionChanged() {
        // tile numbers follow the tray order
        $('.lib-tile').each(function () {
            const img = state.images.find(i => i.id === Number($(this).data('id')));
            if (!img) return;
            const n = selectedIndex(img);
            $(this).toggleClass('is-on', n >= 0);
            $(this).find('[data-select]').prop('checked', n >= 0);
            $(this).find('.lib-check span').text(n >= 0 ? n + 1 : '');
        });
        renderTray();
    }
    function toggleSelected(img, on) {
        const n = selectedIndex(img);
        if (on && n < 0) state.selected.push({ id: img.id, url: img.url });
        if (!on && n >= 0) state.selected.splice(n, 1);
        selectionChanged();
    }
    function moveSlide(from, to) {
        if (to < 0 || to >= state.selected.length || from === to) return;
        const [item] = state.selected.splice(from, 1);
        state.selected.splice(to, 0, item);
        selectionChanged();
    }
    // One post from the tray: a carousel for LinkedIn and Instagram (Facebook
    // can be ticked in the dialog), or a single image everywhere.
    function postSource() {
        const images = state.selected.map(s => s.url);
        const many = images.length > 1;
        return {
            key: `library-${Date.now()}`, story: '', conversationId: null, runId: null,
            platforms: ['linkedin', 'instagram', 'facebook'].map(p => ({
                platform: p, caption: '', hashtags: [], images: images, media: many ? 'carousel' : 'single', on: p !== 'facebook',
            })),
        };
    }

    // ── Favourites and delete ───────────────────────────────────────────
    function setFavorite(img, on) {
        img.favorite = on;
        $.ajax({ url: `/api/library/images/${img.id}/favorite`, type: 'POST', contentType: 'application/json', data: JSON.stringify({ on: on }) })
            .done(() => {
                if (state.view === 'favorites' && !on) {
                    state.images = state.images.filter(i => i.id !== img.id);
                    state.total = Math.max(0, state.total - 1);
                }
                renderImages();
                if (state.viewing && state.viewing.id === img.id) renderViewerActions();
            })
            .fail(() => { img.favorite = !on; renderImages(); toast('Could not update favourites. Please try again.'); });
    }
    // Remove now, send after the Undo window (static/js/undo.js)
    function deleteImages(ids) {
        const removed = state.images.filter(i => ids.includes(i.id));
        if (!removed.length) return;
        const before = state.images.slice(), beforeTotal = state.total, beforeSelected = state.selected.slice();
        const many = removed.length > 1;
        window.withUndo({
            message: many ? `${removed.length} images deleted` : 'Image deleted',
            url: many ? `/api/library/images?ids=${ids.join(',')}` : `/api/library/images/${ids[0]}`,
            method: 'DELETE',
            hide: function () {
                state.images = state.images.filter(i => !ids.includes(i.id));
                state.total = Math.max(0, state.total - removed.length);
                state.selected = state.selected.filter(s => !ids.includes(s.id));
                if (state.folder && state.folder.count != null) state.folder.count = Math.max(0, state.folder.count - removed.length);
                closeViewer();
                renderCrumb();
                renderImages();
            },
            restore: function () {
                state.images = before; state.total = beforeTotal; state.selected = beforeSelected;
                if (state.folder && state.folder.count != null) state.folder.count += removed.length;
                renderCrumb();
                renderImages();
            },
            failed: function () { toast('Could not delete. Please try again.'); },
        });
    }

    // ── The viewer ──────────────────────────────────────────────────────
    function showInViewer(version) {
        $('#libViewerImg').attr({ src: version.url, alt: version.description || 'Image' });
        $('#libViewerText').text(version.description || '');
        const bits = [dateText(version.created_at), version.width ? `${version.width}×${version.height}` : '', shapeText(version),
            PLATFORMS[version.platform] || ''].filter(Boolean);
        $('#libViewerMeta').html(bits.map(b => `<span>${esc(b)}</span>`).join(''));
        state.viewing.shown = version;
        $('#libVersionsStrip [data-version]').each(function () {
            $(this).toggleClass('is-on', Number($(this).data('version')) === version.id).attr('aria-pressed', String(Number($(this).data('version')) === version.id));
        });
        renderViewerActions();
    }
    function renderViewerActions() {
        const img = state.viewing, shown = img.shown || img;
        const inTray = selectedIndex(img) >= 0;
        $('#libViewerActions').html(`
            <button type="button" class="lib-btn is-primary" data-use="${esc(shown.url)}"><i class="fas fa-plus me-1"></i>Use in a new post</button>
            <button type="button" class="lib-btn" data-viewer-tray><i class="fas ${inTray ? 'fa-check' : 'fa-clone'} me-1"></i>${inTray ? 'In the carousel tray' : 'Add to carousel tray'}</button>
            <div class="lib-viewer-row">
                <a class="lib-btn text-decoration-none" href="${esc(shown.url)}" download><i class="fas fa-download me-1"></i>Download</a>
                <button type="button" class="lib-btn" data-viewer-fav aria-pressed="${img.favorite ? 'true' : 'false'}"><i class="${img.favorite ? 'fas' : 'far'} fa-star me-1"></i>${img.favorite ? 'Favourite' : 'Add to favourites'}</button>
            </div>
            ${img.conversation_id ? `<button type="button" class="lib-btn" data-conversation="${img.conversation_id}"><i class="fas fa-comments me-1"></i>Open its conversation</button>` : ''}
            <button type="button" class="lib-btn is-danger" data-viewer-delete><i class="far fa-trash-can me-1"></i>Delete${img.versions > 1 ? ` all ${img.versions} versions` : ''}</button>`);
    }
    function openViewer(img) {
        state.viewing = img;
        $('#libVersions').toggleClass('d-none', !(img.versions > 1));
        $('#libVersionsStrip').empty();
        showInViewer(img);
        if (img.versions > 1) {
            $.get(`/api/library/images/${img.id}/versions`).done(function (r) {
                if (state.viewing !== img) return;
                const versions = r.versions || [];
                $('#libVersionsLabel').text(`${versions.length} versions, oldest first`);
                $('#libVersionsStrip').html(versions.map((v, i) => `
                    <button type="button" class="lib-version" data-version="${v.id}" title="${esc(v.description || (i ? 'Edit ' + i : 'Original'))}">
                        <img src="${esc(v.url)}" alt="Version ${i + 1}"><span>${i ? 'v' + (i + 1) : 'Original'}</span></button>`).join(''));
                img.allVersions = versions;
                showInViewer(img.shown && img.shown.id !== img.id ? img.shown : versions[versions.length - 1] || img);
            });
        }
        const viewer = document.getElementById('libViewer');
        if (viewer.showModal) { if (!viewer.open) viewer.showModal(); } else viewer.setAttribute('open', '');
    }
    function closeViewer() {
        const viewer = document.getElementById('libViewer');
        if (viewer && viewer.open) { if (viewer.close) viewer.close(); else viewer.removeAttribute('open'); }
        state.viewing = null;
    }

    // ── Wiring ──────────────────────────────────────────────────────────
    $(function () {
        $(window).on('hashchange', route);
        $(document).on('click', '.lib-view', function () {
            const view = $(this).data('lib-view');
            go(view === 'folders' ? '' : view);
        });
        $('#libBack').on('click', () => go(''));

        let searchTimer = null;
        $('#libSearch').on('input', function () {
            clearTimeout(searchTimer);
            const value = this.value.trim();
            searchTimer = setTimeout(function () {
                if (value === state.query) return;
                state.query = value;
                if (state.view === 'folders') loadFolders(); else loadImages(true);
            }, 350);
        });

        // More images as the end of the grid comes into view ("Show more" stays as a fallback)
        $('#libMore').on('click', () => loadImages(false));
        if ('IntersectionObserver' in window) {
            new window.IntersectionObserver(entries => {
                if (entries.some(e => e.isIntersecting) && !$('#libMore').hasClass('d-none') && !state.loading) loadImages(false);
            }, { rootMargin: '400px' }).observe(document.querySelector('.lib-more'));
        }

        // Tiles
        $(document).on('click', '[data-open]', function () {
            const img = state.images.find(i => i.id === Number($(this).data('open')));
            if (img) openViewer(img);
        });
        $(document).on('change', '[data-select]', function () {
            const img = state.images.find(i => i.id === Number($(this).data('select')));
            if (img) toggleSelected(img, this.checked);
        });
        $(document).on('click', '[data-fav]', function () {
            const img = state.images.find(i => i.id === Number($(this).data('fav')));
            if (img) setFavorite(img, !img.favorite);
        });

        // A campaign as a whole
        $('#libFolderChat').on('click', function () {
            try { sessionStorage.setItem(CONVERSATION_KEY, String(state.folder.conversation_id)); } catch (e) { /* storage blocked */ }
            window.location.href = '/dashboard';
        });
        $('#libFolderCarousel').on('click', function () {
            // the campaign's images, in the order they were made, into the tray
            const $btn = $(this).prop('disabled', true);
            $.get('/api/library/images', imageParams(0, MAX_SLIDES)).done(function (r) {
                const images = (r.images || []).slice().reverse();
                images.forEach(img => { if (selectedIndex(img) < 0 && state.selected.length < MAX_SLIDES) state.selected.push({ id: img.id, url: img.url }); });
                selectionChanged();
                toast(r.total > MAX_SLIDES ? `Added the first ${MAX_SLIDES} images - a carousel holds at most ${MAX_SLIDES}.` : 'Added to the carousel tray. Drag to change the order.');
            }).fail(() => toast('Could not load the campaign. Please try again.'))
                .always(() => $btn.prop('disabled', false));
        });

        // The tray: reorder by dragging or with the arrows, remove, clear, use
        let dragFrom = null;
        $(document).on('dragstart', '.lib-tray-item', function (e) {
            dragFrom = Number($(this).data('tray'));
            $(this).addClass('is-dragging');
            e.originalEvent.dataTransfer.effectAllowed = 'move';
            try { e.originalEvent.dataTransfer.setData('text/plain', String(dragFrom)); } catch (err) { /* old browsers */ }
        });
        $(document).on('dragend', '.lib-tray-item', function () { $(this).removeClass('is-dragging'); $('.lib-tray-item').removeClass('is-target'); });
        $(document).on('dragover', '.lib-tray-item', function (e) { e.preventDefault(); $('.lib-tray-item').removeClass('is-target'); $(this).addClass('is-target'); });
        $(document).on('drop', '.lib-tray-item', function (e) {
            e.preventDefault();
            if (dragFrom != null) moveSlide(dragFrom, Number($(this).data('tray')));
            dragFrom = null;
        });
        $(document).on('click', '[data-tray-move]', function () {
            const i = Number($(this).data('tray-move'));
            moveSlide(i, i + Number($(this).data('dir')));
            $(`[data-tray-move="${i + Number($(this).data('dir'))}"][data-dir="${$(this).data('dir')}"]`).trigger('focus');
        });
        $(document).on('click', '[data-tray-remove]', function () {
            state.selected.splice(Number($(this).data('tray-remove')), 1);
            selectionChanged();
        });
        $('#libClear').on('click', function () { state.selected = []; selectionChanged(); });
        $('#libDeleteSelected').on('click', () => deleteImages(state.selected.map(s => s.id)));
        if (!window.SCHEDULING_ENABLED) {  // "Soon" until STUDIO_SCHEDULING=true (config.py)
            $('#libSchedule').attr('title', 'Coming soon').append('<span class="soon-tag">Soon</span>');
        }
        $('#libSchedule').on('click', function () {
            if (!window.SCHEDULING_ENABLED) return;
            window.openSchedulePost(Object.assign(postSource(), {
                onScheduled: function () { state.selected = []; selectionChanged(); },
            }));
        });
        $('#libApprove').on('click', function () {
            window.openSendForApproval(Object.assign(postSource(), {
                onSent: function () { state.selected = []; selectionChanged(); },
            }));
        });

        // In the viewer
        $(document).on('click', '[data-version]', function () {
            const img = state.viewing;
            const v = img && (img.allVersions || []).find(x => x.id === Number($(this).data('version')));
            if (v) showInViewer(v);
        });
        $(document).on('click', '[data-viewer-tray]', function () {
            const img = state.viewing;
            if (img) { toggleSelected(img, selectedIndex(img) < 0); renderViewerActions(); }
        });
        $(document).on('click', '[data-viewer-fav]', function () { if (state.viewing) setFavorite(state.viewing, !state.viewing.favorite); });
        $(document).on('click', '[data-viewer-delete]', function () { if (state.viewing) deleteImages([state.viewing.id]); });
        $(document).on('click', '[data-use]', function () {
            try { sessionStorage.removeItem(CONVERSATION_KEY); sessionStorage.setItem(PENDING_IMAGE_KEY, $(this).data('use')); }
            catch (e) { /* storage blocked: Studio Chat opens without the image */ }
            window.location.href = '/dashboard';
        });
        $(document).on('click', '[data-conversation]', function () {
            try { sessionStorage.setItem(CONVERSATION_KEY, String($(this).data('conversation'))); } catch (e) { /* storage blocked */ }
            window.location.href = '/dashboard';
        });
        document.getElementById('libViewer').addEventListener('close', () => { state.viewing = null; });

        route();
    });
})();
