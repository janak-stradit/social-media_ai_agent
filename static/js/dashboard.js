$(document).ready(function() {
    
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

        $('#postsContainer').addClass('d-none');
        $('#postsLoader').removeClass('d-none');
        
        // Reset selections
        $('#selectedPostCount').text('0');
        $('#storyContextInput').val('');
        $('#generateStoryBtn').prop('disabled', true);

        $.ajax({
            url: '/api/platform-posts?platform=' + encodeURIComponent(platform),
            type: 'GET',
            success: function(r) {
                $('#postsLoader').addClass('d-none');
                $('#postsContainer').removeClass('d-none');

                if (r.success && r.posts && r.posts.length > 0) {
                    renderPlatformPosts(r.posts);
                } else {
                    $('#postsContainer').html(`
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

    function renderPlatformPosts(posts) {
        // Group posts by competitor and pick the latest one (first in the list)
        const latestPosts = {};
        posts.forEach(p => {
            const comp = p._source_competitor || 'Unknown Competitor';
            if (!latestPosts[comp]) {
                latestPosts[comp] = p; // Keep only the first one we see
            }
        });

        let html = '<div class="row g-4">';
        let cIdx = 0;
        
        for (const comp in latestPosts) {
            const p = latestPosts[comp];
            const title = p.title || 'Untitled Post';
            // Show more of the text since it's only one post
            const textSnippet = p.text ? p.text.substring(0, 300) + (p.text.length > 300 ? '...' : '') : 'No text content available.';
            const platformIcon = getPlatformIcon(p.platform);
            
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
                                <h6 class="mb-0 fw-bold text-dark">${comp}</h6>
                                <small class="text-muted fw-medium">Latest Post</small>
                            </div>
                        </div>
                        <div class="form-check m-0" style="transform: scale(1.3);">
                            <input class="form-check-input comp-master-checkbox cursor-pointer shadow-sm border-primary" type="checkbox" value="${cIdx}" id="masterCheck${cIdx}" data-payload="${encodedPayload}">
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
        }
        
        html += '</div>';
        
        $('#postsContainer').html(html);
        
        $('.comp-master-checkbox').on('change', function() {
            updateSelection();
        });
    }

    function updateSelection() {
        const checked = $('.comp-master-checkbox:checked');
        $('#selectedPostCount').text(checked.length);
        
        if (checked.length === 0) {
            $('#storyContextInput').val('');
            $('#generateStoryBtn').prop('disabled', true);
            return;
        }
        
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
            return $(this).closest('.col-md-6').find('h6.text-dark').text();
        }).get().join(', ');
        
        window.activePipeline = {
            id: Date.now(),
            timestamp: new Date().toISOString(),
            competitors: checkedLabels,
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

    window.copyStoryOutput = function() {
        const text = $('#storyOutput').val();
        if (!text) return;
        
        navigator.clipboard.writeText(text).then(() => {
            showToast('Copied to clipboard!', 'success');
        });
    }

    window.slideWorkflow = function(stepIndex) {
        // stepIndex: 0 = Context, 1 = Strategy, 2 = Generation
        const translation = -(stepIndex * 33.3333);
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
            
            // Build vertical timeline HTML
            const statuses = [
                { id: 'intel_selected', label: 'Competitor Intel Selected', icon: 'fa-check' },
                { id: 'strategy_generated', label: 'Counter Strategy Generated', icon: 'fa-brain' },
                { id: 'asset_generated', label: 'Content Generated', icon: 'fa-magic' },
                { id: 'approved', label: 'Asset Approved', icon: 'fa-thumbs-up' },
                { id: 'published', label: 'Published', icon: 'fa-paper-plane' }
            ];

            let timelineHtml = '<div class="timeline mt-3 px-2 border-start border-2 border-primary ms-2">';
            let reachedStatus = true;
            let pipelineStatus = pipeline.status || 'unknown';
            let hasError = pipelineStatus.startsWith('stopped') || pipelineStatus === 'rejected';

            for (const step of statuses) {
                let badgeClass = 'bg-secondary';
                let textClass = 'text-muted';
                let stepIcon = step.icon;

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
                    <div class="position-relative mb-3 ms-3">
                        <div class="position-absolute top-0 start-0 translate-middle-x rounded-circle ${badgeClass} d-flex align-items-center justify-content-center shadow-sm" style="width: 24px; height: 24px; margin-left: -17px; z-index: 10;">
                            <i class="fas ${stepIcon} text-white" style="font-size: 10px;"></i>
                        </div>
                        <div class="${textClass} small lh-sm" style="transform: translateY(-2px);">${step.label}</div>
                    </div>
                `;
            }
            timelineHtml += '</div>';

            html += `
                <div class="list-group-item list-group-item-action p-3 border-bottom bg-light bg-opacity-50">
                    <div class="d-flex w-100 justify-content-between mb-2">
                        <h6 class="mb-0 fw-bold text-dark"><i class="fas fa-layer-group me-2 text-primary"></i>Pipeline ID: ${pipeline.id}</h6>
                        <small class="text-muted" style="font-size: 0.7rem;">${date}</small>
                    </div>
                    <p class="mb-1 text-muted small"><strong>Competitors:</strong> ${pipeline.competitors || 'None'}</p>
                    <p class="mb-1 text-muted small"><strong>Asset:</strong> ${pipeline.assetType || 'Pending'}</p>
                    ${timelineHtml}
                    ${pipelineStatus === 'approved' || pipelineStatus === 'published' || pipelineStatus === 'asset_generated' ? 
                        `<button class="btn btn-sm btn-outline-primary mt-2 py-1 px-3 rounded-pill fw-bold" onclick="viewHistoryItem(${index})" style="font-size: 0.8rem;">View Pipeline Content</button>` : ''}
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

        
        // Hide offcanvas
        const offcanvas = bootstrap.Offcanvas.getInstance(document.getElementById('historyOffcanvas'));
        if (offcanvas) offcanvas.hide();
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
        const prompt = $('#pipelinePrompt').val().trim();
        const platform = $('#dashboardPlatformSelect').val();

        $('#startPipelineBtn').prop('disabled', true);
        $('#pipelineResultBlock').removeClass('d-none');
        $('#pipelineLoader').removeClass('d-none');
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
});
