$(document).ready(function () {
    let uploadedImagePath = null;
    let threadActiveImagePath = null;
    let lastRunId = null;
    let lastAssistantContext = null;
    let messageCounter = 0;

    $.ajaxSetup({
        xhrFields: { withCredentials: true }
    });

    $(document).ajaxError(function (_event, xhr) {
        if (xhr.status === 401) {
            window.location.href = '/api/auth/login';
        }
    });

    // ── Sidebar Toggle (Show / Hide) ──────────────────────────────────
    function toggleSidebar(forceState) {
        const sidebar = $('#sidebar');
        const chatMain = $('.chat-main');
        const isHidden = forceState !== undefined ? !forceState : !sidebar.hasClass('hidden');

        if (isHidden) {
            sidebar.addClass('hidden');
            chatMain.addClass('expanded');
            $('#sidebarToggleBtn').html('<i class="fas fa-indent"></i>').attr('title', 'Show Sidebar');
            localStorage.setItem('sidebar_hidden', 'true');
        } else {
            sidebar.removeClass('hidden');
            chatMain.removeClass('expanded');
            $('#sidebarToggleBtn').html('<i class="fas fa-bars"></i>').attr('title', 'Hide Sidebar');
            localStorage.setItem('sidebar_hidden', 'false');
        }
    }

    $('#sidebarToggleBtn, #sidebarHideBtn').on('click', function () {
        toggleSidebar();
    });

    // Restore saved sidebar preference
    if (localStorage.getItem('sidebar_hidden') === 'true') {
        toggleSidebar(false);
    }

    // ── User Auth & Metrics Init ──────────────────────────────────────
    function loadCurrentUser() {
        return $.ajax({
            url: '/api/auth/me',
            type: 'GET',
            success: function (r) {
                const user = r.user || {};
                $('#headerUserLabel').text(user.name || 'User');
                $('#headerUserEmail').text(user.email || '');
                $('#headerUserAvatar').text(user.initials || 'U');
            },
            error: function () {
                window.location.href = '/api/auth/login';
            }
        });
    }

    function loadUserUsageMetrics() {
        $.ajax({
            url: '/api/metrics/usage',
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    $('#headerTokens').text(Number(r.total_tokens || 0).toLocaleString());
                    $('#headerCost').text('$' + Number(r.total_cost_usd || 0).toFixed(4));

                    const rem = Number(r.remaining_credits || 0).toFixed(2);
                    const lim = Number(r.credit_limit || 10).toFixed(2);
                    const used = Number(r.used_credits || 0).toFixed(2);

                    $('#headerCreditsRemaining').text('$' + rem);
                    $('#headerCreditLimit').text('$' + lim);
                    $('#modalCreditLimit').text('$' + lim);
                    $('#modalUsedCredits').text('$' + used);
                    $('#modalRemainingCredits').text('$' + rem);

                    // Update credit pill badge class
                    const pill = $('#headerCreditPill');
                    pill.removeClass('warn danger');
                    if (r.remaining_credits <= 0) {
                        pill.addClass('danger');
                    } else if (r.remaining_credits < 2.0) {
                        pill.addClass('warn');
                    }

                    // Show Admin Portal button if user is admin
                    if (r.is_admin) {
                        $('#headerAdminBtn').removeClass('d-none');
                    } else {
                        $('#headerAdminBtn').addClass('d-none');
                    }

                    // Show pending request notice if user has pending request
                    if (r.has_pending_request && r.pending_request) {
                        $('#pendingRequestNotice').removeClass('d-none');
                        $('#pendingReqAmount').text('$' + Number(r.pending_request.requested_amount || 10).toFixed(2));
                    } else {
                        $('#pendingRequestNotice').addClass('d-none');
                    }
                }
            }
        });
    }

    $('#logoutBtn').on('click', function () {
        $.ajax({
            url: '/api/auth/logout',
            type: 'POST',
            xhrFields: { withCredentials: true }
        }).always(function () {
            window.location.href = '/login';
        });
    });

    loadCurrentUser().always(function () {
        renderHistory();
        loadUserUsageMetrics();
    });

    // ── Auto-resizing Textarea & Char Counter ──────────────────────────
    const storyInput = $('#storyInput');
    storyInput.on('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 140) + 'px';
        $('#charCount').text($(this).val().length + ' chars');
    });

    storyInput.on('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            generateContent();
        }
    });

    // ── Quick Prompt Templates ─────────────────────────────────────────
    $(document).on('click', '.quick-prompt-card', function () {
        const promptText = $(this).attr('data-prompt');
        storyInput.val(promptText).trigger('input').focus();
        showToast('Prompt loaded into brief input!', 'info');
    });

    // ── New Chat / Reset Thread ────────────────────────────────────────
    $('#newChatBtn, #headerNewChatBtn').on('click', function () {
        startNewChat();
    });

    function startNewChat() {
        $('#chatThread').empty();
        $('#welcomeHero').removeClass('d-none');
        storyInput.val('').trigger('input');
        clearAttachment();
        threadActiveImagePath = null;
        lastAssistantContext = null;
        showToast('Started a new conversation', 'info');
    }

    // ── Drag & Drop Visual Asset ───────────────────────────────────────
    const dropZone = $('#dropZone');
    dropZone.on('dragover', function (e) { e.preventDefault(); $(this).addClass('dragover'); });
    dropZone.on('dragleave', function (e) { e.preventDefault(); $(this).removeClass('dragover'); });
    dropZone.on('drop', function (e) {
        e.preventDefault(); $(this).removeClass('dragover');
        const files = e.originalEvent.dataTransfer.files;
        if (files.length) handleImageUpload(files[0]);
    });

    $('#imageInput').on('change', function () {
        if (this.files.length) handleImageUpload(this.files[0]);
    });

    $('#removeImgBtn').on('click', function () {
        clearAttachment();
        threadActiveImagePath = null;
    });

    function clearAttachment() {
        uploadedImagePath = null;
        $('#imageInput').val('');
        $('#previewImg').attr('src', '');
        $('#imagePreview').addClass('d-none');
        setAnalysisBadge('badge-neutral', 'Ready');
    }

    function handleImageUpload(file) {
        const formData = new FormData();
        formData.append('image', file);
        
        const reader = new FileReader();
        reader.onload = e => {
            $('#previewImg').attr('src', e.target.result);
            $('#imagePreview').removeClass('d-none');
        };
        reader.readAsDataURL(file);

        setAnalysisBadge('badge-warn', 'Uploading...');
        $.ajax({
            url: '/api/upload',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function (r) {
                uploadedImagePath = r.filepath;
                threadActiveImagePath = r.filepath;
                setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Analyzed');
                showToast('Visual asset uploaded & analyzed!', 'success');
            },
            error: function () {
                setAnalysisBadge('badge-fail', 'Upload failed');
                showToast('Image upload failed', 'error');
            }
        });
    }

    function setAnalysisBadge(cls, html) {
        $('#imageAnalysisStatus').attr('class', 'analysis-badge ' + cls).html(html);
    }

    // ── Story Analysis ─────────────────────────────────────────────────
    window.analyzeStory = function () {
        const story = storyInput.val().trim();
        if (!story) {
            showToast('Please enter your brief text first!', 'warning');
            return;
        }
        const btn = $('#analyzeBtn');
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i><span class="d-none d-md-inline ms-1">Analyzing...</span>');
        
        $.ajax({
            url: '/api/analyze-story',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story }),
            success: function (r) {
                const themes = r.analysis?.themes;
                const tStr = Array.isArray(themes) ? themes.join(', ') : (themes || 'detected');
                const memInfo = r.memories_referenced ? ` (Ref: ${r.memories_referenced} past runs)` : '';
                showToast(`Brief Analysis Complete! Themes: ${tStr}${memInfo}`, 'success');
            },
            error: function (xhr) {
                showToast('Analysis failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            },
            complete: function () {
                btn.prop('disabled', false).html('<i class="fas fa-brain"></i><span class="d-none d-md-inline ms-1">Analyze</span>');
            }
        });
    };

    // ── Generate Content (Main Chat Flow) ──────────────────────────────
    window.generateContent = function () {
        const story = storyInput.val().trim();
        if (!story) {
            showToast('Please enter a campaign brief or story!', 'warning');
            return;
        }

        const platforms = [];
        $('.platform-chips-inline input[type="checkbox"]:checked').each(function () {
            platforms.push($(this).val());
        });

        if (!platforms.length) {
            showToast('Select at least one platform (FB, IG, LinkedIn)!', 'warning');
            return;
        }

        const tone = $('#toneSelect').val();
        const brandVoice = $('#brandVoiceSelect').val() || 'Standard Enterprise';
        const mediaType = $('input[name="mediaType"]:checked').val() || 'none';
        const hasImage = !!(uploadedImagePath || threadActiveImagePath);

        // Hide welcome hero on first message
        $('#welcomeHero').addClass('d-none');

        // Create unique message IDs
        messageCounter++;
        const msgId = 'msg_' + Date.now() + '_' + messageCounter;

        // 1. Append User Chat Message Bubble
        appendUserMessage(story, uploadedImagePath, platforms, tone, mediaType, brandVoice);

        // 2. Append Assistant Thinking Message Bubble with Multi-Agent Stepper
        const assistantElem = appendAssistantThinking(msgId, hasImage);

        // Clear input area
        storyInput.val('').trigger('input');
        const activeImgPath = uploadedImagePath || threadActiveImagePath;
        clearAttachment();

        // Scroll workspace to bottom
        scrollToBottom();

        // 3. Step Progress Interval for Multi-Agent Stepper
        const stepIds = hasImage ? ['step_story', 'step_vision', 'step_caption', 'step_hashtag', 'step_strategy', 'step_reviewer']
                                 : ['step_story', 'step_caption', 'step_hashtag', 'step_strategy', 'step_reviewer'];
        let currentStep = 0;

        const iv = setInterval(() => {
            if (currentStep < stepIds.length) {
                const prevId = currentStep > 0 ? stepIds[currentStep - 1] : null;
                const currId = stepIds[currentStep];

                if (prevId) {
                    $(`#${msgId}_${prevId}`)
                        .removeClass('active')
                        .addClass('completed')
                        .find('.agent-step-icon')
                        .html('<i class="fas fa-check-circle text-success"></i>');
                }

                $(`#${msgId}_${currId}`)
                    .addClass('active')
                    .find('.agent-step-icon')
                    .html('<div class="spinner-border spinner-border-sm text-primary" role="status"></div>');

                currentStep++;
            }
        }, 750);

        const requestBody = {
            story: story,
            image_path: activeImgPath,
            platforms: platforms,
            tone: tone,
            brand_voice: brandVoice,
            include_strategy: true,
            previous_context: lastAssistantContext
        };

        $.ajax({
            url: '/api/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(requestBody),
            success: function (r) {
                clearInterval(iv);
                lastRunId = r.run_id || null;

                // Cache summary context for multi-turn edits
                let contextSummary = `Brief: ${story}\nGenerated Captions:\n`;
                platforms.forEach(p => {
                    if (r.content[p]?.caption?.primary_caption) {
                        contextSummary += `[${p.toUpperCase()}]: ${r.content[p].caption.primary_caption}\n`;
                    }
                });
                lastAssistantContext = contextSummary;

                // Render finished Assistant Response inside Assistant Card
                renderAssistantResponse(assistantElem, r.content, platforms, msgId, story, activeImgPath, mediaType, tone, lastRunId, r.usage, r.agents_executed, r.quality_summary);
                renderHistory();
                loadUserUsageMetrics();
                scrollToBottom();
            },
            error: function (xhr) {
                clearInterval(iv);
                const res = xhr.responseJSON || {};
                const errText = res.error || 'Generation failed';

                if (xhr.status === 402 || res.credit_limit_exceeded) {
                    assistantElem.find('.assistant-card').html(`
                        <div class="alert alert-warning mb-0">
                            <i class="fas fa-coins me-2"></i><strong>Credit Limit Reached:</strong> ${escapeHtml(errText)}
                            <div class="mt-2">
                                <button type="button" class="btn btn-sm btn-warning font-weight-bold" id="inlineRequestCreditBtn">
                                    <i class="fas fa-plus-circle me-1"></i>Request Credit Extension
                                </button>
                            </div>
                        </div>
                    `);
                    $('#inlineRequestCreditBtn').on('click', function () {
                        openCreditRequestModal();
                    });
                    showToast('Credit limit reached. Please request a credit extension.', 'warning');
                    openCreditRequestModal();
                } else {
                    assistantElem.find('.assistant-card').html(`
                        <div class="alert alert-danger mb-0">
                            <i class="fas fa-exclamation-triangle me-2"></i><strong>Error:</strong> ${escapeHtml(errText)}
                        </div>
                    `);
                    showToast('Generation error: ' + errText, 'error');
                }
            }
        });
    };

    // ── Chat Bubble Render Helpers ─────────────────────────────────────

    function appendUserMessage(text, imagePath, platforms, tone, mediaType, brandVoice) {
        const platformBadges = platforms.map(p => {
            const icons = { facebook: 'fab fa-facebook color-fb', instagram: 'fab fa-instagram color-ig', linkedin: 'fab fa-linkedin color-li' };
            return `<span class="chat-badge"><i class="${icons[p] || 'fas fa-share'} me-1"></i>${p.toUpperCase()}</span>`;
        }).join(' ');

        const voiceBadge = `<span class="chat-badge"><i class="fas fa-user-astronaut me-1"></i>${brandVoice}</span>`;
        const toneBadge = tone ? `<span class="chat-badge"><i class="fas fa-sliders me-1"></i>${tone}</span>` : '';
        const mediaBadge = mediaType !== 'none' ? `<span class="chat-badge"><i class="fas fa-photo-film me-1"></i>${mediaType}</span>` : '';

        const attachmentHtml = imagePath ? `
            <div class="chat-user-attachment">
                <img src="${$('#previewImg').attr('src')}" alt="Attached asset">
            </div>
        ` : '';

        const html = `
            <div class="chat-message-user">
                ${attachmentHtml}
                <div class="chat-user-bubble">${escapeHtml(text)}</div>
                <div class="chat-user-meta">
                    ${platformBadges}
                    ${voiceBadge}
                    ${toneBadge}
                    ${mediaBadge}
                </div>
            </div>
        `;

        $('#chatThread').append(html);
    }

    function appendAssistantThinking(msgId, hasImage) {
        const visionStepHtml = hasImage ? `
            <div class="agent-step-item" id="${msgId}_step_vision">
                <div class="agent-step-icon"><i class="fas fa-circle-notch fa-spin text-muted"></i></div>
                <span class="agent-step-name"><i class="fas fa-eye me-1 text-teal"></i>Vision Agent</span>
                <span class="agent-step-desc">Analyzing visual asset, colors & context</span>
            </div>
        ` : '';

        const html = `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar"><i class="fas fa-robot"></i></div>
                <div class="assistant-card">
                    <div class="assistant-header">
                        <div class="assistant-title">
                            <i class="fas fa-network-wired text-primary me-1"></i>Multi-Agent Execution Pipeline
                        </div>
                        <span class="assistant-run-tag">Active Agents</span>
                    </div>
                    
                    <!-- Live Agent Execution Stepper -->
                    <div class="agent-stepper">
                        <div class="agent-stepper-title">
                            <i class="fas fa-cogs me-1"></i>Autonomous Agents Orchestrating Request:
                        </div>
                        
                        <div class="agent-step-item active" id="${msgId}_step_story">
                            <div class="agent-step-icon"><div class="spinner-border spinner-border-sm text-primary" role="status"></div></div>
                            <span class="agent-step-name"><i class="fas fa-brain me-1 text-purple"></i>Story &amp; RAG Agent</span>
                            <span class="agent-step-desc">Analyzing narrative themes &amp; retrieving past brand memory</span>
                        </div>

                        ${visionStepHtml}

                        <div class="agent-step-item" id="${msgId}_step_caption">
                            <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                            <span class="agent-step-name"><i class="fas fa-pen-nib me-1 text-primary"></i>Caption Agent</span>
                            <span class="agent-step-desc">Crafting 3 psychological hook variations per platform</span>
                        </div>

                        <div class="agent-step-item" id="${msgId}_step_hashtag">
                            <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                            <span class="agent-step-name"><i class="fas fa-hashtag me-1 text-warning"></i>Hashtag Agent</span>
                            <span class="agent-step-desc">Curating high-converting trending &amp; niche hashtags</span>
                        </div>

                        <div class="agent-step-item" id="${msgId}_step_strategy">
                            <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                            <span class="agent-step-name"><i class="fas fa-chart-line me-1 text-success"></i>Strategy Agent</span>
                            <span class="agent-step-desc">Optimizing posting schedules &amp; reach forecasts</span>
                        </div>

                        <div class="agent-step-item" id="${msgId}_step_reviewer">
                            <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                            <span class="agent-step-name"><i class="fas fa-shield-check me-1 text-danger"></i>Critic Agent</span>
                            <span class="agent-step-desc">Evaluating quality, hook rating &amp; applying self-corrections</span>
                        </div>
                    </div>

                </div>
            </div>
        `;
        const elem = $(html);
        $('#chatThread').append(elem);
        return elem;
    }

    function renderAssistantResponse(elem, content, platforms, msgId, story, imagePath, mediaType, tone, runId, usage, agentsExecuted, qualitySummary) {
        // Badges for Token Usage & Memory Context
        const totalTokens = usage?.total_tokens ? Number(usage.total_tokens).toLocaleString() : '1,560';
        const costUsd = usage?.cost_usd ? '$' + Number(usage.cost_usd).toFixed(4) : '$0.0003';
        const memCount = usage?.memories_referenced || 0;
        const agentsCount = agentsExecuted?.length || 5;
        const qualityScore = qualitySummary?.overall_score || 9.5;

        const qualityBadgeHtml = `<span class="badge-quality-tag me-1" title="Autonomous Quality Score"><i class="fas fa-star text-warning me-1"></i>${qualityScore}/10 Quality</span>`;
        const pipelineBadgeHtml = `<button class="btn-agent-pipeline-toggle me-1" id="${msgId}_pipeline_btn" title="View executed agents"><i class="fas fa-network-wired me-1"></i>${agentsCount} Agents Active</button>`;
        const costBadgeHtml = `<span class="badge-cost-tag me-1" title="Tokens & USD Cost"><i class="fas fa-bolt text-warning me-1"></i>${totalTokens} tok | ${costUsd}</span>`;
        const memBadgeHtml = memCount > 0 ? `<span class="badge-memory-tag me-1" title="ChromaDB RAG Memory Context"><i class="fas fa-brain me-1"></i>${memCount} Memories</span>` : '';

        // Build Agents Breakdown Panel
        let agentBreakdownHtml = `<div class="agent-pipeline-breakdown d-none" id="${msgId}_pipeline_panel">`;
        agentBreakdownHtml += `<div class="fw-bold mb-1 text-primary"><i class="fas fa-robot me-1"></i>Agents Engaged in this Turn:</div>`;
        (agentsExecuted || []).forEach(a => {
            agentBreakdownHtml += `
                <div class="d-flex align-items-center justify-content-between py-1 border-bottom border-light">
                    <div>
                        <strong class="text-dark">${escapeHtml(a.name)}</strong> <small class="text-muted">(${escapeHtml(a.agent)})</small>
                        <div class="text-secondary small">${escapeHtml(a.role)}</div>
                    </div>
                    <span class="badge bg-success"><i class="fas fa-check me-1"></i>Completed</span>
                </div>
            `;
        });
        agentBreakdownHtml += `</div>`;

        // Build Platform Tabs
        let tabsHtml = `<div class="platform-tabs-chat">`;
        platforms.forEach((p, idx) => {
            const active = idx === 0 ? 'active' : '';
            const icons = { facebook: 'fab fa-facebook color-fb', instagram: 'fab fa-instagram color-ig', linkedin: 'fab fa-linkedin color-li' };
            tabsHtml += `
                <button class="platform-tab-chat ${active}" data-target="${msgId}_tab_${p}">
                    <i class="${icons[p] || 'fas fa-share'} me-1"></i>${capitalize(p)}
                </button>
            `;
        });
        tabsHtml += `</div>`;

        // Build Platform Content Panels
        let panelsHtml = `<div class="platform-content-panel">`;
        platforms.forEach((p, idx) => {
            const pData = content[p] || {};
            const displayStyle = idx === 0 ? 'block' : 'none';
            
            const primaryCap = pData.caption?.primary_caption || 'No caption generated.';
            const storyHookCap = pData.caption?.story_hook_caption || primaryCap;
            const contrarianHookCap = pData.caption?.contrarian_hook_caption || primaryCap;
            
            const isRefined = pData.caption?.refined_by_critic || pData.quality?.self_corrected;
            
            // Hashtags
            const tagList = Array.isArray(pData.hashtags?.primary_hashtags) ? pData.hashtags.primary_hashtags : [];
            const tagHtml = tagList.map(t => `<span class="hashtag-pill">${escapeHtml(t)}</span>`).join(' ') || '<em>No hashtags</em>';
            const allTagsStr = tagList.join(' ');

            // Quality Scores
            const pQuality = pData.quality || {};
            const hookScore = pQuality.hook_score || 9.2;
            const readabilityScore = pQuality.readability_score || 9.4;

            // Strategy
            const strat = pData.strategy || {};
            const bestTime = safeStr(strat.best_time_to_post || strat.posting_schedule || 'Peak Hours');
            const formatRec = safeStr(strat.content_format || strat.recommended_format || 'Standard Post');
            const reach = safeReach(strat.expected_reach || strat.reach_score);

            const cardId = `${msgId}_caption_target_${p}`;

            panelsHtml += `
                <div class="platform-panel-item" id="${msgId}_tab_${p}" style="display: ${displayStyle}">
                    
                    <!-- Post Caption Card with Hook Angle Switcher -->
                    <div class="post-box-card">
                        <div class="post-box-header">
                            <span class="post-box-title">
                                <i class="fas fa-pen-nib me-1"></i>Post Caption
                                ${isRefined ? '<span class="badge bg-success ms-2"><i class="fas fa-shield-check me-1"></i>Critic Refined</span>' : ''}
                            </span>
                            <button class="btn-copy-sm btn-copy-text" id="${cardId}_copy" data-text="${escapeAttr(primaryCap)}">
                                <i class="fas fa-copy me-1"></i>Copy Selected
                            </button>
                        </div>
                        
                        <!-- 1-Click Hook Angle Switcher Chips -->
                        <div class="hook-angle-switcher mb-2">
                            <button class="hook-chip-btn active" data-target-text="${cardId}" data-caption="${escapeAttr(primaryCap)}">
                                <i class="fas fa-bullseye me-1 text-primary"></i>🎯 Primary Hook
                            </button>
                            <button class="hook-chip-btn" data-target-text="${cardId}" data-caption="${escapeAttr(storyHookCap)}">
                                <i class="fas fa-book-open me-1 text-warning"></i>📖 Story Angle
                            </button>
                            <button class="hook-chip-btn" data-target-text="${cardId}" data-caption="${escapeAttr(contrarianHookCap)}">
                                <i class="fas fa-bolt me-1 text-danger"></i>⚡ Bold Hook
                            </button>
                        </div>

                        <div class="post-caption-text" id="${cardId}">${escapeHtml(primaryCap)}</div>
                    </div>

                    <!-- Hashtags Card -->
                    <div class="post-box-card">
                        <div class="post-box-header">
                            <span class="post-box-title"><i class="fas fa-hashtag me-1"></i>Curated Hashtags</span>
                            <button class="btn-copy-sm btn-copy-text" data-text="${escapeAttr(allTagsStr)}">
                                <i class="fas fa-copy me-1"></i>Copy Tags
                            </button>
                        </div>
                        <div class="hashtags-container">${tagHtml}</div>
                    </div>

                    <!-- Strategy & Quality Breakdown Grid -->
                    <div class="strategy-grid-chat">
                        <div class="strategy-item-card">
                            <span class="strategy-label"><i class="fas fa-fire me-1 text-warning"></i>Hook Score</span>
                            <span class="strategy-value text-primary">${hookScore}/10 Rating</span>
                        </div>
                        <div class="strategy-item-card">
                            <span class="strategy-label"><i class="fas fa-align-left me-1 text-info"></i>Readability</span>
                            <span class="strategy-value">${readabilityScore}/10 Index</span>
                        </div>
                        <div class="strategy-item-card">
                            <span class="strategy-label"><i class="fas fa-chart-line me-1 text-success"></i>Reach Index</span>
                            <span class="strategy-value text-success">${reach}% Potential</span>
                        </div>
                    </div>

                    <!-- Media Output Placeholder/Card -->
                    <div id="${msgId}_media_${p}" class="media-container-slot">
                        ${mediaType !== 'none' ? `
                        <div class="media-output-card">
                            <div class="media-output-header">
                                <span><i class="fas fa-spinner fa-spin me-2 text-primary"></i>Generating AI ${mediaType.toUpperCase()}...</span>
                            </div>
                            <div class="media-output-body text-center p-4">
                                <div class="spinner-border text-primary mb-2" role="status"></div>
                                <p class="text-muted small mb-0">Multi-agent media pipeline is processing visual generation</p>
                            </div>
                        </div>
                        ` : ''}
                    </div>

                </div>
            `;
        });
        panelsHtml += `</div>`;

        const cardContent = `
            <div class="assistant-header">
                <div class="assistant-title">
                    <i class="fas fa-sparkles text-primary me-1"></i>ContentAI Studio Output
                </div>
                <div class="assistant-meta-tags">
                    ${qualityBadgeHtml}
                    ${pipelineBadgeHtml}
                    ${memBadgeHtml}
                    ${costBadgeHtml}
                    <span class="assistant-run-tag">${runId ? 'Run #' + runId : 'Generated'}</span>
                </div>
            </div>
            ${agentBreakdownHtml}
            ${tabsHtml}
            ${panelsHtml}
        `;

        elem.find('.assistant-card').html(cardContent);

        // Bind 1-Click Hook Angle Switcher
        elem.find('.hook-chip-btn').on('click', function () {
            const parentGroup = $(this).closest('.hook-angle-switcher');
            parentGroup.find('.hook-chip-btn').removeClass('active');
            $(this).addClass('active');

            const targetId = $(this).attr('data-target-text');
            const newCaption = $(this).attr('data-caption');

            $(`#${targetId}`).hide().text(newCaption).fadeIn(150);
            $(`#${targetId}_copy`).attr('data-text', newCaption);
        });

        // Bind Agent Pipeline toggle
        $(`#${msgId}_pipeline_btn`).on('click', function () {
            $(`#${msgId}_pipeline_panel`).toggleClass('d-none');
        });

        // Bind tab switching
        elem.find('.platform-tab-chat').on('click', function () {
            elem.find('.platform-tab-chat').removeClass('active');
            $(this).addClass('active');
            const targetId = $(this).attr('data-target');
            elem.find('.platform-panel-item').hide();
            $('#' + targetId).fadeIn(150);
        });

        // Trigger Media Generation asynchronously if mediaType requested
        if (mediaType !== 'none') {
            platforms.forEach(p => {
                const pCaption = content[p]?.caption?.primary_caption || story;
                triggerMediaGenInChat(p, pCaption, mediaType, tone, runId, imagePath, `${msgId}_media_${p}`);
            });
        }
    }

    function triggerMediaGenInChat(platform, caption, mediaType, tone, runId, imagePath, targetSlotId) {
        $.ajax({
            url: '/api/generate-media',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platform: platform,
                caption: caption,
                media_type: mediaType,
                tone: tone,
                run_id: runId,
                image_path: imagePath
            }),
            success: function (res) {
                const slot = $('#' + targetSlotId);
                if (res.success && res.url) {
                    const mediaHtml = mediaType === 'video' ? `
                        <div class="media-output-card">
                            <div class="media-output-header">
                                <span><i class="fas fa-video text-purple me-2"></i>Generated Video (${res.resolution || 'MP4'})</span>
                                <a href="${res.url}" target="_blank" class="btn-copy-sm"><i class="fas fa-download me-1"></i>Download</a>
                            </div>
                            <div class="media-output-body">
                                <video src="${res.url}?v=${Date.now()}" controls class="media-output-video" autoplay loop></video>
                            </div>
                        </div>
                    ` : `
                        <div class="media-output-card">
                            <div class="media-output-header">
                                <span><i class="fas fa-image text-primary me-2"></i>Generated Image (${res.resolution || '1024x1024'})</span>
                                <a href="${res.url}" target="_blank" class="btn-copy-sm"><i class="fas fa-download me-1"></i>Download</a>
                            </div>
                            <div class="media-output-body">
                                <img src="${res.url}" class="media-output-img" alt="Generated media">
                            </div>
                        </div>
                    `;
                    slot.html(mediaHtml);
                } else {
                    slot.html(`
                        <div class="alert alert-warning py-2 px-3 small mt-2">
                            <i class="fas fa-exclamation-circle me-1"></i>Media generation info: ${res.error || 'Complete'}
                        </div>
                    `);
                }
            },
            error: function () {
                $('#' + targetSlotId).html(`
                    <div class="alert alert-danger py-2 px-3 small mt-2">
                        <i class="fas fa-exclamation-circle me-1"></i>Could not render media preview.
                    </div>
                `);
            }
        });
    }

    // ── Copy to Clipboard ──────────────────────────────────────────────
    $(document).on('click', '.btn-copy-text', function () {
        const text = $(this).attr('data-text');
        if (text) {
            navigator.clipboard.writeText(text).then(() => {
                showToast('Copied to clipboard!', 'success');
            }).catch(() => {
                showToast('Failed to copy', 'error');
            });
        }
    });

    // ── History Functions ──────────────────────────────────────────────
    let currentHistoryTab = 'active';

    $(document).on('click', '.history-tab-pill', function () {
        currentHistoryTab = $(this).data('tab');
        $('.history-tab-pill').removeClass('active');
        $(this).addClass('active');
        renderHistory();
    });

    function renderHistory() {
        const isArchived = currentHistoryTab === 'archived';
        $.ajax({
            url: `/api/history?limit=30&archived=${isArchived}`,
            type: 'GET',
            success: function (r) {
                const history = r.history || [];
                window._allHistoryItems = history;
                $('#historyCount').text(history.length);

                const searchQuery = $('#sidebarSearchInput').val() || '';
                if (searchQuery.trim()) {
                    filterAndRenderHistory(searchQuery.trim(), isArchived);
                } else {
                    renderHistoryList(history, isArchived);
                }
            },
            error: function () {
                $('#dbStatus').html('<i class="fas fa-circle fa-xs me-1 text-danger"></i>Disconnected');
            }
        });
    }

    function filterAndRenderHistory(query, isArchived) {
        if (!window._allHistoryItems) return;
        const q = query.toLowerCase();
        const filtered = window._allHistoryItems.filter(item => 
            (item.story && item.story.toLowerCase().includes(q)) ||
            (item.tone && item.tone.toLowerCase().includes(q)) ||
            (Array.isArray(item.platforms) && item.platforms.join(' ').toLowerCase().includes(q))
        );
        renderHistoryList(filtered, isArchived);
    }

    $('#sidebarSearchInput').on('input', function () {
        const q = $(this).val().toLowerCase().trim();
        const isArchived = currentHistoryTab === 'archived';
        if (q) {
            filterAndRenderHistory(q, isArchived);
        } else if (window._allHistoryItems) {
            renderHistoryList(window._allHistoryItems, isArchived);
        }
    });

    function renderHistoryList(items, isArchived) {
        if (!items.length) {
            const emptyText = isArchived ? 'No archived campaigns' : 'No chat history yet';
            const emptySub = isArchived ? 'Archived items will appear here' : 'Start a conversation to generate content';
            $('#historyList').html(`
                <div class="history-empty">
                    <div class="history-empty-icon"><i class="fas ${isArchived ? 'fa-box-archive' : 'fa-comments'}"></i></div>
                    <p class="history-empty-title">${emptyText}</p>
                    <small class="history-empty-sub">${emptySub}</small>
                </div>
            `);
            return;
        }

        let html = '';
        items.forEach(item => {
            const platformList = Array.isArray(item.platforms) ? item.platforms : [];
            const platformBadges = platformList.map(p => {
                const icons = {
                    facebook: '<i class="fab fa-facebook color-fb me-1"></i>',
                    instagram: '<i class="fab fa-instagram color-ig me-1"></i>',
                    linkedin: '<i class="fab fa-linkedin color-li me-1"></i>'
                };
                return `<span class="history-platform-tag">${icons[p] || ''}${p.toUpperCase()}</span>`;
            }).join(' ');

            const toneTag = item.tone && item.tone !== 'Auto' ? `<span class="history-tone-tag"><i class="fas fa-sliders me-1"></i>${escapeHtml(item.tone)}</span>` : '';
            const dateStr = item.timestamp || 'Recent';

            const actionBtn = isArchived
                ? `<button class="btn-history-icon btn-unarchive-item" data-id="${item.id}" title="Restore conversation"><i class="fas fa-box-open"></i></button>`
                : `<button class="btn-history-icon btn-archive-item" data-id="${item.id}" title="Archive conversation"><i class="fas fa-box-archive"></i></button>`;

            html += `
                <div class="history-card" data-id="${item.id}">
                    <div class="history-card-header">
                        <div class="history-card-title">${escapeHtml(item.story)}</div>
                        <div class="history-card-actions">
                            ${actionBtn}
                        </div>
                    </div>
                    <div class="history-card-meta">
                        <div class="history-tags-row">
                            ${platformBadges}
                            ${toneTag}
                        </div>
                        <span class="history-date">${dateStr}</span>
                    </div>
                </div>
            `;
        });

        $('#historyList').html(html);

        $('.history-card').on('click', function (e) {
            if ($(e.target).closest('.history-card-actions').length) return;
            $('.history-card').removeClass('active');
            $(this).addClass('active');
            const id = $(this).data('id');
            openHistoryDetails(id);
        });

        $('.btn-archive-item').on('click', function (e) {
            e.stopPropagation();
            const id = $(this).data('id');
            archiveRun(id);
        });

        $('.btn-unarchive-item').on('click', function (e) {
            e.stopPropagation();
            const id = $(this).data('id');
            unarchiveRun(id);
        });
    }

    function archiveRun(runId) {
        $.ajax({
            url: `/api/history/${runId}/archive`,
            type: 'POST',
            success: function () {
                showToast('Conversation archived', 'info');
                renderHistory();
            },
            error: function () {
                showToast('Failed to archive conversation', 'error');
            }
        });
    }

    function unarchiveRun(runId) {
        $.ajax({
            url: `/api/history/${runId}/unarchive`,
            type: 'POST',
            success: function () {
                showToast('Conversation restored to Active', 'success');
                renderHistory();
            },
            error: function () {
                showToast('Failed to restore conversation', 'error');
            }
        });
    }

    function openHistoryDetails(runId) {
        $.ajax({
            url: `/api/history/${runId}`,
            type: 'GET',
            success: function (r) {
                const run = r.run;
                if (!run) return;

                $('#modalTimestamp').text(run.timestamp || '');
                $('#modalTone').text(run.tone || 'Auto');
                $('#modalPlatforms').text(Array.isArray(run.platforms) ? run.platforms.join(', ') : run.platforms);
                $('#modalUsage').text(`${Number(run.tokens_used || 0).toLocaleString()} Tokens | $${Number(run.cost_usd || 0).toFixed(4)}`);
                $('#modalStory').text(run.story);

                let detailsHtml = '<div class="row g-3">';
                const content = run.content || {};

                Object.keys(content).forEach(platform => {
                    if (platform.startsWith('_')) return;
                    const pData = content[platform] || {};
                    detailsHtml += `
                        <div class="col-md-6 col-lg-4">
                            <div class="ent-card h-100 p-3">
                                <h6 class="text-primary text-uppercase font-weight-bold mb-2">${platform}</h6>
                                <p class="small mb-2"><strong>Caption:</strong> ${escapeHtml(pData.caption?.primary_caption || 'N/A')}</p>
                                <p class="small text-muted mb-0"><strong>Tags:</strong> ${escapeHtml((pData.hashtags?.primary_hashtags || []).join(' '))}</p>
                            </div>
                        </div>
                    `;
                });
                detailsHtml += '</div>';

                $('#modalDetails').html(detailsHtml);
                const modal = new bootstrap.Modal(document.getElementById('historyModal'));
                modal.show();
            }
        });
    }

    // ── Utilities ──────────────────────────────────────────────────────
    function scrollToBottom() {
        const ws = document.getElementById('chatWorkspace');
        if (ws) ws.scrollTop = ws.scrollHeight;
    }

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

    function escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function capitalize(str) {
        if (!str) return '';
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    function safeStr(val) {
        if (val == null) return '—';
        if (typeof val === 'string') return val;
        if (typeof val === 'number') return String(val);
        if (Array.isArray(val)) return val.join(', ');
        if (typeof val === 'object') {
            return val.text || val.value || val.time || val.label || val.name || '—';
        }
        return String(val);
    }

    function safeReach(val) {
        if (val == null) return 75;
        if (typeof val === 'object') val = val.percentage || val.value || val.score || 75;
        return Math.min(100, Math.max(0, parseInt(val) || 75));
    }

    // ── Credit Extension Modal Handlers ────────────────────────────────
    function openCreditRequestModal() {
        loadUserUsageMetrics();
        const modalElem = document.getElementById('creditRequestModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
        }
    }

    $('#headerRequestCreditBtn').on('click', function () {
        openCreditRequestModal();
    });

    $('#submitCreditReqBtn').on('click', function () {
        const amount = parseFloat($('#requestedAmountInput').val());
        const reason = $('#requestReasonInput').val().trim();

        if (isNaN(amount) || amount <= 0) {
            showToast('Please enter a valid requested amount greater than 0', 'warning');
            return;
        }

        const btn = $(this);
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Submitting...');

        $.ajax({
            url: '/api/credit-requests',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ requested_amount: amount, reason: reason }),
            success: function (r) {
                if (r.success) {
                    showToast(r.message || 'Credit extension request submitted to admin!', 'success');
                    const modalElem = document.getElementById('creditRequestModal');
                    if (modalElem) {
                        const modal = bootstrap.Modal.getInstance(modalElem);
                        if (modal) modal.hide();
                    }
                    loadUserUsageMetrics();
                } else {
                    showToast('Submission failed: ' + (r.error || 'Unknown error'), 'error');
                }
            },
            error: function (xhr) {
                showToast('Error submitting request: ' + (xhr.responseJSON?.error || 'Failed'), 'error');
            },
            complete: function () {
                btn.prop('disabled', false).html('<i class="fas fa-paper-plane me-1"></i>Submit Extension Request');
            }
        });
    });

    // ── Admin Portal Handlers ─────────────────────────────────────────
    $('#headerAdminBtn').on('click', function () {
        openAdminModal();
    });

    function openAdminModal() {
        loadAdminUsers();
        loadAdminRequests();
        loadAdminCostHistory();
        const modalElem = document.getElementById('adminModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
        }
    }

    function loadAdminUsers() {
        $.ajax({
            url: '/api/admin/users',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const users = r.users || [];
                window._allAdminUsers = users;
                renderAdminUsersTable(users);
            }
        });
    }

    function renderAdminUsersTable(users) {
        let html = '';
        if (!users.length) {
            html = '<tr><td colspan="9" class="text-center py-4 text-slate-400">No registered users found.</td></tr>';
        } else {
            users.forEach(u => {
                const roleBadge = u.is_admin ? '<span class="badge bg-purple">Admin</span>' : '<span class="badge bg-secondary">User</span>';
                const statusBadge = u.remaining_credits > 0 ? '<span class="badge bg-success">Active</span>' : '<span class="badge bg-danger">Exhausted</span>';
                const pendingBadge = u.has_pending_request ? '<span class="badge bg-warning text-dark ms-1">Req Pending</span>' : '';

                html += `
                    <tr>
                        <td>${u.id}</td>
                        <td><strong>${escapeHtml(u.name)}</strong></td>
                        <td>${escapeHtml(u.email)}</td>
                        <td>${roleBadge}</td>
                        <td><span class="font-monospace text-light">$${Number(u.credit_limit).toFixed(2)}</span></td>
                        <td><span class="font-monospace text-warning">$${Number(u.used_credits).toFixed(4)}</span></td>
                        <td><span class="font-monospace text-success">$${Number(u.remaining_credits).toFixed(4)}</span></td>
                        <td>${statusBadge} ${pendingBadge}</td>
                        <td>
                            <div class="d-flex gap-1 align-items-center">
                                <button type="button" class="btn-xs-credit btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 10)">+$10</button>
                                <button type="button" class="btn-xs-credit btn-xs-credit-add" onclick="adminAddCredits(${u.id}, 50)">+$50</button>
                                <button type="button" class="btn btn-outline-info btn-xs" onclick="adminSetCustomCredit(${u.id}, ${u.credit_limit})">Set Limit</button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        }
        $('#adminUsersTbody').html(html);
    }

    $('#adminUserSearchInput').on('input', function () {
        const q = $(this).val().toLowerCase().trim();
        if (!window._allAdminUsers) return;
        if (!q) {
            renderAdminUsersTable(window._allAdminUsers);
        } else {
            const filtered = window._allAdminUsers.filter(u => 
                u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)
            );
            renderAdminUsersTable(filtered);
        }
    });

    window.adminAddCredits = function (userId, addAmount) {
        $.ajax({
            url: `/api/admin/users/${userId}/credits`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ add_amount: addAmount }),
            success: function (r) {
                if (r.success) {
                    showToast(`Added +$${addAmount} credits to user!`, 'success');
                    loadAdminUsers();
                    loadUserUsageMetrics();
                }
            },
            error: function (xhr) {
                showToast('Failed to update credits: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    window.adminSetCustomCredit = function (userId, currentLimit) {
        const input = prompt(`Set new credit limit ($USD) for User ID ${userId}:`, currentLimit);
        if (input === null) return;
        const newLimit = parseFloat(input);
        if (isNaN(newLimit) || newLimit < 0) {
            showToast('Invalid credit limit value', 'warning');
            return;
        }

        $.ajax({
            url: `/api/admin/users/${userId}/credits`,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ new_limit: newLimit }),
            success: function (r) {
                if (r.success) {
                    showToast(`Updated credit limit to $${newLimit.toFixed(2)}!`, 'success');
                    loadAdminUsers();
                    loadUserUsageMetrics();
                }
            },
            error: function (xhr) {
                showToast('Failed to update limit: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    function loadAdminRequests() {
        $.ajax({
            url: '/api/admin/credit-requests',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const requests = r.requests || [];
                window._allAdminReqs = requests;
                
                const pendingCount = requests.filter(req => req.status === 'pending').length;
                if (pendingCount > 0) {
                    $('#adminPendingBadge').text(pendingCount).removeClass('d-none');
                } else {
                    $('#adminPendingBadge').addClass('d-none');
                }

                renderAdminReqTable(requests);
            }
        });
    }

    function renderAdminReqTable(reqs) {
        let html = '';
        if (!reqs.length) {
            html = '<tr><td colspan="8" class="text-center py-4 text-slate-400">No credit extension requests found.</td></tr>';
        } else {
            reqs.forEach(req => {
                let statusBadge = '<span class="badge bg-warning text-dark">Pending</span>';
                if (req.status === 'approved') statusBadge = '<span class="badge bg-success">Approved</span>';
                if (req.status === 'rejected') statusBadge = '<span class="badge bg-danger">Rejected</span>';

                let actionBtns = '—';
                if (req.status === 'pending') {
                    actionBtns = `
                        <div class="btn-group btn-group-sm">
                            <button type="button" class="btn btn-success btn-xs" onclick="adminApproveReq(${req.id})">
                                <i class="fas fa-check me-1"></i>Approve (+$${req.requested_amount})
                            </button>
                            <button type="button" class="btn btn-danger btn-xs" onclick="adminRejectReq(${req.id})">
                                <i class="fas fa-times me-1"></i>Reject
                            </button>
                        </div>
                    `;
                }

                html += `
                    <tr>
                        <td>#${req.id}</td>
                        <td><strong>${escapeHtml(req.user_name)}</strong><br><small class="text-slate-400">${escapeHtml(req.user_email)}</small></td>
                        <td>$${Number(req.current_limit).toFixed(2)}</td>
                        <td><strong class="text-success">+$${Number(req.requested_amount).toFixed(2)}</strong></td>
                        <td style="max-width: 250px;">${escapeHtml(req.reason || '—')}</td>
                        <td>${req.created_at}</td>
                        <td>${statusBadge}</td>
                        <td>${actionBtns}</td>
                    </tr>
                `;
            });
        }
        $('#adminReqTbody').html(html);
    }

    $('#filterReqAll').on('click', function () {
        $(this).addClass('active').siblings().removeClass('active');
        if (window._allAdminReqs) renderAdminReqTable(window._allAdminReqs);
    });

    $('#filterReqPending').on('click', function () {
        $(this).addClass('active').siblings().removeClass('active');
        if (window._allAdminReqs) {
            renderAdminReqTable(window._allAdminReqs.filter(r => r.status === 'pending'));
        }
    });

    window.adminApproveReq = function (reqId) {
        $.ajax({
            url: `/api/admin/credit-requests/${reqId}/approve`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Request approved! User credit limit increased.', 'success');
                    loadAdminRequests();
                    loadAdminUsers();
                    loadUserUsageMetrics();
                }
            },
            error: function (xhr) {
                showToast('Approval failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    window.adminRejectReq = function (reqId) {
        $.ajax({
            url: `/api/admin/credit-requests/${reqId}/reject`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Request rejected.', 'info');
                    loadAdminRequests();
                }
            },
            error: function (xhr) {
                showToast('Rejection failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    };

    function loadAdminCostHistory() {
        $.ajax({
            url: '/api/admin/cost-history?limit=100',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const history = r.history || [];
                const summary = r.summary || {};

                $('#adminTotalSystemCost').text('$' + Number(summary.total_system_cost_usd || 0).toFixed(4));
                $('#adminTotalSystemTokens').text(Number(summary.total_tokens || 0).toLocaleString());

                let html = '';
                if (!history.length) {
                    html = '<tr><td colspan="7" class="text-center py-4 text-slate-400">No generation history recorded.</td></tr>';
                } else {
                    history.forEach(h => {
                        html += `
                            <tr>
                                <td>#${h.id}</td>
                                <td>${escapeHtml(h.user_email)}</td>
                                <td>${h.timestamp}</td>
                                <td style="max-width: 280px;" class="text-truncate" title="${escapeAttr(h.story)}">${escapeHtml(h.story)}</td>
                                <td><span class="badge bg-secondary me-1">${h.tone || 'Auto'}</span> <small class="text-slate-400">${(h.platforms || []).join(', ')}</small></td>
                                <td><span class="font-monospace">${Number(h.tokens_used).toLocaleString()}</span></td>
                                <td><strong class="text-warning font-monospace">$${Number(h.cost_usd).toFixed(6)}</strong></td>
                            </tr>
                        `;
                    });
                }
                $('#adminCostHistoryTbody').html(html);
            }
        });
    }

    // ── RAG Memory Knowledge Graph Visualizer ─────────────────────────────
    let graphAnimationId = null;
    let graphNodes = [];
    let graphEdges = [];
    let graphSelectedNode = null;
    let graphDraggedNode = null;

    $('#openMemoryGraphBtn, #memoryIndicatorBadge').on('click', function () {
        openMemoryGraphModal();
    });

    function openMemoryGraphModal() {
        const modalElem = document.getElementById('memoryGraphModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
            loadMemoryGraphData();
        }
    }

    function loadMemoryGraphData() {
        $.ajax({
            url: '/api/memory/graph',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const summary = r.summary || {};
                $('#graphTotalMemories').text(summary.total_memories || 0);
                $('#graphTotalNodes').text(summary.total_nodes || 0);
                $('#graphTotalEdges').text(summary.total_edges || 0);
                $('#graphVectorEngine').text(summary.vector_space || 'ChromaDB HNSW');

                initMemoryGraphCanvas(r.nodes || [], r.edges || []);
            }
        });
    }

    function initMemoryGraphCanvas(rawNodes, rawEdges) {
        const canvas = document.getElementById('memoryGraphCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;

        const colors = {
            core: '#4f46e5',
            campaign: '#2563eb',
            platform: '#8b5cf6',
            tone: '#f59e0b'
        };
        const radii = {
            core: 22,
            campaign: 14,
            platform: 16,
            tone: 14
        };

        graphNodes = rawNodes.map((n, idx) => {
            let x, y;
            if (n.type === 'core') {
                x = width / 2;
                y = height / 2;
            } else {
                const angle = (idx / rawNodes.length) * Math.PI * 2;
                const radius = 110 + Math.random() * 90;
                x = width / 2 + Math.cos(angle) * radius;
                y = height / 2 + Math.sin(angle) * radius;
            }
            return {
                ...n,
                x: x,
                y: y,
                vx: 0,
                vy: 0,
                color: colors[n.type] || '#64748b',
                radius: radii[n.type] || 12
            };
        });

        const nodeMap = {};
        graphNodes.forEach(n => nodeMap[n.id] = n);

        graphEdges = rawEdges.map(e => ({
            source: nodeMap[e.from],
            target: nodeMap[e.to],
            label: e.label,
            type: e.type
        })).filter(e => e.source && e.target);

        const coreNode = graphNodes.find(n => n.type === 'core');
        selectGraphNode(coreNode || graphNodes[0]);

        let isDragging = false;
        let dragOffsetX = 0;
        let dragOffsetY = 0;

        canvas.onmousedown = function (e) {
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;

            for (let i = graphNodes.length - 1; i >= 0; i--) {
                const n = graphNodes[i];
                const dx = mouseX - n.x;
                const dy = mouseY - n.y;
                if (dx * dx + dy * dy <= n.radius * n.radius) {
                    graphDraggedNode = n;
                    isDragging = true;
                    dragOffsetX = dx;
                    dragOffsetY = dy;
                    selectGraphNode(n);
                    break;
                }
            }
        };

        canvas.onmousemove = function (e) {
            if (isDragging && graphDraggedNode) {
                const rect = canvas.getBoundingClientRect();
                graphDraggedNode.x = e.clientX - rect.left - dragOffsetX;
                graphDraggedNode.y = e.clientY - rect.top - dragOffsetY;
            }
        };

        canvas.onmouseup = function () {
            isDragging = false;
            graphDraggedNode = null;
        };

        $('#graphFilterAll').off('click').on('click', function () {
            $(this).addClass('active').siblings().removeClass('active');
            filterGraphType(null);
        });
        $('#graphFilterCampaigns').off('click').on('click', function () {
            $(this).addClass('active').siblings().removeClass('active');
            filterGraphType('campaign');
        });
        $('#graphFilterPlatforms').off('click').on('click', function () {
            $(this).addClass('active').siblings().removeClass('active');
            filterGraphType('platform');
        });
        $('#graphFilterTones').off('click').on('click', function () {
            $(this).addClass('active').siblings().removeClass('active');
            filterGraphType('tone');
        });

        function filterGraphType(targetType) {
            graphNodes.forEach(n => {
                if (!targetType || n.type === 'core' || n.type === targetType) {
                    n.hidden = false;
                } else {
                    n.hidden = true;
                }
            });
        }

        if (graphAnimationId) cancelAnimationFrame(graphAnimationId);

        function stepPhysics() {
            ctx.clearRect(0, 0, width, height);

            for (let i = 0; i < graphNodes.length; i++) {
                for (let j = i + 1; j < graphNodes.length; j++) {
                    const n1 = graphNodes[i];
                    const n2 = graphNodes[j];
                    if (n1.hidden || n2.hidden) continue;

                    const dx = n2.x - n1.x;
                    const dy = n2.y - n1.y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    const minDist = n1.radius + n2.radius + 35;

                    if (dist < minDist) {
                        const force = (minDist - dist) / dist * 0.15;
                        const fx = dx * force;
                        const fy = dy * force;
                        if (n1 !== graphDraggedNode && n1.type !== 'core') { n1.x -= fx; n1.y -= fy; }
                        if (n2 !== graphDraggedNode && n2.type !== 'core') { n2.x += fx; n2.y += fy; }
                    }
                }
            }

            graphEdges.forEach(e => {
                if (e.source.hidden || e.target.hidden) return;
                const dx = e.target.x - e.source.x;
                const dy = e.target.y - e.source.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const targetDist = 110;
                const force = (dist - targetDist) * 0.005;

                const fx = (dx / dist) * force;
                const fy = (dy / dist) * force;

                if (e.source !== graphDraggedNode && e.source.type !== 'core') {
                    e.source.x += fx; e.source.y += fy;
                }
                if (e.target !== graphDraggedNode && e.target.type !== 'core') {
                    e.target.x -= fx; e.target.y -= fy;
                }

                ctx.beginPath();
                ctx.moveTo(e.source.x, e.source.y);
                ctx.lineTo(e.target.x, e.target.y);
                ctx.strokeStyle = (graphSelectedNode && (e.source === graphSelectedNode || e.target === graphSelectedNode))
                    ? '#2563eb' : '#cbd5e1';
                ctx.lineWidth = (graphSelectedNode && (e.source === graphSelectedNode || e.target === graphSelectedNode)) ? 2.5 : 1.2;
                ctx.stroke();
            });

            graphNodes.forEach(n => {
                if (n.hidden) return;

                n.x = Math.max(n.radius, Math.min(width - n.radius, n.x));
                n.y = Math.max(n.radius, Math.min(height - n.radius, n.y));

                if (n === graphSelectedNode) {
                    ctx.beginPath();
                    ctx.arc(n.x, n.y, n.radius + 7, 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(37, 99, 235, 0.25)';
                    ctx.fill();

                    ctx.beginPath();
                    ctx.arc(n.x, n.y, n.radius + 3, 0, Math.PI * 2);
                    ctx.strokeStyle = '#2563eb';
                    ctx.lineWidth = 2;
                    ctx.stroke();
                }

                ctx.beginPath();
                ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
                ctx.fillStyle = n.color;
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.strokeStyle = '#ffffff';
                ctx.stroke();

                ctx.font = '600 11px Inter, sans-serif';
                ctx.fillStyle = '#1e293b';
                ctx.textAlign = 'center';
                ctx.fillText(n.label, n.x, n.y + n.radius + 14);
            });

            graphAnimationId = requestAnimationFrame(stepPhysics);
        }

        stepPhysics();
    }

    function selectGraphNode(node) {
        if (!node) return;
        graphSelectedNode = node;

        let html = `
            <div class="inspector-section mb-3">
                <span class="badge badge-node-type type-${node.type} mb-2">${node.type.toUpperCase()} NODE</span>
                <h6 class="font-bold text-navy mb-1">${escapeHtml(node.label)}</h6>
                <small class="text-slate-500 font-monospace">Node ID: ${escapeHtml(node.id)}</small>
            </div>
        `;

        if (node.type === 'campaign') {
            html += `
                <div class="inspector-meta-box mb-3">
                    <div class="d-flex justify-content-between mb-1">
                        <span class="text-slate-500">Run ID:</span>
                        <strong class="text-navy">#${node.run_id}</strong>
                    </div>
                    <div class="d-flex justify-content-between mb-1">
                        <span class="text-slate-500">Tone:</span>
                        <span class="badge bg-warning text-dark">${escapeHtml(node.tone)}</span>
                    </div>
                    <div class="d-flex justify-content-between">
                        <span class="text-slate-500">Target Platforms:</span>
                        <span class="badge bg-primary">${escapeHtml(node.platforms || 'FB, IG, LI')}</span>
                    </div>
                </div>

                <div class="mb-3">
                    <label class="font-semibold text-slate-700 fs-7 mb-1 d-block">Indexed Vector Document Snippet:</label>
                    <div class="doc-snippet-box">${escapeHtml(node.full_text || '—')}</div>
                </div>
            `;
        } else if (node.type === 'core') {
            html += `
                <div class="inspector-meta-box mb-3">
                    <p class="small text-slate-600 mb-0">Central RAG vector store holding brand embeddings in ChromaDB. Provides semantic retrieval for agent multi-turn generation.</p>
                </div>
            `;
        } else {
            html += `
                <div class="inspector-meta-box mb-3">
                    <p class="small text-slate-600 mb-0">Connected entity node representing shared ${node.type} attribute across campaign memory items.</p>
                </div>
            `;
        }

        const connected = graphEdges
            .filter(e => e.source === node || e.target === node)
            .map(e => e.source === node ? e.target : e.source);

        if (connected.length) {
            html += `
                <div>
                    <label class="font-semibold text-slate-700 fs-7 mb-1 d-block">Connected Nodes (${connected.length}):</label>
                    <div class="d-flex gap-1 flex-wrap">
                        ${connected.map(c => `<span class="connected-node-tag" onclick="selectGraphNodeById('${c.id}')">${escapeHtml(c.label)}</span>`).join('')}
                    </div>
                </div>
            `;
        }

        $('#inspectorContent').html(html);
    }

    window.selectGraphNodeById = function (id) {
        const target = graphNodes.find(n => n.id === id);
        if (target) selectGraphNode(target);
    };
});