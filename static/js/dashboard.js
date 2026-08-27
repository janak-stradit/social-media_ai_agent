$(document).ready(function() {

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

    // Toast notification helper
    function showToast(message, type = 'info') {
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
            $toast.fadeOut(300, function() { $(this).remove(); });
        }, 4000);
    }

    window.fetchPlatformPosts = function() {
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
            success: function(r) {
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
                            <p class="fw-semibold">No ${platform} posts found for any competitors.</p>
                        </div>
                    `);
                }
            },
            error: function(xhr) {
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

    // Loads previously-scraped posts already saved in the DB (no external scan)
    window.loadStoredPosts = function() {
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
            success: function(r) {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none');

                if (r.success && r.posts && r.posts.length > 0) {
                    renderPlatformPosts(r.posts);
                } else {
                    $('#postsContainer').html(`
                        <div class="col-12 empty-state">
                            <i class="fas fa-database"></i>
                            <h4 class="fw-bold text-dark mb-2">No Saved Data Yet</h4>
                            <p class="fw-medium">Click "Start Scan" to pull the latest ${platform} posts and save them here.</p>
                        </div>
                    `);
                }
            },
            error: function() {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none');
            }
        });
    };

    window.newlyInsertedPostUrls = new Set();

    // Show whatever is already saved as soon as the dashboard loads / filters change
    loadStoredPosts();
    $('#dashboardPlatformSelect, #dashboardCompetitorSelect').on('change', function() {
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

        let html = '<div class="row g-4">';
        let cIdx = 0;

        sortedPosts.forEach(p => {
            const comp = p._source_competitor || 'Unknown Competitor';
            const title = getPostTitle(p);
            const textSnippet = p.text ? p.text.substring(0, 300) + (p.text.length > 300 ? '...' : '') : 'No text content available.';
            const platformIcon = getPlatformIcon(p.platform);
            const postDate = formatPostDate(p);
            const isNew = window.newlyInsertedPostUrls && p.post_url && window.newlyInsertedPostUrls.has(p.post_url);

            // Full payload for generation
            const encodedPayload = encodeURIComponent(`[${comp} - ${title}]\n${p.text || title}\n\n---\n\n`);

            html += `
            <div class="col-md-6">
                <div class="premium-card h-100 d-flex flex-column competitor-post-card position-relative p-4" style="background: white; border: 1px solid #e2e8f0; border-radius: 16px;">
                    <div class="d-flex justify-content-between align-items-start mb-3">
                        <div class="d-flex align-items-center gap-2">
                            <div class="rounded-circle d-flex align-items-center justify-content-center shadow-sm" style="width: 36px; height: 36px; background: rgba(79, 70, 229, 0.1); color: var(--primary);">
                                <i class="fas fa-building-columns"></i>
                            </div>
                            <div>
                                <h6 class="mb-0 fw-bold text-dark d-flex align-items-center gap-2">
                                    ${comp}
                                    ${isNew ? '<span class="badge rounded-pill bg-success" style="font-size: 0.62rem; font-weight: 700; padding: 0.25rem 0.55rem;"><i class="fas fa-sparkles me-1"></i>New</span>' : ''}
                                </h6>
                                <small class="text-muted fw-medium">${postDate || 'Recent Post'}</small>
                            </div>
                        </div>
                        <div class="form-check m-0" style="transform: scale(1.3);">
                            <input class="form-check-input comp-master-checkbox cursor-pointer shadow-sm border-primary" type="checkbox" value="${cIdx}" id="masterCheck${cIdx}" data-payload="${encodedPayload}" data-competitor="${comp}">
                        </div>
                    </div>

                    <label class="flex-grow-1 cursor-pointer" for="masterCheck${cIdx}">
                        <h6 class="fw-bold text-gray-800 mb-2 lh-base d-flex align-items-start gap-2" style="font-size: 1.05rem;">
                            <span class="mt-1">${platformIcon}</span> <span>${title}</span>
                        </h6>
                        <p class="text-muted small mb-0 lh-sm" style="line-height: 1.5 !important;">${textSnippet}</p>
                    </label>
                </div>
            </div>`;
            cIdx++;
        });

        html += '</div>';
        
        $('#postsContainer').html(html);
        
        $('.comp-master-checkbox').on('change', function() {
            updateSelection();
        });
    }

    function openSynthesisPanel() {
        $('#rightSynthesisPanel').removeClass('d-none');
        $('#centerFeedPanel').removeClass('col-xl-9 col-lg-9').addClass('col-xl-6 col-lg-6');
    }

    function closeSynthesisPanel() {
        $('#rightSynthesisPanel').addClass('d-none');
        $('#centerFeedPanel').removeClass('col-xl-6 col-lg-6').addClass('col-xl-9 col-lg-9');
    }

    function updateSelection() {
        const checked = $('.comp-master-checkbox:checked');
        $('#selectedPostCount').text(checked.length);

        if (checked.length === 0) {
            $('#storyContextInput').val('');
            $('#generateStoryBtn').prop('disabled', true);
            closeSynthesisPanel();
            return;
        }

        // Only open the Strategic Synthesis panel once a post is selected
        openSynthesisPanel();

        $('#generateStoryBtn').prop('disabled', false);

        let combinedText = "--- SELECTED COMPETITOR POSTS ---\n\n";
        checked.each(function() {
            const decoded = decodeURIComponent($(this).attr('data-payload'));
            combinedText += decoded;
        });
        
        $('#storyContextInput').val(combinedText);
    }

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

    window.generateStoryFromSelection = function() {
        const context = $('#storyContextInput').val();
        if (!context) return;

        // Initialize active pipeline
        const checkedLabels = $('.comp-master-checkbox:checked').map(function() {
            return $(this).data('competitor');
        }).get().join(', ');
        
        window.activePipeline = {
            id: Date.now(),
            timestamp: new Date().toISOString(),
            competitors: checkedLabels,
            context: context,
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
                story: context
            }),
            success: function(r) {
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
                        <div class="mb-3 text-muted" style="line-height: 1.6; font-size: 0.95rem;">
                            <strong class="text-dark">Storyline / Prompt:</strong> <br>
                            ${(data.prompt || '').replace(/\n/g, '<br>')}
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
            error: function(xhr) {
                $('#generateStoryBtn').prop('disabled', false);
                $('#generationLoader').addClass('d-none');
                $('#structuredOutput').html('<div class="text-danger fw-bold">Network Error.</div>');
                showToast('Network error.', 'danger');
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

    window.loadOpportunitySuggestions = function() {
        $.ajax({
            url: '/api/opportunity-suggestions',
            type: 'GET',
            success: function(r) {
                if (r.success && r.suggestions) {
                    window.opportunitySuggestions = r.suggestions;
                    renderOpportunityLists();
                }
            }
        });
    };

    window.openOpportunityModal = function() {
        renderOpportunityLists();
        const modalEl = document.getElementById('opportunityModal');
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    };

    window.generateOpportunitySuggestions = function() {
        const context = $('#storyContextInput').val();
        if (!context) {
            showToast('Select competitor posts from the feed first.', 'warning');
            return;
        }
        const accounts = $('.comp-master-checkbox:checked').map(function() {
            return $(this).data('competitor');
        }).get().join(', ');

        $('#findOpportunitiesBtn').prop('disabled', true);
        $('#opportunityGenLoader').removeClass('d-none');

        $.ajax({
            url: '/api/generate-opportunity-suggestions',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: context, accounts: accounts }),
            success: function(r) {
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
            error: function() {
                $('#findOpportunitiesBtn').prop('disabled', false);
                $('#opportunityGenLoader').addClass('d-none');
                showToast('Network error generating opportunities.', 'danger');
            }
        });
    };

    loadOpportunitySuggestions();

    window.copyStoryOutput = function() {
        const text = $('#storyOutput').val();
        if (!text) return;
        
        navigator.clipboard.writeText(text).then(() => {
            showToast('Copied to clipboard!', 'success');
        });
    }

    window.slideWorkflow = function(stepIndex) {
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

    window.approveStrategy = function() {
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

    function renderPipelineHistory() {
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
                    <p class="mb-1 text-muted small"><strong>Competitors:</strong> ${competitorsList}</p>
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
    
    window.viewHistoryItem = function(index) {
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

    function renderAssetItems(assetContent) {
        const items = Array.isArray(assetContent) ? assetContent : [assetContent];
        return items.map(a => {
            const type = (a.type || '').toLowerCase();
            if (type.includes('video')) {
                return `<video controls class="w-100 rounded-3 mb-2" src="${a.content}"></video>`;
            }
            if (type.includes('image')) {
                return `<img src="${a.content}" class="w-100 rounded-3 mb-2" alt="Generated asset">`;
            }
            return `<div class="bg-light rounded-3 p-3 small mb-2" style="white-space: pre-wrap;">${a.content}</div>`;
        }).join('');
    }

    function getStageDetailHtml(pipeline, stageId) {
        if (stageId === 'intel_selected') {
            const competitorsList = pipeline.competitors
                ? [...new Set(pipeline.competitors.split(',').map(c => c.trim()).filter(Boolean))].join(', ')
                : 'None';
            const safeContext = (pipeline.context || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            return `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-layer-group me-2"></i>Post Pipeline</h6>
                <p class="small text-muted mb-2"><strong class="text-dark">Competitors:</strong> ${competitorsList}</p>
                
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
        }
        if (stageId === 'strategy_generated') {
            if (!pipeline.strategy) return emptyStageState('Counter strategy has not been generated yet.');
            const facts = (pipeline.strategy.observed_facts || [])
                .map(f => `<span class="badge rounded-pill bg-white text-primary border border-primary px-3 py-2 me-2 mb-2 text-wrap text-start" style="font-size: 0.8rem; font-weight: 600; line-height: 1.4;">${f}</span>`)
                .join('');
            
            return `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-brain me-2"></i>Counter Strategy Generated</h6>
                <div class="mb-3 d-flex flex-wrap">${facts || '<span class="text-muted small">No observed facts recorded.</span>'}</div>
                <div class="small text-muted bg-light rounded-3 p-3 mb-3" style="white-space: pre-wrap; max-height: 400px; overflow-y: auto;">${pipeline.strategy.prompt || ''}</div>
                <div class="d-flex gap-2">
                    <button class="btn btn-sm btn-outline-danger fw-bold" onclick="rejectPipelineStrategy(${pipeline.id})"><i class="fas fa-times me-1"></i>Reject</button>
                    <button class="btn btn-sm btn-success fw-bold" onclick="approvePipelineStrategy(${pipeline.id})"><i class="fas fa-check me-1"></i>Approve & Continue</button>
                </div>
            `;
        }
        if (stageId === 'asset_generated') {
            if (!pipeline.assetContent) return emptyStageState('Content has not been generated yet.');
            return `
                <h6 class="fw-bold text-primary mb-3"><i class="fas fa-magic me-2"></i>Content Generated ${pipeline.assetType ? `<span class="badge bg-light text-dark border ms-1">${pipeline.assetType}</span>` : ''}</h6>
                <div style="max-height: 55vh; overflow-y: auto;" class="mb-3">${renderAssetItems(pipeline.assetContent)}</div>
                <div class="d-flex gap-2">
                    <button class="btn btn-sm btn-outline-danger fw-bold flex-grow-1" onclick="rejectPipelineAsset(${pipeline.id})"><i class="fas fa-times me-1"></i>Reject Asset</button>
                    <button class="btn btn-sm btn-success fw-bold flex-grow-1" onclick="approvePipelineAsset(${pipeline.id})"><i class="fas fa-check me-1"></i>Approve Asset</button>
                </div>
            `;
        }
        if (stageId === 'approved') {
            if (pipeline.status !== 'approved' && pipeline.status !== 'published') return emptyStageState('This asset has not been approved yet.');
            return `
                <h6 class="fw-bold text-success mb-3"><i class="fas fa-thumbs-up me-2"></i>Asset Approved</h6>
                <p class="small text-muted">This asset was reviewed and approved for publishing.</p>
                ${pipeline.assetContent ? `<div style="max-height: 320px; overflow-y: auto;" class="mb-3">${renderAssetItems(pipeline.assetContent)}</div>` : ''}
                ${pipeline.status === 'approved' ? `
                    <button class="btn btn-dark fw-bold w-100 py-2 mt-2" onclick="publishModalPipelineContent(${pipeline.id})" id="modalPublishBtn">
                        <i class="fas fa-paper-plane me-2"></i>Publish to Platforms
                    </button>
                ` : ''}
            `;
        }
        if (stageId === 'published') {
            if (pipeline.status !== 'published') return emptyStageState('This pipeline has not been published yet.');
            return `
                <h6 class="fw-bold text-dark mb-3"><i class="fas fa-paper-plane me-2"></i>Published</h6>
                <p class="small text-muted">This content has been published live.</p>
            `;
        }
        return emptyStageState('No details available for this stage.');
    }

    window.openPipelineModal = function(index) {
        const pipeline = window.pipelineHistory[index];
        if (!pipeline) return;

        // Default the detail view to the furthest reached stage
        const pipelineStatus = pipeline.status || 'unknown';
        const reachedIdx = Math.max(0, PIPELINE_STAGES.findIndex(s => s.id === pipelineStatus));

        $('#pipelineModalTitle').text('Pipeline ID: ' + pipeline.id);
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
                badgeClass = 'bg-light border text-muted';
            }
            if (step.id === activeStageId) {
                textClass += ' text-primary';
            }

            stepperHtml += `
                <div class="d-flex align-items-stretch pipeline-stage-row" style="cursor: pointer;" onclick="showPipelineStageDetail(${index}, '${step.id}')">
                    <div class="d-flex flex-column align-items-center" style="width: 28px;">
                        <div class="rounded-circle ${badgeClass} d-flex align-items-center justify-content-center shadow-sm flex-shrink-0" style="width: 24px; height: 24px;">
                            <i class="fas ${stepIcon} text-white" style="font-size: 10px;"></i>
                        </div>
                        ${!isLast ? '<div class="flex-grow-1" style="width: 2px; background: #e5e7eb; min-height: 18px;"></div>' : ''}
                    </div>
                    <div class="${textClass} small ps-2 pb-3" style="font-size: 0.85rem;">${step.label}</div>
                </div>
            `;
        });
        stepperHtml += '</div>';
        $('#pipelineModalStepper').html(stepperHtml);
    }

    window.showPipelineStageDetail = function(index, stageId) {
        const pipeline = window.pipelineHistory[index];
        if (!pipeline) return;

        renderPipelineModalStepper(index, stageId);
        $('#pipelineModalDetail').html(getStageDetailHtml(pipeline, stageId));
    };

    renderPipelineHistory();

    window.currentCarouselAssets = [];
    
    function renderCarousel() {
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
                outHtml = `<div class="p-4"><p class="m-0" style="white-space: pre-wrap;">${item.content}</p></div>`;
            } else if (item.type === 'image') {
                outHtml = `<img src="${item.content}" class="d-block w-100 rounded" style="object-fit: cover; max-height: 400px;">
                           <div class="p-3 bg-light border-top"><p class="small text-muted m-0"><strong>Caption:</strong> ${item.caption}</p></div>`;
            } else if (item.type === 'video') {
                outHtml = `<video controls autoplay loop class="d-block w-100 rounded" style="max-height: 400px;"><source src="${item.content}" type="video/mp4"></video>
                           <div class="p-3 bg-light border-top"><p class="small text-muted m-0"><strong>Caption:</strong> ${item.caption}</p></div>`;
            }
            
            innerHtml += `
                <div class="carousel-item ${activeClass}">
                    ${outHtml}
                    <div class="d-flex gap-2 mt-3 mb-2 px-3 pb-2">
                        <button class="btn btn-outline-danger flex-grow-1 fw-bold" onclick="rejectPipelineContent()"><i class="fas fa-times me-1"></i>Reject</button>
                        <button class="btn btn-success flex-grow-1 fw-bold" onclick="approveCarouselItem(${index})"><i class="fas fa-check me-2"></i>Approve</button>
                    </div>
                </div>
            `;
        });
        
        const carouselHtml = `
            <div id="generationCarousel" class="carousel slide" data-bs-ride="false">
              <div class="carousel-indicators bg-dark rounded-pill py-1 mb-0" style="bottom: -15px;">
                ${indicators}
              </div>
              <div class="carousel-inner rounded border shadow-sm" style="background: #fff;">
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

    window.approveCarouselItem = function(index) {
        const item = window.currentCarouselAssets[index];
        window.lastGeneratedPipeline = item;
        
        // Hide carousel controls, just show the approved item
        $('#generationCarousel .carousel-indicators, #generationCarousel .carousel-control-prev, #generationCarousel .carousel-control-next, #generationCarousel .btn-success, #generationCarousel .btn-outline-danger').addClass('d-none');
        $('#approvalButtons').removeClass('d-none');
    };

    window.startPipelineGeneration = function() {
        if (!window.lastStrategyData) {
            showToast('Please generate a synthesis strategy first.', 'warning');
            return;
        }

        const mediaType = $('input[name="mediaType"]:checked').val();
        let prompt = $('#pipelinePrompt').val();
        const platform = $('#dashboardPlatformSelect').val() || 'linkedin';
        
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
            success: function(res) {
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
                                    tone: generatedCount // slight seed variation
                                }),
                                success: function(mediaRes) {
                                    if (mediaRes.success && mediaRes.url) {
                                        window.currentCarouselAssets.push({
                                            type: mediaType,
                                            content: mediaRes.url,
                                            caption: captions.primary_caption,
                                            platform: platform,
                                            prompt: prompt
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
                                        showPipelineError('Media generation failed on variation ' + (generatedCount+1));
                                    }
                                },
                                error: function() {
                                    showPipelineError('Media API network error on variation ' + (generatedCount+1));
                                }
                            });
                        }
                        
                        generateNextMedia();
                    }
                } else {
                    showPipelineError('Caption generation failed.');
                }
            },
            error: function(err) {
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

    window.approvePipelineContent = function() {
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
            success: function(res) {
                $('#approvalButtons').addClass('d-none');
                $('#publishPipelineBtn').removeClass('d-none');
                showToast('Asset Approved and saved to database!', 'success');
                
                if (window.activePipeline) {
                    window.activePipeline.status = 'approved';
                    localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
                    renderPipelineHistory();
                }
            },
            error: function(err) {
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

    window.rejectPipelineContent = function() {
        $('#pipelineOutputContent').html('<div class="text-muted p-4 text-center"><em>Content Rejected. Please refine your prompt and regenerate.</em></div>');
        $('#approvalButtons').addClass('d-none');
        showToast('Asset Rejected', 'warning');
        
        if (window.activePipeline) {
            window.activePipeline.status = 'rejected';
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            renderPipelineHistory();
        }
    };

    window.publishPipelineContent = function() {
        // Simulate publishing
        const btn = $('#publishPipelineBtn');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-2"></i>Publishing...');
        
        setTimeout(() => {
            btn.html('<i class="fas fa-check-circle me-2"></i>Published Successfully');
            btn.removeClass('btn-dark').addClass('btn-success');
            showToast('Asset published to ' + (window.lastGeneratedPipeline ? window.lastGeneratedPipeline.platform : 'platform') + '!', 'success');
            
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
        }, 1500);
    };

    window.saveAndRerunPipelineStrategy = function(pipelineId) {
        const newContext = $('#editPipelineContextArea').val();
        if (!newContext) return;
        
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (pipeline) {
            pipeline.context = newContext;
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        }
        rerunPipelineStrategy(pipelineId);
    };

    window.rerunPipelineStrategy = function(pipelineId) {
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
            success: function(r) {
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
            error: function() {
                btn.prop('disabled', false).html(origText);
                showToast('Network error while regenerating strategy.', 'error');
            }
        });
    };

    window.rejectPipelineStrategy = function(pipelineId) {
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

    window.approvePipelineStrategy = function(pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        window.activePipeline = pipeline;

        // Render the Generator UI inside the modal
        const generatorHtml = `
            <h6 class="fw-bold text-primary mb-3"><i class="fas fa-magic me-2"></i>Content Generator</h6>
            <div id="modalGenerationPipelineBlock" class="d-flex flex-column gap-3 p-4 border rounded-3 bg-light mt-2">
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
                    <i class="fas fa-paperclip position-absolute" style="top: 15px; left: 15px; color: #9ca3af;"></i>
                    <textarea id="modalPipelinePrompt" class="form-control" rows="3" style="padding-left: 2.5rem; border-radius: 12px; resize: none;" placeholder="Attach an optional creative prompt (e.g. 'Use an energetic tone', 'Include branding colors')"></textarea>
                </div>
                <button class="btn btn-primary fw-bold rounded-pill w-100 py-3 shadow-sm mt-3" onclick="startModalPipelineGeneration(${pipeline.id})" id="startModalPipelineBtn">
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

    window.startModalPipelineGeneration = function(pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        const mediaType = $('input[name="modalMediaType"]:checked').val();
        const prompt = $('#modalPipelinePrompt').val();
        const platform = $('#dashboardPlatformSelect').val() || 'linkedin'; 

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
            success: function(res) {
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
                                    tone: generatedCount
                                }),
                                success: function(mediaRes) {
                                    if (mediaRes.success && mediaRes.url) {
                                        window.currentCarouselAssets.push({
                                            type: mediaType,
                                            content: mediaRes.url,
                                            caption: captions.primary_caption,
                                            platform: platform,
                                            prompt: prompt
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
                                        showToast('Media API failed on variation ' + (generatedCount+1), 'error');
                                        $('#startModalPipelineBtn').prop('disabled', false);
                                        $('#modalPipelineLoader').addClass('d-none');
                                    }
                                },
                                error: function() {
                                    showToast('Network error on variation ' + (generatedCount+1), 'error');
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
            error: function() {
                $('#startModalPipelineBtn').prop('disabled', false);
                $('#modalPipelineLoader').addClass('d-none');
                showToast('Error generating assets.', 'error');
            }
        });
    };

    window.rejectPipelineAsset = function(pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        pipeline.status = 'rejected';
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();
        
        showToast('Asset Rejected.', 'warning');
        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'asset_generated');
    };

    window.approvePipelineAsset = function(pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        pipeline.status = 'approved';
        localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
        renderPipelineHistory();
        
        showToast('Asset Approved! Ready for publishing.', 'success');
        
        renderPipelineModalStepper(window.pipelineHistory.indexOf(pipeline), 'approved');
        showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'approved');
    };

    window.publishModalPipelineContent = function(pipelineId) {
        const pipeline = window.pipelineHistory.find(p => p.id === pipelineId);
        if (!pipeline) return;

        const btn = $('#modalPublishBtn');
        const origText = btn.html();
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-2"></i>Publishing...');
        
        setTimeout(() => {
            btn.html('<i class="fas fa-check-circle me-2"></i>Published Successfully');
            btn.removeClass('btn-dark').addClass('btn-success');
            showToast('Asset published to platform!', 'success');
            
            pipeline.status = 'published';
            localStorage.setItem('straditPipelineHistory', JSON.stringify(window.pipelineHistory));
            renderPipelineHistory();
            
            setTimeout(() => {
                renderPipelineModalStepper(window.pipelineHistory.indexOf(pipeline), 'published');
                showPipelineStageDetail(window.pipelineHistory.indexOf(pipeline), 'published');
            }, 1500);
        }, 1500);
    };
});
