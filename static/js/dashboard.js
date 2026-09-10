/* global showToast, renderCarousel */
$(document).ready(function () {

    // Load current user info into the shared app header
    $.ajax({
        url: '/api/auth/me',
        type: 'GET',
        success: function (r) {
            if (r.user) {
                $('#headerUserLabel').text(r.user.name);
                $('#headerUserEmail').text(r.user.email);
                $('#headerUserAvatar').text(r.user.name.charAt(0).toUpperCase());
            }
        }
    });

    $('input[name="mediaType"]').on('change', function () {
        if ($(this).val() === 'image') {
            $('#imageContextContainer').removeClass('d-none');
        } else {
            $('#imageContextContainer').addClass('d-none');
        }
    });

    $(document).on('change', 'input[name="modalMediaType"]', function () {
        if ($(this).val() === 'image') {
            $('#modalImageContextContainer').removeClass('d-none');
        } else {
            $('#modalImageContextContainer').addClass('d-none');
        }
    });

    // Brand character assets selectable under Character Setup. "auto" has no
    // image - the AI invents a text-described persona instead. "ada"/"logo"
    // use the real brand image as a reference for generated visuals. Global
    // (not closure-local) since regenerateImageInCarousel/regenerateModalImage
    // are defined outside this $(document).ready block.
    window.CHARACTER_ASSETS = {
        ada: { path: 'static/img/brand/ada-character.png', url: '/static/img/brand/ada-character.png', label: 'Ada' },
        logo: { path: 'static/img/brand/stradit-logo.png', url: '/static/img/brand/stradit-logo.png', label: 'StradIT Logo' }
    };

    // Resolves a pipeline's configured brand character (if any) to the
    // reference image path media generation should use. Returns null for
    // "auto" (AI-invented persona, no real reference image) or no config.
    window.getCharacterAssetPath = function (pipeline) {
        const character = pipeline && pipeline.characterConfig && pipeline.characterConfig.character;
        const asset = window.CHARACTER_ASSETS[character];
        return asset ? asset.path : null;
    };

    function renderCharacterAssetPreview() {
        const asset = window.CHARACTER_ASSETS[$('#characterAssetSelect').val()];
        $('#characterAssetPreview').html(asset
            ? `<img src="${asset.url}" alt="${asset.label}" style="width: 36px; height: 36px; object-fit: contain; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; padding: 2px;"><span class="small text-muted">${asset.label} will be used as the reference image for generated visuals.</span>`
            : '');
    }

    $('#characterModeSelect').on('change', function () {
        $('#characterAssetContainer').toggleClass('d-none', $(this).val() !== 'with_character');
    }).trigger('change');

    $('#characterAssetSelect').on('change', renderCharacterAssetPreview);
    renderCharacterAssetPreview();

    // Toast notification helper
    window.showToast = function (message, type = 'info') {
        const bgClass = type === 'success' ? 'bg-success' : type === 'danger' ? 'bg-danger' : type === 'warning' ? 'bg-warning' : 'bg-primary';
        const toastHtml = `
            <div class="toast align-items-center text-white ${bgClass} border-0 show" role="alert" aria-live="assertive" aria-atomic="true" style="position: fixed; bottom: 20px; right: 20px; z-index: 1055; min-width: 250px;">
                <div class="d-flex">
                    <div class="toast-body fw-bold">
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                </div>
            </div>
        `;
        const $toast = $(toastHtml);
        $('body').append($toast);
        setTimeout(() => {
            $toast.fadeOut(300, function () { $(this).remove(); });
        }, 4000);
    }

    // "all" isn't a real generation target - fall back to linkedin for content generation.
    // Pass a selector to read the dedicated per-generation platform picker
    // (#pipelineTargetPlatform / #modalPipelineTargetPlatform) - each platform
    // generates images/video at its own correct size. With no selector, falls
    // back to the feed's platform filter (used only by defensive fallbacks
    // where a generated asset is missing its platform tag).
    function getSelectedPlatform(selector) {
        const val = $(selector || '#dashboardPlatformSelect').val();
        return (!val || val === 'all') ? 'linkedin' : val;
    }

    window.fetchPlatformPosts = function () {
        const platform = $('#dashboardPlatformSelect').val();
        if (!platform) return;
        const competitor = $('#dashboardCompetitorSelect').val();

        $('#postsContainer').addClass('d-none');
        $('#postsLoader').removeClass('d-none');

        // Reset selections
        $('#selectedPostCount').text('0');
        $('#storyContextInput').val('');
        $('#generateStoryBtn').prop('disabled', true);
        closeSynthesisPanel();

        let url = '/api/platform-posts?platform=' + encodeURIComponent(platform);
        if (competitor && competitor !== 'all') {
            url += '&competitor=' + encodeURIComponent(competitor);
        }

        $.ajax({
            url: url,
            type: 'GET',
            success: function (r) {
                if (r.success && r.posts && r.posts.length > 0) {
                    if (r.db && r.db.inserted > 0) {
                        showToast(`Saved ${r.db.inserted} new post${r.db.inserted === 1 ? '' : 's'} to the database.`, 'success');
                    } else {
                        showToast('Scan complete. No new posts found.', 'info');
                    }
                    // Mark the posts saved by this scan so the feed can label them "New"
                    window.newlyInsertedPostUrls = new Set((r.db && r.db.new_post_urls) || []);
                    // Refresh the feed from the DB so it reflects everything stored (existing + new)
                    loadStoredPosts();
                } else {
                    $('#postsLoader').addClass('d-none');
                    $('#postsContainer').removeClass('d-none').html(`
                        <div class="col-12 text-center py-5 my-5 text-muted">
                            <i class="fas fa-exclamation-circle fa-3x mb-3 text-secondary opacity-50"></i>
                            <p class="fw-semibold">No ${platform === 'all' ? '' : platform + ' '}posts found for any competitors.</p>
                        </div>
                    `);
                }
            },
            error: function (xhr) {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none').html(`
                    <div class="col-12 text-center py-5 my-5 text-danger">
                        <i class="fas fa-times-circle fa-3x mb-3"></i>
                        <p class="fw-bold">Error fetching posts: ${xhr.responseJSON?.error || 'Unknown error'}</p>
                    </div>
                `);
            }
        });
    };

    // Loads previously-scraped posts already saved in the DB (no external scan).
    // onComplete (optional) fires once the feed has been rendered, so callers
    // like useSuggestedCollection() can act on the resulting checkboxes.
    window.loadStoredPosts = function (onComplete) {
        const platform = $('#dashboardPlatformSelect').val();
        if (!platform) return;
        const competitor = $('#dashboardCompetitorSelect').val();

        $('#postsContainer').addClass('d-none');
        $('#postsLoader').removeClass('d-none');

        let url = '/api/competitor-posts-db?platform=' + encodeURIComponent(platform);
        if (competitor && competitor !== 'all') {
            url += '&competitor=' + encodeURIComponent(competitor);
        }

        $.ajax({
            url: url,
            type: 'GET',
            success: function (r) {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none');

                if (r.success && r.posts && r.posts.length > 0) {
                    renderPlatformPosts(r.posts);
                    if (typeof onComplete === 'function') onComplete();
                } else {
                    // Auto-scan if no stored posts exist so the dashboard lists posts immediately
                    window.fetchPlatformPosts();
                }
            },
            error: function () {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none');
                window.fetchPlatformPosts();
            }
        });
    };

    window.newlyInsertedPostUrls = new Set();

    // Show whatever is already saved as soon as the dashboard loads / filters change
    loadStoredPosts();
    $('#dashboardPlatformSelect, #dashboardCompetitorSelect').on('change', function () {
        window.newlyInsertedPostUrls = new Set();
        loadStoredPosts();
    });

    function getPostTimestamp(p) {
        const raw = p.scraped_at || p.published_at || p.post_date || p.date || p.created_at || null;
        const t = raw ? new Date(raw).getTime() : NaN;
        return isNaN(t) ? 0 : t;
    }

    function getPostTitle(p) {
        if (p.title && p.title.trim()) return p.title.trim();
        if (p.text) {
            const firstLine = (p.text.split('\n').find(l => l.trim().length > 0) || '').trim();
            if (firstLine) {
                return firstLine.length > 90 ? firstLine.substring(0, 90).trim() + '...' : firstLine;
            }
        }
        return 'Untitled Post';
    }

    function formatPostDate(p) {
        const raw = p.scraped_at || p.published_at || p.post_date || p.date || p.created_at || null;
        if (!raw) return '';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function renderPlatformPosts(posts) {
        // Show every post, sorted by most recent first
        const sortedPosts = [...posts].sort((a, b) => getPostTimestamp(b) - getPostTimestamp(a));

        // Update KPI Stats
        $('#statScrapedCount').text(`${posts.length} Posts`);

        let html = '<div class="row g-4 pt-3" id="feedCardsRow">';
        let cIdx = 0;

        sortedPosts.forEach(p => {
            const comp = p._source_competitor || 'Unknown Competitor';
            const title = getPostTitle(p);
            const textSnippet = p.text ? p.text.substring(0, 300) + (p.text.length > 300 ? '...' : '') : 'No text content available.';
            const platformIcon = getPlatformIcon(p.platform);
            const postDate = formatPostDate(p);
            let isNew = window.newlyInsertedPostUrls && p.post_url && window.newlyInsertedPostUrls.has(p.post_url);

            if (!isNew) {
                const rawDate = p.scraped_at || p.published_at || p.post_date || p.date || p.created_at || null;
                if (rawDate) {
                    const d = new Date(rawDate);
                    const today = new Date();
                    if (!isNaN(d.getTime()) &&
                        d.getDate() === today.getDate() &&
                        d.getMonth() === today.getMonth() &&
                        d.getFullYear() === today.getFullYear()) {
                        isNew = true;
                    }
                }
            }

            // Full payload for generation
            const encodedPayload = encodeURIComponent(`[${comp} - ${title}]\n${p.text || title}\n\n---\n\n`);

            html += `
            <div class="col-md-6 mt-2 competitor-post-card-col">
                <div class="premium-card h-100 d-flex flex-column competitor-post-card position-relative p-4" id="postCard_${cIdx}" style="background: white; border: 1px solid #e2e8f0; border-radius: 16px; overflow: hidden;">
                    ${isNew ? '<span class="badge bg-success position-absolute" style="top: 0; left: 0; font-size: 0.7rem; padding: 0.35rem 0.8rem; box-shadow: 2px 2px 6px rgba(0,0,0,0.1); z-index: 10; border-bottom-right-radius: 12px;"><i class="fas fa-sparkles me-1"></i>New</span>' : ''}
                    <div class="d-flex justify-content-between align-items-start mb-3">
                        <div class="d-flex align-items-center gap-2">
                            <div class="rounded-circle d-flex align-items-center justify-content-center shadow-sm" style="width: 36px; height: 36px; background: rgba(79, 70, 229, 0.1); color: var(--primary);">
                                <i class="fas fa-building-columns"></i>
                            </div>
                            <div>
                                <h6 class="mb-0 fw-bold text-dark">${comp}</h6>
                                <small class="text-muted fw-medium">${postDate || 'Recent Post'}</small>
                            </div>
                        </div>
                        <div class="form-check m-0" style="transform: scale(1.3);">
                            <input class="form-check-input comp-master-checkbox cursor-pointer shadow-sm border-primary" type="checkbox" value="${cIdx}" id="masterCheck${cIdx}" data-payload="${encodedPayload}" data-competitor="${comp}" data-post-url="${escapeHtml(p.post_url).replace(/"/g, '&quot;')}">
                        </div>
                    </div>

                    <label class="flex-grow-1 cursor-pointer" for="masterCheck${cIdx}">
                        <h6 class="fw-bold text-gray-800 mb-2 lh-base d-flex align-items-start gap-2" style="font-size: 1.05rem;">
                            <span class="mt-1">${platformIcon}</span> <span>${title}</span>
                        </h6>
                        <p class="text-muted small mb-0 lh-sm" style="line-height: 1.5 !important;">${textSnippet}</p>
                    </label>
                    ${p.post_url ? `<div class="pt-2 mt-auto text-end"><a href="${escapeHtml(p.post_url)}" target="_blank" class="small text-decoration-none text-primary font-semibold" onclick="event.stopPropagation();"><i class="fas fa-external-link-alt me-1"></i>View Source</a></div>` : ''}
                </div>
            </div>`;
            cIdx++;
        });

        html += '</div>';

        $('#postsContainer').html(html);

        $('.comp-master-checkbox').on('change', function () {
            updateSelection();
        });
    }

    // ==========================================
    // SUGGESTED STORYLINES (similarity-based post collections)
    // ==========================================
    window.suggestedCollections = [];
    window.newSuggestedCollectionHashes = new Set();

    // Hydrate from previously-generated (persisted) suggestions on page load,
    // same pattern as loadOpportunitySuggestions() - no compute, just a read.
    window.loadSuggestedCollections = function () {
        $.ajax({
            url: '/api/suggested-collections',
            type: 'GET',
            success: function (r) {
                if (r.success && r.collections) {
                    window.suggestedCollections = r.collections;
                    renderSuggestedCollections();
                }
            }
        });
    };

    window.generateSuggestedCollections = function () {
        const platform = $('#dashboardPlatformSelect').val() || 'all';
        const competitor = $('#dashboardCompetitorSelect').val() || 'all';

        $('#suggestCollectionsBtn').prop('disabled', true);
        $('#suggestedCollectionsLoader').removeClass('d-none');

        $.ajax({
            url: '/api/generate-suggested-collections',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ platform: platform, competitor: competitor }),
            success: function (r) {
                $('#suggestedCollectionsLoader').addClass('d-none');
                $('#suggestCollectionsBtn').prop('disabled', false);

                if (r.success) {
                    window.suggestedCollections = r.collections || [];
                    window.newSuggestedCollectionHashes = new Set((r.db && r.db.new_hashes) || []);
                    renderSuggestedCollections();

                    if (r.db && r.db.inserted > 0) {
                        showToast(`Found ${r.db.inserted} new storyline${r.db.inserted === 1 ? '' : 's'}.`, 'success');
                    } else {
                        showToast('No new related storylines found this run.', 'info');
                    }
                } else {
                    showToast('Failed to generate suggestions.', 'danger');
                }
            },
            error: function (xhr) {
                $('#suggestedCollectionsLoader').addClass('d-none');
                $('#suggestCollectionsBtn').prop('disabled', false);
                showToast('Could not generate suggestions: ' + (xhr.responseJSON?.error || 'Unknown error'), 'danger');
            }
        });
    };

    function renderSuggestedCollections() {
        const list = $('#suggestedCollectionsList');
        if (window.suggestedCollections.length === 0) {
            list.html('<p class="text-muted small m-0">No storylines suggested yet - click "Suggest Storylines" to scan for related posts.</p>');
            return;
        }

        const relevanceClass = {
            high: 'bg-success-subtle text-success',
            medium: 'bg-warning-subtle text-warning',
            low: 'bg-secondary-subtle text-secondary'
        };

        let html = '';
        window.suggestedCollections.forEach((c, idx) => {
            const badgeClass = relevanceClass[c.relevance] || relevanceClass.medium;
            const isNew = window.newSuggestedCollectionHashes.has(c.post_urls_hash);
            const tags = [...(c.competitors || []), ...(c.platforms || []).map(p => p.toUpperCase())]
                .map(t => `<span class="badge bg-light text-dark border" style="font-size: 0.65rem;">${escapeHtml(t)}</span>`)
                .join(' ');

            let generatedDate = '';
            if (c.created_at) {
                const d = new Date(c.created_at);
                if (!isNaN(d.getTime())) {
                    generatedDate = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
                        + ' · ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
                }
            }

            html += `
                <div class="border rounded-4 p-3 flex-shrink-0 d-flex flex-column gap-2 position-relative" style="min-width: 260px; max-width: 280px; background: #f8fafc;">
                    ${isNew ? '<span class="badge rounded-pill bg-success position-absolute" style="top: -8px; right: 10px; font-size: 0.6rem;">New</span>' : ''}
                    <div class="d-flex align-items-center justify-content-between">
                        <span class="badge rounded-pill ${badgeClass} text-uppercase" style="font-size: 0.65rem;">${escapeHtml(c.relevance)}</span>
                        <span class="text-muted small">${c.post_count} posts</span>
                    </div>
                    <h6 class="fw-bold text-dark mb-0" style="font-size: 0.9rem;">${escapeHtml(c.label)}</h6>
                    <p class="text-muted small mb-1" style="font-size: 0.78rem; line-height: 1.4;">${escapeHtml(c.description)}</p>
                    <div class="d-flex flex-wrap gap-1">${tags}</div>
                    ${generatedDate ? `<small class="text-muted" style="font-size: 0.68rem;"><i class="fas fa-clock me-1"></i>Generated ${generatedDate}</small>` : ''}
                    <button class="btn btn-sm btn-primary fw-bold rounded-pill mt-auto" onclick="useSuggestedCollection(${idx})">
                        <i class="fas fa-check me-1"></i>Use This Collection
                    </button>
                </div>
            `;
        });
        list.html(html);
    }

    window.loadSuggestedCollections();

    // ==========================================
    // FESTIVE STORYLINES (upcoming US holidays / Indian festivals)
    // ==========================================
    window.festiveStorylines = [];

    window.loadFestiveStorylines = function () {
        $('#festiveStorylinesLoader').removeClass('d-none');
        $.ajax({
            url: '/api/festive-storylines?days_ahead=60',
            type: 'GET',
            success: function (r) {
                $('#festiveStorylinesLoader').addClass('d-none');
                window.festiveStorylines = (r.success && r.festivals) || [];
                renderFestiveStorylines();
            },
            error: function () {
                $('#festiveStorylinesLoader').addClass('d-none');
            }
        });
    };

    function renderFestiveStorylines() {
        const list = $('#festiveStorylinesList');
        if (window.festiveStorylines.length === 0) {
            list.html('<p class="text-muted small m-0">No upcoming holidays or festivals in the next 60 days.</p>');
            return;
        }

        const regionClass = {
            USA: 'bg-primary-subtle text-primary',
            India: 'bg-warning-subtle text-warning'
        };

        let html = '';
        window.festiveStorylines.forEach((f, idx) => {
            const badgeClass = regionClass[f.region] || 'bg-secondary-subtle text-secondary';
            const dateLabel = new Date(f.date).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
            const daysLabel = f.days_until === 0 ? 'Today' : (f.days_until === 1 ? 'Tomorrow' : `In ${f.days_until} days`);

            html += `
                <div class="border rounded-4 p-3 flex-shrink-0 d-flex flex-column gap-2" style="min-width: 220px; max-width: 240px; background: #fffbeb;">
                    <div class="d-flex align-items-center justify-content-between">
                        <span class="badge rounded-pill ${badgeClass}" style="font-size: 0.65rem;">${escapeHtml(f.region)}</span>
                        <span class="text-muted small">${daysLabel}</span>
                    </div>
                    <h6 class="fw-bold text-dark mb-0" style="font-size: 0.9rem;"><i class="fas fa-champagne-glasses text-warning me-1"></i>${escapeHtml(f.name)}</h6>
                    <p class="text-muted small mb-0" style="font-size: 0.78rem;">${dateLabel}</p>
                    <button class="btn btn-sm btn-warning fw-bold rounded-pill mt-auto" onclick="useFestiveStoryline(${idx})">
                        <i class="fas fa-wand-magic-sparkles me-1"></i>Create Festive Post
                    </button>
                </div>
            `;
        });
        list.html(html);
    }

    // Seeds the context buffer with a festive greeting brief (no competitor
    // posts) and opens the Synthesis panel, reusing the same Generate
    // Counter-Strategy -> images/video pipeline as competitor-based
    // storylines. StoryAgent recognizes the "--- FESTIVE GREETING ---" marker
    // and skips the strict project-matching gate for this content.
    window.useFestiveStoryline = function (idx) {
        const festival = window.festiveStorylines[idx];
        if (!festival) return;

        $('.comp-master-checkbox').prop('checked', false);
        $('.competitor-post-card').removeClass('selected-card');
        $('#selectedPostCount').text(0);
        $('#selectedPostCountBadge').text('0 Selected');

        const context = `--- FESTIVE GREETING ---\nFestival: ${festival.name}\nDate: ${festival.date}\nRegion: ${festival.region}\n\nCreate a warm, professional festive greeting/social media post for this occasion, reflecting StradIT's brand voice.`;
        $('#storyContextInput').val(context);
        $('#generateStoryBtn').prop('disabled', false);
        openSynthesisPanel();

        showToast(`Ready to create a ${festival.name} post - click Generate Counter-Strategy.`, 'success');
    };

    window.loadFestiveStorylines();

    // Selects every post belonging to a suggested collection and opens the
    // Synthesis panel, reusing the existing manual-selection pipeline as-is.
    // A collection can span competitors/platforms outside the current feed
    // filter, so the filters are switched to "all" and the feed reloaded
    // before matching checkboxes by data-post-url.
    window.useSuggestedCollection = function (idx) {
        const collection = window.suggestedCollections[idx];
        if (!collection) return;

        const targetUrls = new Set(collection.post_urls || []);

        $('#dashboardPlatformSelect').val('all');
        $('#dashboardCompetitorSelect').val('all');

        window.loadStoredPosts(function () {
            $('.comp-master-checkbox').each(function () {
                $(this).prop('checked', targetUrls.has($(this).data('post-url')));
            });
            updateSelection();
            showToast(`Selected ${collection.post_count} posts from "${collection.label}".`, 'success');
        });
    };

    // #centerFeedPanel uses Bootstrap's auto-layout column (col-xl/col-lg with no
    // number), so it always fills whatever space is left beside the fixed-width
    // History / collapsed-rail / Synthesis columns - no manual width math needed.

    function openSynthesisPanel() {
        $('#rightSynthesisPanel').removeClass('d-none');
    }

    function closeSynthesisPanel() {
        $('#rightSynthesisPanel').addClass('d-none');
    }

    // Toggles the History panel between its full column and a slim collapsed rail
    window.toggleHistoryPanel = function (show) {
        if (show) {
            $('#historyPanelCol').removeClass('d-none');
            $('#historyCollapsedRail').addClass('d-none');
        } else {
            $('#historyPanelCol').addClass('d-none');
            $('#historyCollapsedRail').removeClass('d-none');
        }
        try {
            localStorage.setItem('historyPanelVisible', show ? '1' : '0');
        } catch (e) { /* ignore storage errors */ }
    };

    // Restore the last-used History panel visibility (defaults to visible)
    (function initHistoryPanelState() {
        let visible = true;
        try {
            visible = localStorage.getItem('historyPanelVisible') !== '0';
        } catch (e) { /* ignore storage errors */ }
        if (!visible) {
            window.toggleHistoryPanel(false);
        }
    })();

    function updateSelection() {
        const checked = $('.comp-master-checkbox:checked');
        const count = checked.length;
        $('#selectedPostCount').text(count);
        $('#selectedPostCountBadge').text(`${count} Selected`);

        // Highlight selected cards
        $('.competitor-post-card').removeClass('selected-card');
        checked.each(function () {
            $(this).closest('.competitor-post-card').addClass('selected-card');
        });

        if (count === 0) {
            $('#storyContextInput').val('');
            $('#generateStoryBtn').prop('disabled', true);
            closeSynthesisPanel();
            window.slideWorkflow(0);
            return;
        }

        // Only open the Strategic Synthesis panel once a post is selected
        openSynthesisPanel();

        $('#generateStoryBtn').prop('disabled', false);

        let combinedText = "--- SELECTED COMPETITOR POSTS ---\n\n";
        checked.each(function () {
            const decoded = decodeURIComponent($(this).attr('data-payload'));
            combinedText += decoded;
        });

        $('#storyContextInput').val(combinedText);
    }

    // ── Workflow Slider & User Journey Stepper Synchronizer ──────────────────
    window.slideWorkflow = function (stepIndex) {
        const percentages = [0, -25, -50, -75];
        const pct = percentages[stepIndex] !== undefined ? percentages[stepIndex] : 0;
        $('#workflowSlider').css('transform', `translateX(${pct}%)`);

        // Update wizard header step badges
        $('.wizard-step-badge').removeClass('active');
        $(`#wizStepBadge${stepIndex}`).addClass('active');

        // Update top 4-step user journey stepper
        $('.journey-step-item').removeClass('active completed');
        for (let i = 1; i <= 4; i++) {
            if (i - 1 < stepIndex) {
                $(`#journeyStep${i}`).addClass('completed');
            } else if (i - 1 === stepIndex) {
                $(`#journeyStep${i}`).addClass('active');
            }
        }
    };

    // ── Feed Live Search Filtering ─────────────────────────────────────────
    window.filterFeedPosts = function () {
        const q = ($('#feedSearchInput').val() || '').toLowerCase().trim();
        $('.competitor-post-card-col').each(function () {
            const text = $(this).text().toLowerCase();
            if (!q || text.includes(q)) {
                $(this).removeClass('d-none');
            } else {
                $(this).addClass('d-none');
            }
        });
    };

    window.selectAllFilteredPosts = function () {
        $('.competitor-post-card-col:not(.d-none) .comp-master-checkbox').prop('checked', true);
        updateSelection();
    };

    window.clearAllPostSelections = function () {
        $('.comp-master-checkbox').prop('checked', false);
        updateSelection();
    };

    // ── History Search Filtering ───────────────────────────────────────────
    window.filterHistoryList = function () {
        const q = ($('#historySearchInput').val() || '').toLowerCase().trim();
        $('#pipelineHistoryList .pipeline-timeline').each(function () {
            const text = $(this).text().toLowerCase();
            if (!q || text.includes(q)) {
                $(this).removeClass('d-none');
            } else {
                $(this).addClass('d-none');
            }
        });
    };

    function getPlatformIcon(platform) {
        if (!platform) return '<i class="fas fa-globe me-2 text-secondary"></i>';
        const p = platform.toLowerCase();
        if (p === 'linkedin') return '<i class="fab fa-linkedin me-2" style="color: #0a66c2;"></i>';
        if (p === 'twitter') return '<i class="fab fa-twitter me-2" style="color: #1da1f2;"></i>';
        if (p === 'blog') return '<i class="fas fa-rss me-2 text-warning"></i>';
        if (p === 'youtube') return '<i class="fab fa-youtube me-2 text-danger"></i>';
        if (p === 'facebook') return '<i class="fab fa-facebook me-2" style="color: #1877f2;"></i>';
        if (p === 'instagram') return '<i class="fab fa-instagram me-2 text-danger"></i>';
        return '<i class="fas fa-globe me-2 text-secondary"></i>';
    }

    function formatPromptTabs(data, uniqueId) {
        if (!data) return '';

        let captionText = '', imageText = '', videoText = '';

        if (typeof data === 'object' && !data.prompt) {
            captionText = data.caption || '';
            imageText = data.image_prompt || '';
            videoText = data.video_prompt || '';
        } else {
            let promptText = typeof data === 'string' ? data : (data.prompt || '');
            if (!promptText) return '';

            let step2 = promptText;
            const step2Match = promptText.split(/\[---\s*STEP 2:\s*CONTENT GENERATION\s*---\]?/i);
            if (step2Match.length > 1) {
                step2 = step2Match[1];
            } else {
                const step2MatchAlt = promptText.split(/STEP 2: CONTENT GENERATION/i);
                if (step2MatchAlt.length > 1) step2 = step2MatchAlt[1];
            }

            const captionMatch = step2.match(/Caption Prompt:([\s\S]*?)(?=Image Prompt:|$)/i);
            if (captionMatch) captionText = captionMatch[1].trim();

            const imageMatch = step2.match(/Image Prompt:([\s\S]*?)(?=Video Script:|$)/i);
            if (imageMatch) imageText = imageMatch[1].trim();

            const videoMatch = step2.match(/Video Script:([\s\S]*?)$/i);
            if (videoMatch) videoText = videoMatch[1].trim();

            if (!captionText && !imageText && !videoText) {
                return `<div style="white-space: pre-wrap;">${promptText.replace(/\n/g, '<br>')}</div>`;
            }
        }

        const randId = Math.floor(Math.random() * 100000) + (uniqueId || 'tmp');
        const contentBg = '#f4f8fd';
        const pipelineIdArg = (uniqueId && uniqueId !== 'tmp') ? `'${uniqueId}'` : 'null';

        return `
            <div class="prompt-tabs-container mt-3">
                <ul class="nav nav-pills mb-2 gap-2" id="pills-tab-${randId}" role="tablist">
                    <li class="nav-item" role="presentation">
                        <button class="nav-link active btn-sm rounded-pill py-1 px-3 fw-bold" id="pills-caption-tab-${randId}" data-bs-toggle="pill" data-bs-target="#pills-caption-${randId}" type="button" role="tab" aria-controls="pills-caption-${randId}" aria-selected="true"><i class="fas fa-align-left me-1"></i>Caption</button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link btn-sm rounded-pill py-1 px-3 fw-bold" id="pills-image-tab-${randId}" data-bs-toggle="pill" data-bs-target="#pills-image-${randId}" type="button" role="tab" aria-controls="pills-image-${randId}" aria-selected="false"><i class="fas fa-image me-1"></i>Image</button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link btn-sm rounded-pill py-1 px-3 fw-bold" id="pills-video-tab-${randId}" data-bs-toggle="pill" data-bs-target="#pills-video-${randId}" type="button" role="tab" aria-controls="pills-video-${randId}" aria-selected="false"><i class="fas fa-video me-1"></i>Video</button>
                    </li>
                </ul>
                <div class="tab-content border rounded-3 p-3 shadow-sm position-relative" id="pills-tabContent-${randId}" style="min-height: 200px; max-height: 400px; overflow-y: auto; background-color: ${contentBg};">
                    
                    <div class="tab-pane fade show active text-dark small prompt-pane" id="pills-caption-${randId}" role="tabpanel" aria-labelledby="pills-caption-tab-${randId}">
                        <div class="d-flex justify-content-end gap-2 position-absolute" style="top: 10px; right: 15px; z-index: 10;">
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-copy" onclick="window.copyPromptTabContent(this)" title="Copy Caption" style="font-size: 1.1rem;"><i class="far fa-copy"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-edit" onclick="window.togglePromptEdit(this)" title="Edit Caption" style="font-size: 1.1rem;"><i class="fas fa-pencil-alt"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-success btn-save d-none" onclick="window.savePromptEdit(this, ${pipelineIdArg}, 'caption')" title="Save Caption" style="font-size: 1.1rem;"><i class="fas fa-save"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-danger btn-cancel d-none" onclick="window.cancelPromptEdit(this)" title="Cancel Edit" style="font-size: 1.1rem;"><i class="fas fa-times"></i></button>
                        </div>
                        <div class="prompt-content mb-2" style="white-space: pre-wrap; padding-top: 5px; padding-right: 40px;">${captionText}</div>
                        <textarea class="form-control prompt-editor d-none w-100" style="min-height: 150px; font-size: 0.875rem;" spellcheck="false"></textarea>
                    </div>
                    
                    <div class="tab-pane fade text-dark small prompt-pane" id="pills-image-${randId}" role="tabpanel" aria-labelledby="pills-image-tab-${randId}">
                        <div class="d-flex justify-content-end gap-2 position-absolute" style="top: 10px; right: 15px; z-index: 10;">
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-copy" onclick="window.copyPromptTabContent(this)" title="Copy Image Prompt" style="font-size: 1.1rem;"><i class="far fa-copy"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-edit" onclick="window.togglePromptEdit(this)" title="Edit Image Prompt" style="font-size: 1.1rem;"><i class="fas fa-pencil-alt"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-success btn-save d-none" onclick="window.savePromptEdit(this, ${pipelineIdArg}, 'image_prompt')" title="Save Image Prompt" style="font-size: 1.1rem;"><i class="fas fa-save"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-danger btn-cancel d-none" onclick="window.cancelPromptEdit(this)" title="Cancel Edit" style="font-size: 1.1rem;"><i class="fas fa-times"></i></button>
                        </div>
                        <div class="prompt-content mb-2" style="white-space: pre-wrap; padding-top: 5px; padding-right: 40px;">${imageText}</div>
                        <textarea class="form-control prompt-editor d-none w-100" style="min-height: 150px; font-size: 0.875rem;" spellcheck="false"></textarea>
                    </div>
                    
                    <div class="tab-pane fade text-dark small prompt-pane" id="pills-video-${randId}" role="tabpanel" aria-labelledby="pills-video-tab-${randId}">
                        <div class="d-flex justify-content-end gap-2 position-absolute" style="top: 10px; right: 15px; z-index: 10;">
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-copy" onclick="window.copyPromptTabContent(this)" title="Copy Video Script" style="font-size: 1.1rem;"><i class="far fa-copy"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-muted btn-edit" onclick="window.togglePromptEdit(this)" title="Edit Video Script" style="font-size: 1.1rem;"><i class="fas fa-pencil-alt"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-success btn-save d-none" onclick="window.savePromptEdit(this, ${pipelineIdArg}, 'video_prompt')" title="Save Video Script" style="font-size: 1.1rem;"><i class="fas fa-save"></i></button>
                            <button class="btn btn-sm border-0 shadow-none p-0 text-danger btn-cancel d-none" onclick="window.cancelPromptEdit(this)" title="Cancel Edit" style="font-size: 1.1rem;"><i class="fas fa-times"></i></button>
                        </div>
                        <div class="prompt-content mb-2" style="white-space: pre-wrap; padding-top: 5px; padding-right: 40px;">${videoText}</div>
                        <textarea class="form-control prompt-editor d-none w-100" style="min-height: 150px; font-size: 0.875rem;" spellcheck="false"></textarea>
                    </div>
                    
                </div>
            </div>
        `;
    }

    window.copyPromptTabContent = function(btn) {
        const pane = btn.closest('.prompt-pane');
        const text = pane.querySelector('.prompt-content').innerText;
        
        function onSuccess() {
            const oldHtml = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-check text-success"></i>';
            setTimeout(() => { btn.innerHTML = oldHtml; }, 2000);
            showToast('Copied to clipboard!', 'success');
        }

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(onSuccess).catch(err => {
                if (window.fallbackCopyTextToClipboard) window.fallbackCopyTextToClipboard(text);
                onSuccess();
            });
        } else {
            if (window.fallbackCopyTextToClipboard) window.fallbackCopyTextToClipboard(text);
            onSuccess();
        }
    };

    window.togglePromptEdit = function (btn) {
        const pane = btn.closest('.prompt-pane');
        const content = pane.querySelector('.prompt-content');
        const editor = pane.querySelector('.prompt-editor');

        editor.value = content.innerText;

        content.classList.add('d-none');
        editor.classList.remove('d-none');

        pane.querySelector('.btn-copy').classList.add('d-none');
        pane.querySelector('.btn-edit').classList.add('d-none');
        pane.querySelector('.btn-save').classList.remove('d-none');
        pane.querySelector('.btn-cancel').classList.remove('d-none');
    };

    window.cancelPromptEdit = function (btn) {
        const pane = btn.closest('.prompt-pane');

        pane.querySelector('.prompt-content').classList.remove('d-none');
        pane.querySelector('.prompt-editor').classList.add('d-none');

        pane.querySelector('.btn-copy').classList.remove('d-none');
        pane.querySelector('.btn-edit').classList.remove('d-none');
        pane.querySelector('.btn-save').classList.add('d-none');
        pane.querySelector('.btn-cancel').classList.add('d-none');
    };

    window.savePromptEdit = function (btn, pipelineId, type) {
        const pane = btn.closest('.prompt-pane');
        const content = pane.querySelector('.prompt-content');
        const editor = pane.querySelector('.prompt-editor');
        const newText = editor.value;

        content.innerText = newText;

        // Find pipeline
        let targetPipeline = null;
        if (pipelineId) {
            targetPipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        } else if (window.activePipeline) {
            targetPipeline = window.activePipeline;
        }

        if (targetPipeline) {
            let strategyObj = {};
            // If the strategy is a raw string (old format), we need to extract current texts to build an object
            if (typeof targetPipeline.strategy === 'string' || (targetPipeline.strategy && targetPipeline.strategy.prompt)) {
                const panes = pane.closest('.tab-content').querySelectorAll('.prompt-pane');
                strategyObj = {
                    caption: panes[0].querySelector('.prompt-content').innerText,
                    image_prompt: panes[1].querySelector('.prompt-content').innerText,
                    video_prompt: panes[2].querySelector('.prompt-content').innerText
                };
            } else if (typeof targetPipeline.strategy === 'object') {
                strategyObj = targetPipeline.strategy;
            }

            // Apply edit
            strategyObj[type] = newText;
            targetPipeline.strategy = strategyObj;

            // Save to local storage if it's in history
            if (pipelineId && window.pipelineHistory) {
                localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            }
        }

        // Restore view mode
        window.cancelPromptEdit(btn);
    };

    window.navigateAssetHistory = function (pipelineId, index, dir, event) {
        if (event) event.stopPropagation();
        if (pipelineId) {
            const pIndex = window.pipelineHistory.findIndex(p => p.id === pipelineId);
            if (pIndex === -1) return;
            const pipeline = window.pipelineHistory[pIndex];
            const item = pipeline.assetContent[index];
            if (!item.history) return;
            item.historyIndex += dir;
            item.content = item.history[item.historyIndex];
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            showPipelineStageDetail(pIndex, window._currentStageId || pipeline.status);
        } else {
            const item = window.currentCarouselAssets[index];
            if (!item.history) return;
            item.historyIndex += dir;
            item.content = item.history[item.historyIndex];
            renderCarousel();
        }
    };

    window.generateStoryFromSelection = function () {
        const context = $('#storyContextInput').val();
        if (!context) return;

        // Initialize active pipeline
        const checkedLabels = $('.comp-master-checkbox:checked').map(function () {
            return $(this).data('competitor');
        }).get().join(', ');

        const characterMode = $('#characterModeSelect').length ? $('#characterModeSelect').val() : 'without_character';
        const characterAsset = (characterMode === 'with_character' && $('#characterAssetSelect').length)
            ? $('#characterAssetSelect').val()
            : 'auto';
        const characterConfig = { mode: characterMode, character: characterAsset };

        window.activePipeline = {
            id: Date.now(),
            timestamp: new Date().toISOString(),
            competitors: checkedLabels,
            context: context,
            characterConfig: characterConfig,
            status: 'intel_selected',
            strategy: null,
            assetType: null,
            assetContent: null
        };
        window.pipelineHistory.unshift(window.activePipeline);
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();

        $('#generateStoryBtn').prop('disabled', true);
        $('#storyOutputContainer').removeClass('d-none');
        $('#generationLoader').removeClass('d-none');
        $('#structuredOutput').html('');

        $.ajax({
            url: '/api/generate-channel-storyline',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                story: context,
                characterConfig: characterConfig
            }),
            success: function (r) {
                $('#generateStoryBtn').prop('disabled', false);
                $('#generationLoader').addClass('d-none');

                if (r.success && r.storyline) {
                    const data = r.storyline;
                    window.lastStrategyData = data;

                    // Render facts pills
                    let factsHtml = '';
                    if (data.observed_facts && Array.isArray(data.observed_facts)) {
                        data.observed_facts.forEach(fact => {
                            factsHtml += `<span class="badge rounded-pill bg-white text-primary border border-primary px-3 py-2 me-2 mb-2" style="font-size: 0.85rem; font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">${fact}</span>`;
                        });
                    }

                    const formattedHtml = `
                        <div class="mb-3 d-flex align-items-center gap-2">
                            <i class="fas fa-play text-muted small"></i>
                            <span class="text-muted fw-bold small text-uppercase tracking-wider">Observed facts (${data.observed_facts ? data.observed_facts.length : 0})</span>
                        </div>
                        <div class="mb-4 d-flex flex-wrap">
                            ${factsHtml}
                        </div>
                        <div class="mb-3" style="line-height: 1.6; font-size: 0.95rem;">
                            ${formatPromptTabs(data, Date.now())}
                        </div>
                    `;
                    $('#structuredOutput').html(formattedHtml);

                    // Slide to Strategy Output (Slide 2)
                    slideWorkflow(1);

                    window.activePipeline.status = 'strategy_generated';
                    window.activePipeline.strategy = data;
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();

                    showToast('Synthesis generated successfully!', 'success');
                } else {
                    $('#structuredOutput').html(`<div class="text-danger fw-bold">Error: ${r.error || 'Invalid response data'}</div>`);
                    showToast('Generation failed.', 'danger');
                    window.activePipeline.status = 'stopped_error';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();
                }
            },
            error: function (xhr) {
                $('#generateStoryBtn').prop('disabled', false);
                $('#generationLoader').addClass('d-none');
                
                const res = xhr.responseJSON || {};
                const errText = res.error || 'Network Error.';
                
                $('#structuredOutput').html(`<div class="text-danger fw-bold"><i class="fas fa-exclamation-triangle me-2"></i>Generation Error: ${escapeHtml(errText)}</div>`);
                showToast('Generation failed: ' + errText, 'danger');
                window.activePipeline.status = 'stopped_error';
                localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                renderPipelineHistory();
            }
        });
    };

    // ==========================================
    // GROWTH & EXPANSION OPPORTUNITIES
    // ==========================================
    window.opportunitySuggestions = { unserved_themes: [], domain_expansion: [] };
    window.newOpportunityTitles = new Set();

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function renderOpportunityCard(item) {
        const isNew = window.newOpportunityTitles.has((item.title || '').toLowerCase());
        return `
            <div class="p-3 mb-2 rounded-3 border" style="background: #f9fafb;">
                <div class="d-flex align-items-start justify-content-between gap-2 mb-1">
                    <h6 class="fw-bold text-dark mb-0" style="font-size: 0.9rem;">${escapeHtml(item.title)}</h6>
                    ${isNew ? '<span class="badge rounded-pill bg-success flex-shrink-0" style="font-size: 0.6rem;">New</span>' : ''}
                </div>
                <p class="text-muted small mb-1" style="line-height: 1.5;">${escapeHtml(item.description)}</p>
                ${item.source_accounts ? `<small class="text-muted"><i class="fas fa-building-columns me-1"></i>${escapeHtml(item.source_accounts)}</small>` : ''}
            </div>
        `;
    }

    function renderOpportunityLists() {
        const themes = window.opportunitySuggestions.unserved_themes || [];
        const domains = window.opportunitySuggestions.domain_expansion || [];

        $('#opportunityUnservedList').html(themes.length
            ? themes.map(renderOpportunityCard).join('')
            : '<p class="text-muted small">No whitespace opportunities found yet.</p>');

        $('#opportunityDomainList').html(domains.length
            ? domains.map(renderOpportunityCard).join('')
            : '<p class="text-muted small">No domain expansion ideas found yet.</p>');

        $('#opportunityCountBadge').text(themes.length + domains.length);
    }

    window.loadOpportunitySuggestions = function () {
        $.ajax({
            url: '/api/opportunity-suggestions',
            type: 'GET',
            success: function (r) {
                if (r.success && r.suggestions) {
                    window.opportunitySuggestions = r.suggestions;
                    renderOpportunityLists();
                }
            }
        });
    };

    window.openOpportunityModal = function () {
        renderOpportunityLists();
        const modalEl = document.getElementById('opportunityModal');
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    };

    window.generateOpportunitySuggestions = function () {
        const context = $('#storyContextInput').val();
        if (!context) {
            showToast('Select competitor posts from the feed first.', 'warning');
            return;
        }
        const accounts = $('.comp-master-checkbox:checked').map(function () {
            return $(this).data('competitor');
        }).get().join(', ');

        $('#findOpportunitiesBtn').prop('disabled', true);
        $('#opportunityGenLoader').removeClass('d-none');

        $.ajax({
            url: '/api/generate-opportunity-suggestions',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: context, accounts: accounts }),
            success: function (r) {
                $('#findOpportunitiesBtn').prop('disabled', false);
                $('#opportunityGenLoader').addClass('d-none');

                if (r.success) {
                    window.newOpportunityTitles = new Set((r.db && r.db.new_titles || []).map(t => t.toLowerCase()));
                    if (r.db && r.db.inserted > 0) {
                        showToast(`Found ${r.db.inserted} new opportunit${r.db.inserted === 1 ? 'y' : 'ies'}.`, 'success');
                    } else {
                        showToast('No new opportunities found this run.', 'info');
                    }
                    loadOpportunitySuggestions();
                } else {
                    showToast('Failed to generate opportunities.', 'danger');
                }
            },
            error: function () {
                $('#findOpportunitiesBtn').prop('disabled', false);
                $('#opportunityGenLoader').addClass('d-none');
                showToast('Network error generating opportunities.', 'danger');
            }
        });
    };

    loadOpportunitySuggestions();

    window.copyStoryOutput = function () {
        const text = $('#storyOutput').val();
        if (!text) return;

        navigator.clipboard.writeText(text).then(() => {
            showToast('Copied to clipboard!', 'success');
        });
    }

    window.slideWorkflow = function (stepIndex) {
        // stepIndex: 0 = Context, 1 = Strategy, 2 = Generation Settings, 3 = Asset Review
        const translation = -(stepIndex * 25);
        $('#workflowSlider').css('transform', `translateX(${translation}%)`);

        if (stepIndex === 0) {
            if (window.activePipeline && window.activePipeline.strategy) {
                $('#slide1NextBtn').removeClass('d-none');
            } else {
                $('#slide1NextBtn').addClass('d-none');
            }
        } else if (stepIndex === 1) {
            if (window.activePipeline && window.activePipeline.assetContent) {
                $('#slide2NextBtn').removeClass('d-none');
            } else {
                $('#slide2NextBtn').addClass('d-none');
            }
        } else if (stepIndex === 2) {
            if (window.activePipeline && window.activePipeline.assetContent) {
                $('#slide3NextBtn').removeClass('d-none');
            } else {
                $('#slide3NextBtn').addClass('d-none');
            }
        }
    };

    window.approveStrategy = function () {
        if (!window.lastStrategyData) return;
        slideWorkflow(2); // Slide to Generation (Slide 3)
    };

    // ==========================================
    // PIPELINE WORKFLOW (History, Generation, Approval)
    // ==========================================

    // Store history in memory/localStorage
    window.pipelineHistory = JSON.parse(localStorage.getItem('straditPipelineHistory') || '[]');

    const PIPELINE_STAGES = [
        { id: 'intel_selected', label: 'Post Pipeline', icon: 'fa-check' },
        { id: 'strategy_generated', label: 'Counter Strategy Generated', icon: 'fa-brain' },
        { id: 'asset_generated', label: 'Content Generated', icon: 'fa-magic' },
        { id: 'approved', label: 'Asset Approved', icon: 'fa-thumbs-up' },
        { id: 'published', label: 'Published', icon: 'fa-paper-plane' }
    ];

    // Mirrors the full history list as a compact icon + id strip for the
    // collapsed rail, so history stays reachable while the panel is hidden.
    function renderCollapsedHistoryRail() {
        const rail = $('#collapsedHistoryList');
        if (window.pipelineHistory.length === 0) {
            rail.html('');
            return;
        }

        let html = '';
        window.pipelineHistory.forEach((pipeline, index) => {
            const pipelineStatus = pipeline.status || 'unknown';
            const hasError = pipelineStatus.startsWith('stopped') || pipelineStatus === 'rejected';
            const isDone = pipelineStatus === 'published' || pipelineStatus === 'approved';
            const badgeClass = hasError ? 'bg-danger' : (isDone ? 'bg-success' : 'bg-primary');
            const icon = hasError ? 'fa-times' : (isDone ? 'fa-check' : 'fa-layer-group');
            const shortId = String(pipeline.id).slice(-4);
            const generatedDate = pipeline.timestamp ? new Date(pipeline.timestamp).toLocaleString() : '';

            html += `
                <button type="button" class="btn p-0 border-0 bg-transparent d-flex flex-column align-items-center gap-1 flex-shrink-0"
                    title="ID: ${pipeline.id}${generatedDate ? ' • Generated ' + generatedDate : ''}" onclick="openPipelineModal(${index})">
                    <span class="rounded-circle ${badgeClass} d-flex align-items-center justify-content-center shadow-sm" style="width: 22px; height: 22px;">
                        <i class="fas ${icon} text-white" style="font-size: 9px;"></i>
                    </span>
                    <span class="text-muted" style="font-size: 0.6rem; line-height: 1;">${shortId}</span>
                </button>
            `;
        });
        rail.html(html);
    }

    function renderPipelineHistory() {
        renderCollapsedHistoryRail();

        const container = $('#pipelineHistoryList');
        if (window.pipelineHistory.length === 0) {
            container.html(`
                <div class="empty-state p-4 text-center">
                    <i class="fas fa-clipboard-list mb-3" style="font-size: 2rem; color: #cbd5e1;"></i>
                    <p class="text-muted small">No active pipelines yet.</p>
                </div>
            `);
            return;
        }

        let html = '<div class="list-group list-group-flush">';
        window.pipelineHistory.forEach((pipeline, index) => {
            const date = new Date(pipeline.timestamp).toLocaleString();

            let timelineHtml = `<div class="pipeline-timeline mt-3" onclick="openPipelineModal(${index})" title="Click to view stage details">`;
            let reachedStatus = true;
            let pipelineStatus = pipeline.status || 'unknown';
            let hasError = pipelineStatus.startsWith('stopped') || pipelineStatus === 'rejected';

            PIPELINE_STAGES.forEach((step, stepIdx) => {
                let badgeClass = 'bg-secondary';
                let textClass = 'text-muted';
                let stepIcon = step.icon;
                const isLast = stepIdx === PIPELINE_STAGES.length - 1;

                if (reachedStatus) {
                    badgeClass = 'bg-primary';
                    textClass = 'text-dark fw-bold';
                }

                if (pipelineStatus === step.id) {
                    reachedStatus = false;
                    if (hasError) {
                        badgeClass = 'bg-danger';
                        textClass = 'text-danger fw-bold';
                        stepIcon = 'fa-times';
                    }
                }

                if (hasError && !reachedStatus && pipelineStatus !== step.id) {
                    // skip remaining
                    badgeClass = 'bg-light border text-muted';
                }

                timelineHtml += `
                    <div class="d-flex align-items-stretch">
                        <div class="d-flex flex-column align-items-center" style="width: 24px;">
                            <div class="rounded-circle ${badgeClass} d-flex align-items-center justify-content-center shadow-sm flex-shrink-0" style="width: 20px; height: 20px;">
                                <i class="fas ${stepIcon} text-white" style="font-size: 9px;"></i>
                            </div>
                            ${!isLast ? '<div class="flex-grow-1" style="width: 2px; background: #e5e7eb; min-height: 14px;"></div>' : ''}
                        </div>
                        <div class="${textClass} small lh-sm ps-2 pb-2" style="font-size: 0.78rem;">${step.label}</div>
                    </div>
                `;
            });
            timelineHtml += '</div>';

            const competitorsList = pipeline.competitors
                ? [...new Set(pipeline.competitors.split(',').map(c => c.trim()).filter(Boolean))].join(', ')
                : 'None';

            html += `
                <div class="list-group-item list-group-item-action p-3 border-bottom bg-light bg-opacity-50">
                    <div class="mb-2">
                        <h6 class="mb-0 fw-bold text-dark text-truncate" style="font-size: 0.85rem;" title="Pipeline ID: ${pipeline.id}"><i class="fas fa-layer-group me-2 text-primary"></i>ID: ${pipeline.id}</h6>
                        <small class="text-muted" style="font-size: 0.7rem;">${date}</small>
                    </div>
                    <p class="mb-1 text-muted small"><strong>Analysis:</strong> ${competitorsList}</p>
                    <p class="mb-1 text-muted small"><strong>Asset:</strong> ${pipeline.assetType || 'Pending'}</p>
                    ${timelineHtml}
                    ${pipelineStatus === 'approved' || pipelineStatus === 'published' || pipelineStatus === 'asset_generated' ?
                    `<button class="btn btn-sm btn-outline-primary mt-2 py-1 px-3 rounded-pill fw-bold" onclick="event.stopPropagation(); viewHistoryItem(${index})" style="font-size: 0.8rem;">View Pipeline Content</button>` : ''}
                </div>
            `;
        });
        html += '</div>';
        container.html(html);
    }

    window.viewHistoryItem = function (index) {
        const pipeline = window.pipelineHistory[index];
        if (!pipeline) return;

        const item = pipeline.assetContent; // this might be an array or single item
        if (!item) return;

        let dispItem = Array.isArray(item) ? item[0] : item; // Fallback for carousel rendering if needed

        window.currentCarouselAssets = Array.isArray(item) ? item : [item];
        renderCarousel();

        $('#pipelineResultBlock').removeClass('d-none');
        $('#approvalButtons').addClass('d-none');

        if (pipeline.status === 'published') {
            $('#publishPipelineBtn').addClass('d-none');
        } else {
            $('#publishPipelineBtn').removeClass('d-none');
        }

        window.lastGeneratedPipeline = dispItem; // Load it into state
        window.activePipeline = pipeline; // Set it as active

        slideWorkflow(3); // Slide to the Asset Review page
    };

    function emptyStageState(msg) {
        return `<div class="text-center text-muted py-5"><i class="fas fa-hourglass-half mb-3" style="font-size: 1.75rem; opacity: 0.4;"></i><p class="small m-0">${msg}</p></div>`;
    }

    function renderAssetItems(assetContent, pipelineId = null, isFinal = false) {
        const items = Array.isArray(assetContent) ? assetContent : [assetContent];
        const html = items.map((a, index) => {
            const type = (a.type || '').toLowerCase();
            if (type.includes('video')) {
                return `<video controls class="w-100 rounded-3 mb-2" src="${a.content}"></video>`;
            }
            if (type.includes('image')) {
                // Ensure history is initialized
                if (!a.history) {
                    a.history = [a.content];
                    a.historyIndex = 0;
                }
                let navHtml = '';
                if (a.history.length > 1 && !isFinal) {
                    navHtml = `
                        <div class="d-flex gap-2 align-items-center px-2 py-1 rounded me-2" style="background: transparent;">
                            <i class="fas fa-chevron-left text-white" style="text-shadow: 0 2px 4px rgba(0,0,0,0.8); ${a.historyIndex === 0 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;'}" ${a.historyIndex > 0 ? `onclick="navigateAssetHistory(${pipelineId}, ${index}, -1, event)"` : ''} title="Previous"></i>
                            <span class="small text-white fw-bold" style="font-size: 0.85rem; text-shadow: 0 2px 4px rgba(0,0,0,0.8);">${a.historyIndex + 1}/${a.history.length}</span>
                            <i class="fas fa-chevron-right text-white" style="text-shadow: 0 2px 4px rgba(0,0,0,0.8); ${a.historyIndex === a.history.length - 1 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;'}" ${a.historyIndex < a.history.length - 1 ? `onclick="navigateAssetHistory(${pipelineId}, ${index}, 1, event)"` : ''} title="Next"></i>
                        </div>
                    `;
                }
                let regenHtml = '';
                if (pipelineId && !isFinal) {
                    regenHtml = `
                        <button class="btn btn-sm text-white border-0 shadow-none p-1" id="modalRegenImgBtn_${pipelineId}_${index}" onclick="regenerateModalImage(${pipelineId}, ${index}, event)" title="Regenerate with original context" style="background: transparent;">
                            <i class="fas fa-sync-alt" style="font-size: 1.1rem; text-shadow: 0 2px 4px rgba(0,0,0,0.8);"></i>
                        </button>
                    `;
                }

                return `
                <div class="position-relative mb-2">
                    <img src="${a.content}" class="w-100 rounded-3" alt="Generated asset">
                    <div class="position-absolute d-flex gap-2 align-items-center" style="bottom: 15px; right: 15px; z-index: 10;">
                        ${navHtml}
                        ${regenHtml}
                    </div>
                </div>
                `;
            }
            // Ensure history is initialized
            if (!a.history) {
                a.history = [a.content];
                a.historyIndex = 0;
            }

            let navHtml = '';
            if (a.history.length > 1 && !isFinal) {
                const pDisabled = a.historyIndex === 0 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;';
                const nDisabled = a.historyIndex === a.history.length - 1 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;';
                navHtml = `
                    <div class="d-flex gap-2 ms-3 border-start ps-3 align-items-center">
                        <i class="fas fa-chevron-left text-muted" style="${pDisabled}" ${a.historyIndex > 0 ? `onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="navigateAssetHistory(${pipelineId}, ${index}, -1, event)"` : ''} title="Previous"></i>
                        <span class="small text-muted" style="font-size: 0.75rem;">${a.historyIndex + 1}/${a.history.length}</span>
                        <i class="fas fa-chevron-right text-muted" style="${nDisabled}" ${a.historyIndex < a.history.length - 1 ? `onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="navigateAssetHistory(${pipelineId}, ${index}, 1, event)"` : ''} title="Next"></i>
                    </div>
                `;
            }

            return `
                <div class="position-relative mb-2">
                    <div class="position-absolute" style="top: 15px; right: 20px; font-size: 1.1rem; z-index: 10;">
                        <i class="far fa-copy text-muted" style="cursor:pointer; transition: all 0.2s;" onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="copyCaptionText(this)" title="Copy Caption"></i>
                    </div>
                    <div class="d-flex justify-content-start gap-3 position-absolute align-items-center" style="bottom: 15px; left: 20px; font-size: 1.1rem; z-index: 10;">
                        ${pipelineId && !isFinal ? `<i class="fas fa-sync-alt text-muted" style="cursor:pointer; transition: color 0.2s;" onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="regenerateModalCaptionText(${pipelineId}, ${index}, event)" title="Regenerate Caption"></i>` : ''}
                        ${navHtml}
                    </div>
                    <div class="bg-light rounded-3 p-3 pb-5 caption-text-content" style="white-space: pre-wrap; font-size: 0.75rem; line-height: 1.4; color: #334155;">${a.content}</div>
                </div>
            `;
        }).join('');
        
        const hasImages = items.some(a => (a.type || '').toLowerCase().includes('image'));
        if (hasImages) {
            const imageUrls = items.filter(a => (a.type || '').toLowerCase().includes('image')).map(a => a.content);
            const btnHtml = `
            <button class="btn btn-sm btn-outline-primary w-100 mt-3" onclick='downloadAllAsZip(${JSON.stringify(imageUrls)})'>
                <i class="fas fa-file-archive me-1"></i>Download All Images (ZIP)
            </button>`;
            return html + btnHtml;
        }
        return html;
    }

    // Builds a plain-text seed message summarizing a pipeline's generated
    // content, for handing off to Studio Chat as a fresh conversation.
    function buildStudioChatSeedText(pipeline) {
        const parts = ['Here is content I generated from a competitor counter-strategy on the Analysis Dashboard. Please help me refine it further.'];

        if (pipeline.context) {
            parts.push(`--- STRATEGY CONTEXT ---\n${pipeline.context}`);
        }

        const assets = Array.isArray(pipeline.assetContent) ? pipeline.assetContent : (pipeline.assetContent ? [pipeline.assetContent] : []);
        assets.forEach((item, idx) => {
            const label = item.type === 'Text (Caption)' ? 'GENERATED CAPTION' : `GENERATED ${(item.type || 'ASSET').toUpperCase()}`;
            const platformTag = item.platform ? ` (${item.platform})` : '';
            let body = item.type === 'Text (Caption)' ? item.content : (item.caption || item.content || '');
            if (item.type !== 'Text (Caption)' && item.content) {
                body += `\n[Asset URL: ${item.content}]`;
            }
            parts.push(`--- ${label}${platformTag} ${assets.length > 1 ? `#${idx + 1}` : ''} ---\n${body}`.trim());
        });

        return parts.join('\n\n');
    }

    // Hands a pipeline's generated content off to Studio Chat as a new
    // conversation. Studio Chat is a separate page, so the seed is passed via
    // localStorage and consumed once on load there (see app.js).
    window.sendPipelineToStudioChat = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline || !pipeline.assetContent) {
            showToast('No generated content to send yet.', 'warning');
            return;
        }

        try {
            localStorage.setItem('incomingStudioChatSeed', JSON.stringify({
                text: buildStudioChatSeedText(pipeline),
                sourcePipelineId: pipeline.id,
                createdAt: new Date().toISOString()
            }));
        } catch (e) {
            showToast('Could not prepare handoff to Studio Chat.', 'danger');
            return;
        }

        window.location.href = '/';
    };

    // Returns {body, footer}: body renders inside the scrollable
    // #pipelineModalDetail, footer renders inside the fixed #pipelineModalFooter
    // bar pinned to the bottom of the panel - so Reject/Approve/Publish/etc.
    // stay reachable without scrolling, however long the content above is.
    function getStageDetailHtml(pipeline, stageId) {
        if (stageId === 'intel_selected') {
            const competitorsList = pipeline.competitors
                ? [...new Set(pipeline.competitors.split(',').map(c => c.trim()).filter(Boolean))].join(', ')
                : 'None';
            const safeContext = (pipeline.context || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            const body = `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-layer-group me-2"></i>Post Pipeline</h6>
                <p class="small text-muted mb-2"><strong class="text-dark">Analysis:</strong> ${competitorsList}</p>

                <div id="intelSelectedReadMode">
                    <div class="bg-light rounded-3 p-3 small" style="white-space: pre-wrap; max-height: 320px; overflow-y: auto;">${pipeline.context || 'No context captured for this pipeline.'}</div>
                    <div class="mt-3 d-flex gap-2">
                        <button class="btn btn-sm btn-outline-primary fw-bold" onclick="$('#intelSelectedReadMode').addClass('d-none'); $('#intelSelectedEditMode').removeClass('d-none');"><i class="fas fa-edit me-1"></i>Edit Context</button>
                    </div>
                </div>

                <div id="intelSelectedEditMode" class="d-none">
                    <textarea class="form-control textarea-premium small mb-2" id="editPipelineContextArea" style="min-height: 320px;">${safeContext}</textarea>
                    <div class="d-flex gap-2 justify-content-end">
                        <button class="btn btn-sm btn-light fw-bold" onclick="$('#intelSelectedEditMode').addClass('d-none'); $('#intelSelectedReadMode').removeClass('d-none');">Cancel</button>
                        <button class="btn btn-sm btn-success fw-bold" onclick="saveAndRerunPipelineStrategy(${pipeline.id})"><i class="fas fa-save me-1"></i>Save & Rerun</button>
                    </div>
                </div>
            `;
            return { body, footer: '' };
        }
        if (stageId === 'strategy_generated') {
            if (!pipeline.strategy) return { body: emptyStageState('Counter strategy has not been generated yet.'), footer: '' };
            const facts = (pipeline.strategy.observed_facts || [])
                .map(f => `<span class="badge rounded-pill bg-white text-primary border border-primary px-3 py-2 me-2 mb-2 text-wrap text-start" style="font-size: 0.8rem; font-weight: 600; line-height: 1.4;">${f}</span>`)
                .join('');

            const body = `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-brain me-2"></i>Counter Strategy Generated</h6>
                <div class="mb-3 d-flex flex-wrap">${facts || '<span class="text-muted small">No observed facts recorded.</span>'}</div>
                <div class="mb-3 w-100">${formatPromptTabs(pipeline.strategy, pipeline.id)}</div>
            `;
            const footer = `
                <button class="btn btn-outline-danger action-btn flex-grow-1" onclick="rejectPipelineStrategy(${pipeline.id})"><i class="fas fa-times me-1"></i>Reject</button>
                <button class="btn btn-success action-btn flex-grow-1" onclick="approvePipelineStrategy(${pipeline.id})"><i class="fas fa-check me-1"></i>Approve & Continue</button>
            `;
            return { body, footer };
        }
        if (stageId === 'asset_generated') {
            if (!pipeline.assetContent) return { body: emptyStageState('Content has not been generated yet.'), footer: '' };
            const body = `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-magic me-2"></i>Content Generated ${pipeline.assetType ? `<span class="badge bg-light text-dark border ms-1">${pipeline.assetType}</span>` : ''}</h6>
                <div style="max-height: 75vh; overflow-y: auto;" class="mb-3">${renderAssetItems(pipeline.assetContent, pipeline.id)}</div>
            `;
            const footer = `
                <button class="btn btn-outline-danger action-btn flex-grow-1" onclick="rejectPipelineAsset(${pipeline.id})"><i class="fas fa-times me-1"></i>Reject Asset</button>
                <button class="btn btn-success action-btn flex-grow-1" onclick="approvePipelineAsset(${pipeline.id})"><i class="fas fa-check me-1"></i>Approve Asset</button>
                <button class="btn btn-outline-primary action-btn flex-grow-1" onclick="sendPipelineToStudioChat(${pipeline.id})">
                    <i class="fas fa-comments me-1"></i>Refine in Studio Chat
                </button>
            `;
            return { body, footer };
        }
        if (stageId === 'approved') {
            if (pipeline.status !== 'approved' && pipeline.status !== 'published') return { body: emptyStageState('This asset has not been approved yet.'), footer: '' };
            const body = `
                <h6 class="fw-bold text-success mb-3"><i class="fas fa-thumbs-up me-2"></i>Asset Approved</h6>
                <p class="small text-muted">This asset was reviewed and approved for publishing.</p>
                ${pipeline.assetContent ? `<div style="max-height: 320px; overflow-y: auto;" class="mb-3">${renderAssetItems(pipeline.assetContent, pipeline.id, true)}</div>` : ''}
            `;
            const footer = pipeline.status === 'approved' ? `
                <button class="btn btn-dark action-btn flex-grow-1" onclick="publishModalPipelineContent(${pipeline.id})" id="modalPublishBtn">
                    <i class="fas fa-paper-plane me-2"></i>Publish to Platforms
                </button>
            ` : '';
            return { body, footer };
        }
        if (stageId === 'published') {
            if (pipeline.status !== 'published') return { body: emptyStageState('This pipeline has not been published yet.'), footer: '' };
            const body = `
                <h6 class="fw-bold text-dark mb-3"><i class="fas fa-paper-plane me-2"></i>Published</h6>
                <p class="small text-muted">This content has been published live.</p>
                ${pipeline.assetContent ? `<div style="max-height: 320px; overflow-y: auto;" class="mb-3">${renderAssetItems(pipeline.assetContent, pipeline.id, true)}</div>` : ''}
            `;
            return { body, footer: '' };
        }
        return { body: emptyStageState('No details available for this stage.'), footer: '' };
    }

    window.openPipelineModal = function (index) {
        const pipeline = window.pipelineHistory[index];
        if (!pipeline) return;

        // Default the detail view to the furthest reached stage
        const pipelineStatus = pipeline.status || 'unknown';
        const reachedIdx = Math.max(0, PIPELINE_STAGES.findIndex(s => s.id === pipelineStatus));

        const hasError = pipelineStatus.startsWith('stopped') || pipelineStatus === 'rejected';
        const stageLabel = (PIPELINE_STAGES.find(s => s.id === pipelineStatus) || {}).label;
        const badgeText = hasError ? 'Stopped' : (stageLabel || 'In Progress');
        const badgeClass = hasError ? 'bg-danger-subtle text-danger' : (pipelineStatus === 'published' ? 'bg-success-subtle text-success' : 'bg-primary-subtle text-primary');

        $('#pipelineModalTitle').text('Pipeline #' + String(pipeline.id).slice(-4)).attr('title', 'Full ID: ' + pipeline.id);
        $('#pipelineModalStatusBadge').text(badgeText).attr('class', 'badge rounded-pill ' + badgeClass);
        $('#pipelineModalSubtitle').text(new Date(pipeline.timestamp).toLocaleString());
        $('#pipelineModalStepper').data('pipeline-index', index);

        renderPipelineModalStepper(index, PIPELINE_STAGES[reachedIdx].id);
        showPipelineStageDetail(index, PIPELINE_STAGES[reachedIdx].id);

        const modalEl = document.getElementById('pipelineStageModal');
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
    };

    function renderPipelineModalStepper(index, activeStageId) {
        const pipeline = window.pipelineHistory[index];
        const pipelineStatus = pipeline.status || 'unknown';
        const hasError = pipelineStatus.startsWith('stopped') || pipelineStatus === 'rejected';

        let reachedStatus = true;
        let stepperHtml = '<div class="pipeline-timeline">';
        PIPELINE_STAGES.forEach((step, stepIdx) => {
            let badgeClass = 'bg-secondary';
            let textClass = 'text-muted';
            let stepIcon = step.icon;
            const isReached = reachedStatus;

            if (reachedStatus) {
                badgeClass = 'bg-primary';
                textClass = 'text-dark fw-bold';
            }
            if (pipelineStatus === step.id) {
                reachedStatus = false;
                if (hasError) {
                    badgeClass = 'bg-danger';
                    textClass = 'text-danger fw-bold';
                    stepIcon = 'fa-times';
                }
            }
            if (hasError && !reachedStatus && pipelineStatus !== step.id) {
                badgeClass = 'bg-light border text-muted';
            }
            const isViewing = step.id === activeStageId;
            if (isViewing) {
                textClass += ' text-primary';
            }

            // Stages the pipeline hasn't reached yet aren't clickable - guides
            // the journey step-by-step instead of letting users click ahead
            // into an empty "not generated yet" state with no explanation.
            const stateClass = isReached ? 'pipeline-stage-row-clickable' : 'pipeline-stage-row-locked';
            const rowClick = isReached
                ? `onclick="showPipelineStageDetail(${index}, '${step.id}')"`
                : `onclick="showToast('Complete the previous step first.', 'info')"`;
            const rowTitle = isReached ? '' : 'title="Complete the previous step first"';
            const viewingClass = isViewing ? ' pipeline-stage-row-active' : '';

            stepperHtml += `
                <div class="pipeline-stage-row ${stateClass}${viewingClass}" ${rowClick} ${rowTitle}>
                    <span class="pipeline-step-dot rounded-circle ${badgeClass} shadow-sm">
                        <i class="fas ${isReached ? stepIcon : 'fa-lock'} text-white"></i>
                    </span>
                    <div class="${textClass} pipeline-step-label">${step.label}</div>
                </div>
            `;
        });
        stepperHtml += '</div>';
        $('#pipelineModalStepper').html(stepperHtml);
    }

    window.showPipelineStageDetail = function (index, stageId) {
        const pipeline = window.pipelineHistory[index];
        if (!pipeline) return;

        window._currentStageId = stageId;

        renderPipelineModalStepper(index, stageId);
        const { body, footer } = getStageDetailHtml(pipeline, stageId);
        $('#pipelineModalDetail').html(body);
        $('#pipelineModalFooter').html(footer).toggleClass('d-none', !footer);
    };

    renderPipelineHistory();

    window.currentCarouselAssets = [];

    window.renderCarousel = function () {
        if (window.currentCarouselAssets.length === 0) return;

        // Update header dynamically
        const firstItem = window.currentCarouselAssets[0];
        let headerText = 'Generated Asset';
        if (firstItem.type === 'Text (Caption)') headerText = 'Generated Caption';
        else if (firstItem.type === 'image') headerText = 'Generated Image';
        else if (firstItem.type === 'video') headerText = 'Generated Video';

        $('#pipelineResultBlock h6').html(`<i class="fas fa-sparkles me-1"></i>${headerText}`);

        let indicators = '';
        let innerHtml = '';

        window.currentCarouselAssets.forEach((item, index) => {
            const activeClass = index === window.currentCarouselAssets.length - 1 ? 'active' : '';
            indicators += `<button type="button" data-bs-target="#generationCarousel" data-bs-slide-to="${index}" class="${activeClass}" aria-current="${activeClass ? 'true' : 'false'}" aria-label="Slide ${index + 1}"></button>`;

            let outHtml = '';
            if (item.type === 'Text (Caption)') {
                if (!item.history) {
                    item.history = [item.content];
                    item.historyIndex = 0;
                }

                let navHtml = '';
                if (item.history.length > 1) {
                    const pDisabled = item.historyIndex === 0 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;';
                    const nDisabled = item.historyIndex === item.history.length - 1 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;';
                    navHtml = `
                        <div class="d-flex gap-2 ms-3 border-start ps-3 align-items-center">
                            <i class="fas fa-chevron-left text-muted" style="${pDisabled}" ${item.historyIndex > 0 ? `onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="navigateAssetHistory(null, ${index}, -1, event)"` : ''} title="Previous"></i>
                            <span class="small text-muted" style="font-size: 0.75rem;">${item.historyIndex + 1}/${item.history.length}</span>
                            <i class="fas fa-chevron-right text-muted" style="${nDisabled}" ${item.historyIndex < item.history.length - 1 ? `onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="navigateAssetHistory(null, ${index}, 1, event)"` : ''} title="Next"></i>
                        </div>
                    `;
                }

                outHtml = `
                    <div class="position-relative">
                        <div class="position-absolute" style="top: 15px; right: 20px; font-size: 1.1rem; z-index: 10;">
                            <i class="far fa-copy text-muted" style="cursor:pointer; transition: all 0.2s;" onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="copyCaptionText(this)" title="Copy Caption"></i>
                        </div>
                        <div class="d-flex justify-content-start gap-3 position-absolute align-items-center" style="bottom: 15px; left: 20px; font-size: 1.1rem; z-index: 10;">
                            <i class="fas fa-sync-alt text-muted" style="cursor:pointer; transition: color 0.2s;" onmouseover="this.classList.remove('text-muted'); this.classList.add('text-primary');" onmouseout="this.classList.remove('text-primary'); this.classList.add('text-muted');" onclick="regenerateCaptionText(${index}, event)" title="Regenerate Caption"></i>
                            ${navHtml}
                        </div>
                        <div class="p-3 pb-5 text-dark caption-text-content" style="background-color: #f8fafc; border-radius: 8px; max-height: calc(100vh - 340px); min-height: 150px; overflow-y: auto !important; white-space: pre-wrap; font-size: 0.875rem; line-height: 1.65; color: #334155;">${item.content}</div>
                    </div>
                `;
            } else if (item.type === 'image') {
                if (!item.history) {
                    item.history = [item.content];
                    item.historyIndex = 0;
                }
                let navHtml = '';
                if (item.history.length > 1) {
                    navHtml = `
                        <div class="d-flex gap-2 align-items-center me-2 px-2 py-1 rounded" style="background: transparent;">
                            <i class="fas fa-chevron-left text-white" style="text-shadow: 0 2px 4px rgba(0,0,0,0.8); ${item.historyIndex === 0 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;'}" ${item.historyIndex > 0 ? `onclick="navigateAssetHistory(null, ${index}, -1, event)"` : ''} title="Previous"></i>
                            <span class="small text-white fw-bold" style="font-size: 0.85rem; text-shadow: 0 2px 4px rgba(0,0,0,0.8);">${item.historyIndex + 1}/${item.history.length}</span>
                            <i class="fas fa-chevron-right text-white" style="text-shadow: 0 2px 4px rgba(0,0,0,0.8); ${item.historyIndex === item.history.length - 1 ? 'opacity: 0.3; cursor: not-allowed;' : 'cursor: pointer; transition: color 0.2s;'}" ${item.historyIndex < item.history.length - 1 ? `onclick="navigateAssetHistory(null, ${index}, 1, event)"` : ''} title="Next"></i>
                        </div>
                    `;
                }

                outHtml = `
                    <div class="position-relative border rounded-top">
                        <img src="${item.content}" class="d-block w-100 rounded-top" style="object-fit: cover; max-height: 350px;">
                        
                        <div class="position-absolute d-flex gap-2 align-items-center" style="bottom: 15px; right: 15px; z-index: 10;">
                            ${navHtml}
                            <button class="btn btn-sm text-white border-0 shadow-none p-1" id="regenImgBtn_${index}" onclick="regenerateImageInCarousel(${index})" title="Regenerate with original context" style="background: transparent;">
                                <i class="fas fa-sync-alt" style="font-size: 1.1rem; text-shadow: 0 2px 4px rgba(0,0,0,0.8);"></i>
                            </button>
                        </div>
                        
                        <div class="p-3 bg-light border-top"><p class="small text-muted m-0"><strong>Caption:</strong> ${item.caption}</p></div>
                    </div>
                `;
            } else if (item.type === 'video') {
                outHtml = `<video controls autoplay loop class="d-block w-100 rounded-top" style="max-height: 350px;"><source src="${item.content}" type="video/mp4"></video>
                           <div class="p-3 bg-light border-top"><p class="small text-muted m-0"><strong>Caption:</strong> ${item.caption}</p></div>`;
            }

            innerHtml += `
                <div class="carousel-item ${activeClass}">
                    ${outHtml}
                    <div class="px-3 pt-3">
                        <button class="btn btn-outline-primary w-100 fw-bold rounded-pill mb-2" onclick='downloadAllAsZip(${JSON.stringify(item.history.map(h => h.content))})'>
                            <i class="fas fa-file-archive me-1"></i>Download All Variations (ZIP)
                        </button>
                    </div>
                    <div class="d-flex gap-2 mt-2 mb-2 px-3 pb-2">
                        <button class="btn btn-outline-danger action-btn flex-grow-1" onclick="rejectPipelineContent()"><i class="fas fa-times me-1"></i>Reject</button>
                        <button class="btn btn-success action-btn flex-grow-1 shadow-sm" onclick="approveCarouselItem(${index})"><i class="fas fa-check me-2"></i>Approve</button>
                    </div>
                </div>
            `;
        });

        const carouselHtml = `
            <div id="generationCarousel" class="carousel slide" data-bs-ride="false">
              <div class="carousel-indicators bg-dark rounded-pill py-1 mb-0" style="bottom: -15px;">
                ${indicators}
              </div>
              <div class="carousel-inner rounded-3 border" style="background: #fff;">
                ${innerHtml}
              </div>
              <button class="carousel-control-prev" type="button" data-bs-target="#generationCarousel" data-bs-slide="prev" style="width: 5%; background: rgba(0,0,0,0.1); margin-left: -20px; border-radius: 10px;">
                <span class="carousel-control-prev-icon" aria-hidden="true" style="filter: invert(1);"></span>
                <span class="visually-hidden">Previous</span>
              </button>
              <button class="carousel-control-next" type="button" data-bs-target="#generationCarousel" data-bs-slide="next" style="width: 5%; background: rgba(0,0,0,0.1); margin-right: -20px; border-radius: 10px;">
                <span class="carousel-control-next-icon" aria-hidden="true" style="filter: invert(1);"></span>
                <span class="visually-hidden">Next</span>
              </button>
            </div>
        `;

        $('#pipelineOutputContent').html(carouselHtml);
        $('#pipelineResultBlock').removeClass('d-none');
        $('#approvalButtons').addClass('d-none');
        $('#publishPipelineBtn').addClass('d-none');

        // Initialize carousel explicitly since it's dynamically added
        const carouselEl = document.getElementById('generationCarousel');
        if (carouselEl) {
            new bootstrap.Carousel(carouselEl, {
                interval: false,
                wrap: true
            });
        }
    }

    // Fires the HTML approval-notification email (story context + generated
    // asset, if it's an image) whenever a piece of content is approved.
    // Best-effort: failures are logged, never surfaced as an approval error -
    // the asset is already approved regardless of whether the email sends.
    function sendApprovalEmail(pipeline, item) {
        if (!pipeline || !item) return;

        const competitors = pipeline.competitors
            ? [...new Set(pipeline.competitors.split(',').map(c => c.trim()).filter(Boolean))]
            : [];
        const caption = item.type === 'Text (Caption)' ? item.content : (item.caption || '');

        $.ajax({
            url: '/api/send-approval-email',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                story: pipeline.context || '',
                platform: item.platform || '',
                competitors: competitors,
                caption: caption,
                asset_type: item.type || '',
                image_url: item.type === 'image' ? item.content : null
            }),
            success: function (res) {
                if (res.success) {
                    showToast('Approval notification emailed.', 'info');
                } else {
                    console.warn('[Approval Email] Not sent:', res.error);
                }
            },
            error: function (xhr) {
                console.warn('[Approval Email] Request failed:', xhr.responseJSON?.error || xhr.statusText);
            }
        });
    }

    window.approveCarouselItem = function (index) {
        const item = window.currentCarouselAssets[index];
        window.lastGeneratedPipeline = item;

        // Hide carousel controls, just show the approved item
        $('#generationCarousel .carousel-indicators, #generationCarousel .carousel-control-prev, #generationCarousel .carousel-control-next, #generationCarousel .btn-success, #generationCarousel .btn-outline-danger').addClass('d-none');
        $('#approvalButtons').removeClass('d-none');
    };

    window.startPipelineGeneration = function () {
        if (!window.lastStrategyData) {
            showToast('Please generate a synthesis strategy first.', 'warning');
            return;
        }

        const mediaType = $('input[name="mediaType"]:checked').val();
        let prompt = $('#pipelinePrompt').val();
        const platform = getSelectedPlatform('#pipelineTargetPlatform');

        slideWorkflow(3); // Slide to Asset Review (Slide 4)

        $('#pipelineLoader').removeClass('d-none');
        $('#pipelineResultBlock').removeClass('d-none');
        $('#pipelineOutputContent').html('<div class="text-center py-4"><div class="spinner-border text-primary mb-2"></div><p class="text-muted small m-0">Generating assets...</p></div>');
        $('#approvalButtons').addClass('d-none');
        $('#publishPipelineBtn').addClass('d-none');

        window.currentCarouselAssets = [];

        if (window.activePipeline) {
            window.activePipeline.assetType = mediaType;
            window.activePipeline.status = 'asset_generating';
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            renderPipelineHistory();
        }

        const combinedStory = `STRATEGY SYNTHESIS:\n${JSON.stringify(window.lastStrategyData, null, 2)}\n\nUSER INSTRUCTIONS / CHARACTERS / HOOK:\n${prompt}`;

        // 1. Generate text captions
        $.ajax({
            url: '/api/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                story: combinedStory,
                platforms: [platform],
                selected_outputs: ['text'],
                include_strategy: false
            }),
            success: function (res) {
                if (res.success && res.content && res.content[platform]) {
                    const captions = res.content[platform].caption;

                    if (mediaType === 'Text (Caption)') {
                        $('#startPipelineBtn').prop('disabled', false);
                        $('#pipelineLoader').addClass('d-none');

                        // Push ONLY 1 text variation (the best, most refined one)
                        window.currentCarouselAssets.push({ type: 'Text (Caption)', content: captions.primary_caption, title: 'Refined Narrative', platform: platform, prompt: prompt });

                        renderCarousel();

                        // Hide carousel controls if only 1 item
                        if (window.currentCarouselAssets.length === 1) {
                            $('#generationCarousel .carousel-indicators, #generationCarousel .carousel-control-prev, #generationCarousel .carousel-control-next').addClass('d-none');
                        }

                        if (window.activePipeline) {
                            window.activePipeline.status = 'asset_generated';
                            window.activePipeline.assetContent = window.currentCarouselAssets;
                            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                            renderPipelineHistory();
                        }
                    } else {
                        // Generate Media (3 variations)
                        let generatedCount = 0;
                        const totalToGenerate = 3;
                        $('#pipelineOutputContent').html('<div class="text-center py-4"><div class="spinner-border text-purple mb-2"></div><p class="text-muted small m-0">Rendering media variation 1 of 3...</p></div>');

                        function generateNextMedia() {
                            if (generatedCount >= totalToGenerate) {
                                $('#startPipelineBtn').prop('disabled', false);
                                $('#pipelineLoader').addClass('d-none');

                                if (window.activePipeline) {
                                    window.activePipeline.status = 'asset_generated';
                                    window.activePipeline.assetContent = window.currentCarouselAssets;
                                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                                    renderPipelineHistory();
                                }
                                return;
                            }

                            $.ajax({
                                url: '/api/generate-media',
                                type: 'POST',
                                contentType: 'application/json',
                                data: JSON.stringify({
                                    platform: platform,
                                    caption: captions.primary_caption,
                                    media_type: mediaType,
                                    tone: generatedCount,
                                    context: $('#preGenImageContext').val(),
                                    image_path: window.getCharacterAssetPath(window.activePipeline)
                                }),
                                success: function (mediaRes) {
                                    if (mediaRes.success && mediaRes.url) {
                                        window.currentCarouselAssets.push({
                                            type: mediaType,
                                            content: mediaRes.url,
                                            caption: captions.primary_caption,
                                            platform: platform,
                                            prompt: prompt,
                                            context: $('#preGenImageContext').val()
                                        });
                                        renderCarousel();

                                        generatedCount++;
                                        if (generatedCount < totalToGenerate) {
                                            $('#pipelineLoader').text(`Rendering media variation ${generatedCount + 1} of 3...`);
                                            generateNextMedia();
                                        } else {
                                            $('#startPipelineBtn').prop('disabled', false);
                                            $('#pipelineLoader').addClass('d-none');
                                        }
                                    } else {
                                        showPipelineError('Media generation failed on variation ' + (generatedCount + 1));
                                    }
                                },
                                error: function () {
                                    showPipelineError('Media API network error on variation ' + (generatedCount + 1));
                                }
                            });
                        }

                        generateNextMedia();
                    }
                } else {
                    showPipelineError('Caption generation failed.');
                }
            },
            error: function (err) {
                $('#startPipelineBtn').prop('disabled', false);
                $('#pipelineLoader').addClass('d-none');
                $('#pipelineOutputContent').html(`<div class="text-danger fw-bold p-3">Error generating assets.</div>`);
                showToast('Asset generation failed', 'danger');
                if (window.activePipeline) {
                    window.activePipeline.status = 'stopped_error';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();
                }
            }
        });
    };

    function showPipelineError(msg) {
        $('#startPipelineBtn').prop('disabled', false);
        $('#pipelineLoader').addClass('d-none');
        $('#pipelineOutputContent').html(`<div class="text-danger fw-bold"><i class="fas fa-exclamation-triangle me-2"></i>${msg}</div>`);
    }

    window.lastGeneratedPipeline = null;

    window.approvePipelineContent = function () {
        if (!window.lastGeneratedPipeline) {
            showToast('No asset to approve.', 'error');
            return;
        }

        const btn = $('#approvalButtons button.btn-success');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-2"></i>Approving...');

        $.ajax({
            url: '/api/approve-asset',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platform: window.lastGeneratedPipeline.platform,
                type: window.lastGeneratedPipeline.type,
                content: window.lastGeneratedPipeline.content || window.lastGeneratedPipeline.url
            }),
            success: function (res) {
                $('#approvalButtons').addClass('d-none');
                $('#publishPipelineBtn').removeClass('d-none');
                showToast('Asset Approved and saved to database!', 'success');

                if (window.activePipeline) {
                    window.activePipeline.status = 'approved';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();
                    sendApprovalEmail(window.activePipeline, window.lastGeneratedPipeline);
                }
            },
            error: function (err) {
                console.error(err);
                btn.prop('disabled', false).html(origText);
                showToast('Error saving asset to database.', 'error');

                if (window.activePipeline) {
                    window.activePipeline.status = 'stopped_error';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();
                }
            }
        });
    };

    window.rejectPipelineContent = function () {
        $('#pipelineOutputContent').html('<div class="text-muted p-4 text-center"><em>Content Rejected. Please refine your prompt and regenerate.</em></div>');
        $('#approvalButtons').addClass('d-none');
        showToast('Asset Rejected', 'warning');

        if (window.activePipeline) {
            window.activePipeline.status = 'rejected';
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            renderPipelineHistory();
        }
    };

    // Publishes one generated asset {platform, type, content, caption} to the
    // user's connected social accounts via SocialPublisherService (real, not simulated).
    function publishAssetToBackend(item) {
        return $.ajax({
            url: '/api/publish-pipeline-asset',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platform: item.platform,
                type: item.type,
                content: item.content,
                caption: item.caption
            })
        });
    }

    window.publishPipelineContent = function () {
        if (!window.lastGeneratedPipeline) {
            showToast('No approved asset to publish.', 'warning');
            return;
        }
        if (!window.lastGeneratedPipeline.platform) {
            window.lastGeneratedPipeline.platform = getSelectedPlatform();
        }

        const btn = $('#publishPipelineBtn');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-2"></i>Publishing...');

        publishAssetToBackend(window.lastGeneratedPipeline)
            .done(function (res) {
                const result = res.result || {};
                if (res.success) {
                    btn.html('<i class="fas fa-check-circle me-2"></i>Published Successfully');
                    btn.removeClass('btn-dark').addClass('btn-success');
                    showToast(result.message || `Asset published to ${window.lastGeneratedPipeline.platform}!`, 'success');

                    if (window.activePipeline) {
                        window.activePipeline.status = 'published';
                        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                        renderPipelineHistory();
                    }

                    setTimeout(() => {
                        btn.html(origText);
                        btn.prop('disabled', false);
                        btn.removeClass('btn-success').addClass('btn-dark');
                    }, 3000);
                } else {
                    btn.prop('disabled', false).html(origText);
                    showToast(result.error || res.error || 'Publish failed.', 'danger');
                }
            })
            .fail(function (xhr) {
                btn.prop('disabled', false).html(origText);
                showToast(xhr.responseJSON?.error || 'Network error while publishing.', 'danger');
            });
    };

    window.saveAndRerunPipelineStrategy = function (pipelineId) {
        const newContext = $('#editPipelineContextArea').val();
        if (!newContext) return;

        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (pipeline) {
            pipeline.context = newContext;
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        }
        rerunPipelineStrategy(pipelineId);
    };

    window.rerunPipelineStrategy = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        const btn = $('#rerunStrategyBtn');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Regenerating...');

        $.ajax({
            url: '/api/generate-channel-storyline',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                story: pipeline.context
            }),
            success: function (r) {
                if (r.success && r.storyline) {
                    pipeline.strategy = r.storyline;
                    pipeline.status = 'strategy_generated';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();

                    showToast('Counter-strategy regenerated successfully!', 'success');

                    // Refresh modal view directly to the strategy step
                    renderPipelineModalStepper(window.pipelineHistory.indexOf(pipeline), 'strategy_generated');
                    showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'strategy_generated');
                } else {
                    btn.prop('disabled', false).html(origText);
                    showToast('Failed to regenerate strategy.', 'error');
                }
            },
            error: function () {
                btn.prop('disabled', false).html(origText);
                showToast('Network error while regenerating strategy.', 'error');
            }
        });
    };

    window.rejectPipelineStrategy = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        pipeline.status = 'rejected';
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();

        showToast('Strategy Rejected.', 'warning');

        const modalEl = document.getElementById('pipelineStageModal');
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.hide();
    };

    window.approvePipelineStrategy = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        // If this pipeline is the one already open in the live workspace
        // (window.lastStrategyData already matches it), jump back there
        // instead of duplicating the whole Generator UI inline - continuing
        // a pipeline you're actively working on should feel like one
        // experience, not two disconnected ones.
        if (window.activePipeline && window.activePipeline.id === pipeline.id) {
            const modalEl = document.getElementById('pipelineStageModal');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
            slideWorkflow(2);
            return;
        }

        window.activePipeline = pipeline;

        // Render the Generator UI inside the modal
        const generatorHtml = `
            <h6 class="fw-bold text-primary mb-3"><i class="fas fa-magic me-2"></i>Content Generator</h6>
            <div id="modalGenerationPipelineBlock" class="d-flex flex-column gap-3 p-4 border rounded-3 bg-light mt-2">
                <div class="d-flex flex-column gap-2">
                    <label class="fw-bold m-0" style="font-size: 0.9rem;"><i class="fas fa-share-nodes me-2 text-primary"></i> Target Platform:</label>
                    <select class="form-select form-select-sm" id="modalPipelineTargetPlatform"
                        title="Each platform generates images/video at its own correct size (e.g. LinkedIn landscape vs Instagram square).">
                        <option value="linkedin" selected>LinkedIn</option>
                        <option value="facebook">Facebook</option>
                        <option value="instagram">Instagram</option>
                    </select>
                </div>
                <div class="d-flex flex-column gap-2">
                    <label class="fw-bold m-0" style="font-size: 0.9rem;"><i class="fas fa-photo-video me-2 text-primary"></i> Select Output Type:</label>
                    <div class="btn-group w-100" role="group" id="modalMediaTypeGroup">
                        <input type="radio" class="btn-check" name="modalMediaType" id="modalTypeText" value="Text (Caption)" autocomplete="off" checked>
                        <label class="btn btn-outline-primary btn-sm fw-bold" for="modalTypeText"><i class="fas fa-align-left me-2"></i>Caption</label>
                        <input type="radio" class="btn-check" name="modalMediaType" id="modalTypeImage" value="image" autocomplete="off">
                        <label class="btn btn-outline-primary btn-sm fw-bold" for="modalTypeImage"><i class="fas fa-image me-2"></i>Image</label>
                        <input type="radio" class="btn-check" name="modalMediaType" id="modalTypeVideo" value="video" autocomplete="off">
                        <label class="btn btn-outline-primary btn-sm fw-bold" for="modalTypeVideo"><i class="fas fa-video me-2"></i>Video</label>
                    </div>
                </div>
                <div class="position-relative mt-2">
                    <i class="fas fa-paperclip position-absolute" style="top: 15px; left: 15px; color: #9ca3af; cursor: pointer; transition: color 0.2s;" onmouseover="this.style.color='#0d6efd'" onmouseout="this.style.color='#9ca3af'" onclick="handlePromptAttachment('modalPipelinePrompt')" title="Attach text file"></i>
                    <textarea id="modalPipelinePrompt" class="form-control" rows="3" style="padding-left: 2.5rem; border-radius: 12px; resize: none;" placeholder="Attach an optional creative prompt (e.g. 'Use an energetic tone', 'Include branding colors')"></textarea>
                </div>
                <div id="modalImageContextContainer" class="mt-2 d-none">
                    <label class="form-label small text-muted fw-bold mb-1"><i class="fas fa-paint-brush me-1"></i>Image Context/Style</label>
                    <select class="form-select form-select-sm" id="modalPreGenImageContext">
                        <option value="Professional">Professional</option>
                        <option value="Casual">Casual</option>
                        <option value="Cinematic">Cinematic</option>
                        <option value="Abstract">Abstract</option>
                    </select>
                </div>
                <button class="btn btn-primary action-btn w-100 shadow-sm mt-3" onclick="startModalPipelineGeneration(${pipeline.id})" id="startModalPipelineBtn">
                    <i class="fas fa-magic me-2"></i>Generate Assets
                </button>
            </div>
            <div id="modalPipelineLoader" class="d-none mt-3 text-center text-primary fw-bold small">
                <i class="fas fa-spinner fa-spin me-2"></i>Generating assets...
            </div>
            <div id="modalPipelineOutputContent" class="mt-3"></div>
        `;

        $('#pipelineModalDetail').html(generatorHtml);
    };

    window.startModalPipelineGeneration = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        const mediaType = $('input[name="modalMediaType"]:checked').val();
        const prompt = $('#modalPipelinePrompt').val();
        const platform = getSelectedPlatform('#modalPipelineTargetPlatform');

        $('#startModalPipelineBtn').prop('disabled', true);
        $('#modalPipelineLoader').removeClass('d-none');
        $('#modalPipelineOutputContent').html('');

        const combinedStory = `STRATEGY SYNTHESIS:\n${JSON.stringify(pipeline.strategy, null, 2)}\n\nUSER INSTRUCTIONS / CHARACTERS / HOOK:\n${prompt}`;

        $.ajax({
            url: '/api/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                story: combinedStory,
                platforms: [platform],
                selected_outputs: ['text'],
                include_strategy: false
            }),
            success: function (res) {
                if (res.success && res.content && res.content[platform]) {
                    const captions = res.content[platform].caption;
                    window.currentCarouselAssets = [];

                    if (mediaType === 'Text (Caption)') {
                        $('#startModalPipelineBtn').prop('disabled', false);
                        $('#modalPipelineLoader').addClass('d-none');

                        pipeline.status = 'asset_generated';
                        pipeline.assetType = mediaType;
                        pipeline.assetContent = [
                            { type: 'Text (Caption)', content: captions.primary_caption }
                        ];
                        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                        renderPipelineHistory();

                        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'asset_generated');
                    } else {
                        // For image/video, just simulate or trigger generation like in main workflow
                        $('#modalPipelineLoader').html('<i class="fas fa-spinner fa-spin me-2"></i>Rendering media variation 1 of 3...');
                        let generatedCount = 0;
                        const totalToGenerate = 3;

                        function generateNextMedia() {
                            if (generatedCount >= totalToGenerate) {
                                $('#startModalPipelineBtn').prop('disabled', false);
                                $('#modalPipelineLoader').addClass('d-none');

                                pipeline.status = 'asset_generated';
                                pipeline.assetType = mediaType;
                                pipeline.assetContent = window.currentCarouselAssets;
                                localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                                renderPipelineHistory();
                                showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'asset_generated');
                                return;
                            }

                            $.ajax({
                                url: '/api/generate-media',
                                type: 'POST',
                                contentType: 'application/json',
                                data: JSON.stringify({
                                    platform: platform,
                                    caption: captions.primary_caption,
                                    media_type: mediaType,
                                    tone: generatedCount,
                                    context: $('#modalPreGenImageContext').val(),
                                    image_path: window.getCharacterAssetPath(pipeline)
                                }),
                                success: function (mediaRes) {
                                    if (mediaRes.success && mediaRes.url) {
                                        window.currentCarouselAssets.push({
                                            type: mediaType,
                                            content: mediaRes.url,
                                            caption: captions.primary_caption,
                                            platform: platform,
                                            prompt: prompt,
                                            context: $('#modalPreGenImageContext').val()
                                        });
                                        generatedCount++;
                                        if (generatedCount < totalToGenerate) {
                                            $('#modalPipelineLoader').html(`<i class="fas fa-spinner fa-spin me-2"></i>Rendering media variation ${generatedCount + 1} of 3...`);
                                            generateNextMedia();
                                        } else {
                                            $('#startModalPipelineBtn').prop('disabled', false);
                                            $('#modalPipelineLoader').addClass('d-none');

                                            pipeline.status = 'asset_generated';
                                            pipeline.assetType = mediaType;
                                            pipeline.assetContent = window.currentCarouselAssets;
                                            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                                            renderPipelineHistory();
                                            showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'asset_generated');
                                        }
                                    } else {
                                        showToast('Media API failed on variation ' + (generatedCount + 1), 'error');
                                        $('#startModalPipelineBtn').prop('disabled', false);
                                        $('#modalPipelineLoader').addClass('d-none');
                                    }
                                },
                                error: function () {
                                    showToast('Network error on variation ' + (generatedCount + 1), 'error');
                                    $('#startModalPipelineBtn').prop('disabled', false);
                                    $('#modalPipelineLoader').addClass('d-none');
                                }
                            });
                        }

                        generateNextMedia();
                    }
                } else {
                    $('#startModalPipelineBtn').prop('disabled', false);
                    $('#modalPipelineLoader').addClass('d-none');
                    showToast('Caption generation failed.', 'error');
                }
            },
            error: function () {
                $('#startModalPipelineBtn').prop('disabled', false);
                $('#modalPipelineLoader').addClass('d-none');
                showToast('Error generating assets.', 'error');
            }
        });
    };

    window.rejectPipelineAsset = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        pipeline.status = 'rejected';
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();

        showToast('Asset Rejected.', 'warning');
        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'asset_generated');
    };

    window.approvePipelineAsset = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        pipeline.status = 'approved';
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();

        showToast('Asset Approved! Ready for publishing.', 'success');

        renderPipelineModalStepper(window.pipelineHistory.indexOf(pipeline), 'approved');
        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'approved');

        const assets = Array.isArray(pipeline.assetContent) ? pipeline.assetContent : [pipeline.assetContent];
        sendApprovalEmail(pipeline, assets[assets.length - 1]);
    };

    window.publishModalPipelineContent = function (pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline || !pipeline.assetContent) return;

        const assets = Array.isArray(pipeline.assetContent) ? pipeline.assetContent : [pipeline.assetContent];
        const item = assets[assets.length - 1];
        if (!item.platform) item.platform = getSelectedPlatform();

        const btn = $('#modalPublishBtn');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-2"></i>Publishing...');

        publishAssetToBackend(item)
            .done(function (res) {
                const result = res.result || {};
                if (res.success) {
                    btn.html('<i class="fas fa-check-circle me-2"></i>Published Successfully');
                    btn.removeClass('btn-dark').addClass('btn-success');
                    showToast(result.message || 'Asset published to platform!', 'success');

                    pipeline.status = 'published';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();

                    setTimeout(() => {
                        renderPipelineModalStepper(window.pipelineHistory.indexOf(pipeline), 'published');
                        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'published');
                    }, 1500);
                } else {
                    btn.prop('disabled', false).html(origText);
                    showToast(result.error || res.error || 'Publish failed.', 'danger');
                }
            })
            .fail(function (xhr) {
                btn.prop('disabled', false).html(origText);
                showToast(xhr.responseJSON?.error || 'Network error while publishing.', 'danger');
            });
    };
});

// Caption Action Utilities
window.handlePromptAttachment = function (textareaId) {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.txt,.md,.csv,.json';
    fileInput.onchange = function (e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function (evt) {
            const content = evt.target.result;
            const textarea = document.getElementById(textareaId);
            if (textarea) {
                const existing = textarea.value;
                textarea.value = existing + (existing ? '\n\n' : '') + `--- Attached File: ${file.name} ---\n` + content;
                showToast(`Attached ${file.name}`, 'success');
            }
        };
        reader.onerror = function () {
            showToast('Error reading file.', 'danger');
        };
        reader.readAsText(file);
    };
    fileInput.click();
};

window.copyCaptionText = function (btnElement) {
    const textToCopy = $(btnElement).closest('.position-relative').find('.caption-text-content').text();

    const copySuccess = () => {
        $(btnElement).removeClass('far fa-copy').addClass('fas fa-check text-success');
        setTimeout(() => {
            $(btnElement).removeClass('fas fa-check text-success').addClass('far fa-copy');
        }, 2000);
    };

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(textToCopy).then(copySuccess).catch(() => fallbackCopy(textToCopy, copySuccess));
    } else {
        fallbackCopy(textToCopy, copySuccess);
    }

    function fallbackCopy(text, successCb) {
        let textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            successCb();
        } catch (err) {
            console.error('Fallback copy failed', err);
        }
        document.body.removeChild(textArea);
    }
};

window.regenerateModalCaptionText = function (pipelineId, index, event) {
    const pipelineIndex = window.pipelineHistory.findIndex(p => p.id === pipelineId);
    if (pipelineIndex === -1) return;
    const pipeline = window.pipelineHistory[pipelineIndex];
    const item = pipeline.assetContent[index];
    const iconElement = $(event.currentTarget);

    if (iconElement.hasClass('fa-spin')) return;
    iconElement.addClass('fa-spin text-primary').removeClass('text-muted');

    const platform = item.platform || 'linkedin';
    const prompt = item.prompt || '';
    const combinedStory = `STRATEGY SYNTHESIS:\n${JSON.stringify(pipeline.strategy, null, 2)}\n\nUSER INSTRUCTIONS / CHARACTERS / HOOK:\n${prompt}`;

    $.ajax({
        url: '/api/generate',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            story: combinedStory,
            platforms: [platform],
            selected_outputs: ['text'],
            include_strategy: false
        }),
        success: function (res) {
            iconElement.removeClass('fa-spin text-primary').addClass('text-muted');
            if (res.success && res.content && res.content[platform]) {
                const captions = res.content[platform].caption;

                const itemToUpdate = pipeline.assetContent[index];
                if (!itemToUpdate.history) {
                    itemToUpdate.history = [itemToUpdate.content];
                    itemToUpdate.historyIndex = 0;
                }
                itemToUpdate.history.push(captions.primary_caption);
                itemToUpdate.historyIndex = itemToUpdate.history.length - 1;
                itemToUpdate.content = captions.primary_caption;

                localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                showPipelineStageDetail(pipelineIndex, pipeline.status);
            } else {
                showToast('Failed to regenerate caption.', 'danger');
            }
        },
        error: function () {
            iconElement.removeClass('fa-spin text-primary').addClass('text-muted');
            showToast('Error regenerating caption.', 'danger');
        }
    });
};

window.regenerateCaptionText = function (index, event) {
    const item = window.currentCarouselAssets[index];
    const iconElement = $(event.currentTarget);

    if (iconElement.hasClass('fa-spin')) return;
    iconElement.addClass('fa-spin text-primary').removeClass('text-muted');

    let combinedStory = "";
    let platform = item.platform || 'linkedin';

    if (window.activePipeline && window.activePipeline.strategy) {
        combinedStory = `STRATEGY SYNTHESIS:\n${JSON.stringify(window.activePipeline.strategy, null, 2)}\n\nUSER INSTRUCTIONS / CHARACTERS / HOOK:\n${item.prompt || ''}`;
    } else if (window.lastStrategyData) {
        combinedStory = `STRATEGY SYNTHESIS:\n${JSON.stringify(window.lastStrategyData, null, 2)}\n\nUSER INSTRUCTIONS / CHARACTERS / HOOK:\n${item.prompt || ''}`;
    }

    $.ajax({
        url: '/api/generate',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            story: combinedStory,
            platforms: [platform],
            selected_outputs: ['text'],
            include_strategy: false
        }),
        success: function (res) {
            iconElement.removeClass('fa-spin text-primary').addClass('text-muted');
            if (res.success && res.content && res.content[platform]) {
                const captions = res.content[platform].caption;

                const itemToUpdate = window.currentCarouselAssets[index];
                if (!itemToUpdate.history) {
                    itemToUpdate.history = [itemToUpdate.content];
                    itemToUpdate.historyIndex = 0;
                }
                itemToUpdate.history.push(captions.primary_caption);
                itemToUpdate.historyIndex = itemToUpdate.history.length - 1;
                itemToUpdate.content = captions.primary_caption;

                if (window.activePipeline) {
                    window.activePipeline.assetContent = window.currentCarouselAssets;
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                }

                renderCarousel();
            } else {
                showToast('Failed to regenerate caption.', 'danger');
            }
        },
        error: function () {
            iconElement.removeClass('fa-spin text-primary').addClass('text-muted');
            showToast('Error regenerating caption.', 'danger');
        }
    });
};

window.regenerateImageInCarousel = function (index) {
    const item = window.currentCarouselAssets[index];
    const context = item.context || 'Professional';

    const btn = $(`#regenImgBtn_${index}`);
    const originalHtml = btn.html();
    btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Wait...');

    $.ajax({
        url: '/api/generate-media',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            platform: item.platform || 'instagram',
            caption: item.prompt || item.caption,
            media_type: 'image',
            context: context,
            image_path: window.getCharacterAssetPath(window.activePipeline)
        }),
        success: function (mediaRes) {
            if (mediaRes.success && mediaRes.url) {
                if (!item.history) {
                    item.history = [item.content];
                    item.historyIndex = 0;
                }
                item.history.push(mediaRes.url);
                item.historyIndex = item.history.length - 1;
                item.content = mediaRes.url;

                // Keep the caption prompt updated if context is meant to override it
                if (window.activePipeline) {
                    window.activePipeline.assetContent = window.currentCarouselAssets;
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                }
                renderCarousel();
            } else {
                showToast('Image generation failed: ' + (mediaRes.error || 'Unknown error'), 'danger');
                btn.prop('disabled', false).html(originalHtml);
            }
        },
        error: function (err) {
            showToast('Error connecting to image generation service.', 'danger');
            btn.prop('disabled', false).html(originalHtml);
        }
    });
};

window.regenerateModalImage = function (pipelineId, index, event) {
    if (event) event.stopPropagation();
    const pipelineIndex = window.pipelineHistory.findIndex(p => p.id === pipelineId);
    if (pipelineIndex === -1) return;
    const pipeline = window.pipelineHistory[pipelineIndex];
    const item = pipeline.assetContent[index];

    const context = item.context || 'Professional';
    const btn = $(`#modalRegenImgBtn_${pipelineId}_${index}`);
    const originalHtml = btn.html();

    btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i>');

    $.ajax({
        url: '/api/generate-media',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            platform: item.platform || 'instagram',
            caption: item.prompt || item.caption,
            media_type: 'image',
            context: context,
            image_path: window.getCharacterAssetPath(pipeline)
        }),
        success: function (mediaRes) {
            if (mediaRes.success && mediaRes.url) {
                if (!item.history) {
                    item.history = [item.content];
                    item.historyIndex = 0;
                }
                item.history.push(mediaRes.url);
                item.historyIndex = item.history.length - 1;
                item.content = mediaRes.url;

                localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                showPipelineStageDetail(pipelineIndex, window._currentStageId || pipeline.status);
            } else {
                showToast('Image generation failed: ' + (mediaRes.error || 'Unknown error'), 'danger');
                btn.prop('disabled', false).html(originalHtml);
            }
        },
        error: function (err) {
            showToast('Error connecting to image generation service.', 'danger');
            btn.prop('disabled', false).html(originalHtml);
        }
    });
};
window.downloadAllAsZip = function(urls) {
    if (!urls || urls.length === 0) {
        alert("No images to download!");
        return;
    }
    
    // Show a loading toast or change button state if desired
    
    fetch('/api/download-zip', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ urls: urls })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        return response.blob();
    })
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'generated_assets.zip';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    })
    .catch(error => {
        console.error('Error downloading zip:', error);
        alert('Failed to download ZIP file. Please try again later.');
    });
};

    // ── KPI Stat Counters Updater ─────────────────────────────────────────
    function updateKpiMetricsFromHistoryAndDB() {
        const historyCount = Array.isArray(window.pipelineHistory) ? window.pipelineHistory.length : 0;
        $('#statPipelinesCount').text(`${historyCount} Runs`);

        $.ajax({
            url: '/api/opportunity-suggestions',
            type: 'GET',
            success: function (r) {
                if (r.success && Array.isArray(r.suggestions)) {
                    $('#statOpportunitiesCount').text(`${r.suggestions.length} Found`);
                }
            }
        });
    }

    setTimeout(updateKpiMetricsFromHistoryAndDB, 800);
