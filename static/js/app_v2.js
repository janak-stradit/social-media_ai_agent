$(document).ready(function () {
    let uploadedImagePath = null;
    let threadActiveImagePath = null;
    let lastRunId = null;
    let lastAssistantContext = null;
    let messageCounter = 0;
    window.chatHistory = {}; // Store generations per msgId

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

                // "My Brand Configuration" (-> /brand-profile, the per-user
                // UserBrandProfile edit page) only makes sense for
                // Individual/Small/Medium accounts - Enterprise/admin have
                // no scraped brand profile of their own.
                if (['individual', 'small', 'medium'].includes(user.account_type)) {
                    $('#dropBrandProfileLi').removeClass('d-none');
                } else {
                    $('#dropBrandProfileLi').addClass('d-none');
                }

                // "Brand Configuration" (-> /brand-configuration, StradIT's
                // own global Content Guidelines) is Enterprise/admin only -
                // matches the @enterprise_required_page gate on that route.
                if (user.account_type === 'enterprise' || user.is_admin) {
                    $('#dropBrandConfigLi').removeClass('d-none');
                } else {
                    $('#dropBrandConfigLi').addClass('d-none');
                }
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
                    const formattedTokens = Number(r.total_tokens || 0).toLocaleString() + ' Tokens';
                    const formattedCost = '$' + Number(r.total_cost_usd || 0).toFixed(4);
                    const rem = Number(r.remaining_credits || 0).toFixed(2);
                    const lim = Number(r.credit_limit || 10).toFixed(2);
                    const used = Number(r.used_credits || 0).toFixed(2);

                    $('#headerTokens').text(Number(r.total_tokens || 0).toLocaleString());
                    $('#headerCost').text(formattedCost);
                    $('#dropHeaderTokens').text(formattedTokens);
                    $('#dropHeaderCost').text(formattedCost);

                    $('#headerCreditsRemaining').text('$' + rem);
                    $('#headerCreditLimit').text('$' + lim);
                    $('#dropHeaderCreditsRemaining').text('$' + rem);
                    $('#dropHeaderCreditLimit').text('$' + lim);

                    $('#modalCreditLimit').text('$' + lim);
                    $('#modalUsedCredits').text('$' + used);
                    $('#modalRemainingCredits').text('$' + rem);

                    // Update credit pill badge class
                    const pill = $('#headerCreditPill');
                    if (pill.length) {
                        pill.removeClass('warn danger');
                        if (r.remaining_credits <= 0) {
                            pill.addClass('danger');
                        } else if (r.remaining_credits < 2.0) {
                            pill.addClass('warn');
                        }
                    }

                    // Show the "Admin Management Portal" link (-> /admin,
                    // a standalone page - see templates/admin.html) only for admins.
                    if (r.is_admin) {
                        $('#dropAdminPortalLi').removeClass('d-none');
                    } else {
                        $('#dropAdminPortalLi').addClass('d-none');
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

    $('#dropHeaderModelInfoBtn').on('click', function () {
        openModelArchitectureModal();
    });

    // Admin Management Portal is now a plain link to /admin (see
    // templates/admin.html) - no click handler needed.

    $('#dropRequestCreditBtn').on('click', function () {
        const modalElem = document.getElementById('creditRequestModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
        }
    });

    loadCurrentUser().always(function () {
        renderHistory();
        loadUserUsageMetrics();
        loadBrandProfileQuickPrompts();
        openModalFromHash();
    });

    function openModalFromHash() {
        const hash = window.location.hash;
        if (hash === '#request-credit') {
            $('#dropRequestCreditBtn').trigger('click');
        } else if (hash === '#ai-models') {
            openModelArchitectureModal();
        } else {
            return;
        }
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    // ── Brand-grounded Quick Prompts ─────────────────────────────────────
    // Replaces the welcome screen's generic example prompt cards with ones
    // grounded in the user's own brand (derived from their onboarding
    // website - see services/website_scraper_service.py,
    // agents/website_analysis_agent.py). Falls back to leaving the static
    // example cards already in the template when the user has no brand
    // profile (StradIT's own team, Enterprise, or the scrape failed).
    const QUICK_PROMPT_ICONS = {
        product: { icon: "fa-rocket", cls: "text-primary" },
        thought_leadership: { icon: "fa-lightbulb", cls: "text-warning" },
        story: { icon: "fa-heart", cls: "text-danger" },
        promo: { icon: "fa-fire", cls: "text-danger" },
        event: { icon: "fa-calendar-check", cls: "text-success" },
        tips: { icon: "fa-list-check", cls: "text-info" }
    };

    let _brandProfileIdeaPool = null; // full pool fetched once (up to 8 ideas)
    let _brandProfileCompanyName = null;
    let _lastGreeting = null;
    let _lastShownPrompts = null; // prompts of the currently-displayed cards, to avoid showing the exact same 4 again
    const QUICK_PROMPT_CARD_COUNT = 4;

    function shuffle(arr) {
        const copy = arr.slice();
        for (let i = copy.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [copy[i], copy[j]] = [copy[j], copy[i]];
        }
        return copy;
    }

    // Draws a fresh random subset of QUICK_PROMPT_CARD_COUNT ideas from the
    // full pool - if the pool is bigger than the card count, this keeps
    // retrying until it gets a set that isn't identical to what's already
    // shown, so "regenerate" reliably produces genuinely different cards
    // instead of occasionally reshuffling into the same 4 by chance.
    function pickIdeaSubset(pool) {
        if (pool.length <= QUICK_PROMPT_CARD_COUNT) return shuffle(pool);

        let subset;
        let attempts = 0;
        do {
            subset = shuffle(pool).slice(0, QUICK_PROMPT_CARD_COUNT);
            attempts++;
        } while (
            _lastShownPrompts &&
            attempts < 10 &&
            subset.every(idea => _lastShownPrompts.has(idea.prompt))
        );
        return subset;
    }

    function renderQuickPromptCards(ideas) {
        _lastShownPrompts = new Set(ideas.map(idea => idea.prompt));
        let html = '';
        ideas.forEach(idea => {
            const meta = QUICK_PROMPT_ICONS[idea.category] || QUICK_PROMPT_ICONS.tips;
            html += `
                <button class="quick-prompt-card" data-prompt="${escapeAttr(idea.prompt)}">
                    <div class="quick-prompt-header">
                        <i class="fas ${meta.icon} ${meta.cls}"></i>
                        <span>${escapeHtml(idea.title)}</span>
                    </div>
                    <p class="quick-prompt-text">${escapeHtml(idea.summary || idea.prompt)}</p>
                </button>
            `;
        });
        $('.quick-prompts-grid').html(html);
    }

    function pickGreeting(companyName) {
        const templates = [
            `Ready to create content for ${companyName}?`,
            `What should we post for ${companyName} today?`,
            `Let's grow ${companyName}'s audience - where do we start?`,
            `${companyName}, what's the story this time?`,
            `Your next ${companyName} post starts here.`
        ];
        // Avoid picking the exact same line twice in a row when the user
        // hits regenerate.
        let pick = templates[Math.floor(Math.random() * templates.length)];
        if (templates.length > 1) {
            while (pick === _lastGreeting) {
                pick = templates[Math.floor(Math.random() * templates.length)];
            }
        }
        _lastGreeting = pick;
        return pick;
    }

    // Picks a fresh subset of cards from the already-fetched idea pool
    // (up to 8, from a single onboarding-time analysis) and a new greeting
    // line - no network round-trip needed, so the regenerate button next to
    // the welcome title responds instantly. Pools of 4 or fewer (accounts
    // analyzed before this pool feature existed) just get their order
    // reshuffled - there's nothing genuinely new to draw from until they
    // re-analyze their site from /brand-profile.
    window.regenerateGreeting = function () {
        if (!_brandProfileCompanyName || !_brandProfileIdeaPool) return;
        $('.welcome-title').text(pickGreeting(_brandProfileCompanyName));
        renderQuickPromptCards(pickIdeaSubset(_brandProfileIdeaPool));
    };

    function loadBrandProfileQuickPrompts() {
        $.ajax({
            url: '/api/brand-profile/quick-prompts',
            type: 'GET',
            success: function (r) {
                const ideas = (r && r.post_ideas) || [];
                if (!ideas.length) return;

                _brandProfileIdeaPool = ideas;
                renderQuickPromptCards(pickIdeaSubset(ideas));

                if (r.company_name) {
                    _brandProfileCompanyName = r.company_name;
                    $('.welcome-title').text(pickGreeting(r.company_name));
                    $('.welcome-desc').text('These ideas are grounded in your brand - pick one, or type your own brief below.');
                    $('#regenerateGreetingBtn').removeClass('d-none');
                }
            }
        });
    }

    // ── Auto-resizing Textarea & Char Counter ──────────────────────────
    const storyInput = $('#storyInput');
    storyInput.on('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 600) + 'px';
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

    // ── Incoming handoff from the Analysis Dashboard's "Refine in Studio
    // Chat" button (see sendPipelineToStudioChat in dashboard.js). Studio
    // Chat is a separate page, so the seed content is passed via localStorage
    // and consumed exactly once here on load. ──────────────────────────────
    (function hydrateIncomingStudioChatSeed() {
        let seed = null;
        try {
            const raw = localStorage.getItem('incomingStudioChatSeed');
            if (raw) seed = JSON.parse(raw);
        } catch (e) { /* ignore malformed/inaccessible storage */ }

        if (!seed || !seed.text) return;

        try {
            localStorage.removeItem('incomingStudioChatSeed');
        } catch (e) { /* ignore storage errors */ }

        startNewChat();
        storyInput.val(seed.text).trigger('input').focus();

        // If the dashboard run generated an image, attach it as the active
        // reference image (it's already on the server, no re-upload needed)
        // so it shows up as a real image in the chat, not just a URL in text.
        if (seed.imagePath) {
            uploadedImagePath = seed.imagePath;
            threadActiveImagePath = seed.imagePath;
            $('#previewImg').attr('src', '/' + seed.imagePath.replace(/^\//, ''));
            $('#imagePreview').removeClass('d-none');
            setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>From Dashboard');
        }

        const extraCount = (seed.imageUrls && seed.imageUrls.length > 1) ? seed.imageUrls.length - 1 : 0;
        showToast(
            extraCount
                ? `Loaded content from the Analysis Dashboard (+${extraCount} more variation${extraCount > 1 ? 's' : ''} referenced below) - review and send to start refining.`
                : 'Loaded content from the Analysis Dashboard - review and send to start refining.',
            'info'
        );
    })();

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
        const story = $('#storyInput').val().trim();
        const targetCompany = $('#targetCompanySelect').val() || 'None';

        if (!story && targetCompany === 'None') {
            showToast('Please enter your brief text or select a company first!', 'warning');
            return;
        }

        showToast('Contacting Scraper Explorer API for ' + targetCompany + '...', 'info');
        const btn = $('#analyzeBtn');
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i><span class="d-none d-md-inline ms-1">Fetching Data...</span>');

        $.ajax({
            url: '/api/analyze-story',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: story, target_company: targetCompany }),
            success: function (r) {
                if (r.suggested_brief) {
                    storyInput.val(r.suggested_brief).trigger('input');
                    showToast('Populated brief with live company data!', 'info');
                }
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

    // Prevents starting a second overlapping generation while the
    // Multi-Agent Execution Pipeline is already running - the textarea and
    // every dock control become inert (opacity + pointer-events: none via
    // .dock-disabled) for the duration of the request.
    function setChatDockDisabled(disabled) {
        $('#dropZone').toggleClass('dock-disabled', disabled);
        $('#storyInput, #generateBtn, #analyzeBtn, #attachBtn').prop('disabled', disabled);
    }

    function executeGeneration(msgId, requestBody, assistantElem, platforms, activeImgPath, mediaType, selectedOutputs) {
        setChatDockDisabled(true);
        const hasImage = !!activeImgPath;
        const stepIds = hasImage
            ? ['step_story', 'step_vision', 'step_caption', 'step_hashtag', 'step_strategy', 'step_reviewer', 'step_guardrail']
            : ['step_story', 'step_caption', 'step_hashtag', 'step_strategy', 'step_reviewer', 'step_guardrail'];
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

        window.currentGenerationRequest = $.ajax({
            url: '/api/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(requestBody),
            success: function (r) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                clearInterval(iv);
                lastRunId = r.run_id || null;

                // Cache summary context for multi-turn edits
                let contextSummary = `Brief: ${requestBody.story}\nGenerated Captions:\n`;
                platforms.forEach(p => {
                    if (r.content[p]?.caption?.primary_caption) {
                        contextSummary += `[${p.toUpperCase()}]: ${r.content[p].caption.primary_caption}\n`;
                    }
                });
                lastAssistantContext = contextSummary;

                if (!window.chatHistory[msgId]) {
                    window.chatHistory[msgId] = { responses: [], currentIndex: 0, requestBody, platforms, activeImgPath, mediaType, selectedOutputs };
                }

                const rData = { content: r.content, runId: lastRunId, usage: r.usage, agentsExecuted: r.agents_executed, qualitySummary: r.quality_summary };
                window.chatHistory[msgId].responses.push(rData);
                window.chatHistory[msgId].currentIndex = window.chatHistory[msgId].responses.length - 1;

                // Render finished Assistant Response inside Assistant Card
                renderAssistantResponse(msgId);
                renderHistory();
                loadUserUsageMetrics();
                scrollToBottom();
            },
            error: function (xhr, status, error) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                clearInterval(iv);
                
                if (status === 'abort') {
                    assistantElem.remove();
                    showToast('Generation cancelled', 'info');
                    return;
                }

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
    }

    window.cancelGeneration = function(msgId) {
        if (window.currentGenerationRequest) {
            window.currentGenerationRequest.abort();
            window.currentGenerationRequest = null;
        }
    };

    // ── Generate Content (Main Chat Flow) ──────────────────────────────
    window.generateContent = function () {
        const story = storyInput.val().trim();
        const targetCompany = $('#targetCompanySelect').val() || 'None';

        if (!story && targetCompany === 'None') {
            showToast('Please enter a campaign brief or select a company!', 'warning');
            return;
        }

        const platforms = [];
        $('.platform-chips-inline input[type="checkbox"]:checked').each(function () {
            platforms.push($(this).val());
        });

        const tone = $('#toneSelect').val();
        const brandVoice = $('#brandVoiceSelect').val() || 'Standard Enterprise';
        const selectedOutputs = Array.from(document.querySelectorAll('input[name="outputOptions"]:checked')).map(el => el.value);
        const mediaType = selectedOutputs.join(', ') || 'none';
        const hasImage = !!(uploadedImagePath || threadActiveImagePath);
        const activeImgPath = uploadedImagePath || threadActiveImagePath;

        // Hide welcome hero on first message
        $('#welcomeHero').addClass('d-none');

        // Create unique message IDs
        messageCounter++;
        const msgId = 'msg_' + Date.now() + '_' + messageCounter;

        // 1. Append User Chat Message Bubble
        appendUserMessage(story, uploadedImagePath, platforms, tone, mediaType, brandVoice);

        // Clear input area
        storyInput.val('').trigger('input');
        clearAttachment();
        scrollToBottom();

        // No platform and/or no output type picked yet - don't block with a
        // validation error. Research the brief first and let the user decide
        // the format afterward (see runResearchThenAsk).
        if (!platforms.length || !selectedOutputs.length) {
            runResearchThenAsk(msgId, story, targetCompany, activeImgPath, tone, brandVoice);
            return;
        }

        // 2. Append Assistant Thinking Message Bubble with Multi-Agent Stepper
        const assistantElem = appendAssistantThinking(msgId, hasImage);
        scrollToBottom();

        const requestBody = {
            story: story,
            image_path: activeImgPath,
            platforms: platforms,
            tone: tone,
            brand_voice: brandVoice,
            include_strategy: true,
            previous_context: lastAssistantContext,
            selected_outputs: selectedOutputs,
            target_company: targetCompany
        };

        executeGeneration(msgId, requestBody, assistantElem, platforms, activeImgPath, mediaType, selectedOutputs);
    };

    // ── Chat Bubble Render Helpers ─────────────────────────────────────

    function appendUserMessage(text, imagePath, platforms, tone, mediaType, brandVoice) {
        const platformBadges = platforms.map(p => {
            const icons = { facebook: 'fab fa-facebook color-fb', instagram: 'fab fa-instagram color-ig', linkedin: 'fab fa-linkedin color-li', youtube: 'fab fa-youtube color-yt' };
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

    // Renders a finished image/video card - shared by triggerMediaGenInChat
    // (a generation that just completed live) and renderAssistantResponse
    // (a run reloaded from history that already has saved media, see
    // db.append_run_media / content[platform].media.{image,video}) so both
    // paths produce the identical card instead of two hand-maintained copies.
    function buildGeneratedMediaHtml(mediaType, media) {
        if (mediaType === 'video') {
            return `
                <div class="media-output-card">
                    <div class="media-output-header">
                        <span><i class="fas fa-video text-purple me-2"></i>Generated Video (${media.resolution || 'MP4'})</span>
                        <button type="button" class="btn-copy-sm" onclick="downloadAsZip('${media.url}', this)"><i class="fas fa-file-zipper me-1"></i>Download</button>
                    </div>
                    <div class="media-output-body">
                        <video src="${media.url}" controls class="media-output-video" loop></video>
                    </div>
                </div>
            `;
        }
        return `
            <div class="media-output-card">
                <div class="media-output-header">
                    <span><i class="fas fa-image text-primary me-2"></i>Generated Image (${media.resolution || '1024x1024'})</span>
                    <button type="button" class="btn-copy-sm" onclick="downloadAsZip('${media.url}', this)"><i class="fas fa-file-zipper me-1"></i>Download</button>
                </div>
                <div class="media-output-body">
                    <img src="${media.url}" class="media-output-img" alt="Generated media">
                </div>
            </div>
        `;
    }

    function assistantAvatarSvg() {
        return `
            <svg viewBox="0 0 24 24" width="19" height="19" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 2.5a9.5 9.5 0 1 0 9.5 9.5" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
                <path d="M12 6.5a5.5 5.5 0 1 0 5.5 5.5" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity="0.7"/>
                <circle cx="12" cy="12" r="1.7" fill="#fff"/>
            </svg>
        `;
    }

    // Builds just the .assistant-card inner HTML for the full generation
    // pipeline stepper - factored out so it can be used both for a brand new
    // chat message (appendAssistantThinking) and to replace an existing
    // research-only card in place once the user picks platform/output there
    // (see runResearchThenAsk / its "Generate Content" button handler).
    function buildPipelineCardHtml(msgId, hasImage) {
        const visionStepHtml = hasImage ? `
            <div class="agent-step-item" id="${msgId}_step_vision">
                <div class="agent-step-icon"><i class="fas fa-circle-notch fa-spin text-muted"></i></div>
                <span class="agent-step-name"><i class="fas fa-eye me-1 text-teal"></i>Vision Agent</span>
                <span class="agent-step-desc">Analyzing visual asset, colors & context</span>
            </div>
        ` : '';

        return `
            <div class="assistant-header">
                <div class="assistant-title">
                    <i class="fas fa-network-wired text-primary me-1"></i>Multi-Agent Execution Pipeline
                </div>
                <div class="d-flex align-items-center">
                    <span class="assistant-run-tag me-2">Active Agents</span>
                    <button class="btn btn-sm btn-outline-danger py-0 px-2 cancel-generation-btn" onclick="cancelGeneration('${msgId}')" title="Cancel generation"><i class="fas fa-times me-1"></i>Cancel</button>
                </div>
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

                <div class="agent-step-item" id="${msgId}_step_guardrail">
                    <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                    <span class="agent-step-name"><i class="fas fa-user-shield me-1 text-indigo"></i>Brand Guardrail</span>
                    <span class="agent-step-desc">Verifying the brand-voice persona was never used as the company name</span>
                </div>
            </div>
        `;
    }

    function appendAssistantThinking(msgId, hasImage) {
        const html = `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar">${assistantAvatarSvg()}</div>
                <div class="assistant-card">${buildPipelineCardHtml(msgId, hasImage)}</div>
            </div>
        `;
        const elem = $(html);
        $('#chatThread').append(elem);
        return elem;
    }

    // ── Research-first flow ──────────────────────────────────────────────
    // When the user sends a brief without picking a platform/output, don't
    // block them with a validation error - research the topic via the Story
    // & Research Agent (see agents/story_agent.py) and show that first, then
    // let them pick platform(s)/output(s) inline to actually generate a post
    // from the same brief, instead of forcing that choice upfront.
    function appendResearchThinking(msgId) {
        const html = `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar">${assistantAvatarSvg()}</div>
                <div class="assistant-card">
                    <div class="assistant-header">
                        <div class="assistant-title"><i class="fas fa-magnifying-glass"></i>Researching Your Topic</div>
                    </div>
                    <div class="agent-stepper">
                        <div class="agent-stepper-title"><i class="fas fa-cogs me-1"></i>Autonomous Agent Working:</div>
                        <div class="agent-step-item active">
                            <div class="agent-step-icon"><div class="spinner-border spinner-border-sm text-primary" role="status"></div></div>
                            <span class="agent-step-name"><i class="fas fa-brain me-1 text-purple"></i>Story &amp; Research Agent</span>
                            <span class="agent-step-desc">Researching the topic &amp; retrieving relevant brand memory</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
        const elem = $(html);
        $('#chatThread').append(elem);
        return elem;
    }

    function runResearchThenAsk(msgId, story, targetCompany, activeImgPath, tone, brandVoice) {
        const assistantElem = appendResearchThinking(msgId);

        $.ajax({
            url: '/api/analyze-story',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: story, target_company: targetCompany }),
            success: function (r) {
                renderResearchResult(msgId, assistantElem, r, story, targetCompany, activeImgPath, tone, brandVoice);
                scrollToBottom();
            },
            error: function (xhr) {
                const errText = xhr.responseJSON?.error || 'Research failed';
                assistantElem.find('.assistant-card').html(`
                    <div class="alert alert-danger mb-0">
                        <i class="fas fa-exclamation-triangle me-2"></i><strong>Error:</strong> ${escapeHtml(errText)}
                    </div>
                `);
                showToast('Research failed: ' + errText, 'error');
            }
        });
    }

    function renderResearchResult(msgId, assistantElem, r, story, targetCompany, activeImgPath, tone, brandVoice) {
        const analysis = r.analysis || {};
        const themes = Array.isArray(analysis.themes) ? analysis.themes : [];
        const researchNotes = Array.isArray(analysis.research_notes) ? analysis.research_notes : [];
        const hooks = Array.isArray(analysis.hooks) ? analysis.hooks : [];

        const themesHtml = themes.length
            ? `<div class="research-section">
                    <span class="research-section-label">Key Themes</span>
                    <div class="research-theme-pills">${themes.map(t => `<span class="research-theme-pill">${escapeHtml(t)}</span>`).join('')}</div>
                </div>`
            : '';
        const notesHtml = `<div class="research-section">
                <span class="research-section-label">Research Notes</span>
                ${researchNotes.length
                    ? `<ul class="research-notes-list">${researchNotes.map(n => `<li>${escapeHtml(n)}</li>`).join('')}</ul>`
                    : '<p class="text-muted small mb-0">No additional research notes for this brief.</p>'}
            </div>`;
        const hooksHtml = hooks.length
            ? `<div class="research-section">
                    <span class="research-section-label">Possible Angles</span>
                    <ul class="research-notes-list research-hooks-list">${hooks.map(h => `<li>${escapeHtml(h)}</li>`).join('')}</ul>
                </div>`
            : '';

        const cardContent = `
            <div class="assistant-header">
                <div class="assistant-title"><i class="fas fa-magnifying-glass"></i>Research Summary</div>
            </div>
            <div class="research-result-block">
                ${themesHtml}
                ${notesHtml}
                ${hooksHtml}
            </div>
            <div class="research-cta-block">
                <div class="research-cta-title"><i class="fas fa-wand-magic-sparkles me-1"></i>Want to turn this into a post?</div>
                <div class="research-cta-row">
                    <span class="config-label">Platforms:</span>
                    <div class="platform-chips-inline" id="${msgId}_researchPlatforms">
                        <label class="platform-chip-sm"><input type="checkbox" value="facebook"><span class="chip-content"><i class="fab fa-facebook me-1 color-fb"></i>FB</span></label>
                        <label class="platform-chip-sm"><input type="checkbox" value="instagram"><span class="chip-content"><i class="fab fa-instagram me-1 color-ig"></i>IG</span></label>
                        <label class="platform-chip-sm"><input type="checkbox" value="linkedin" checked><span class="chip-content"><i class="fab fa-linkedin me-1 color-li"></i>LinkedIn</span></label>
                        <label class="platform-chip-sm"><input type="checkbox" value="youtube"><span class="chip-content"><i class="fab fa-youtube me-1 color-yt"></i>YouTube</span></label>
                    </div>
                </div>
                <div class="research-cta-row">
                    <span class="config-label">Output:</span>
                    <div class="media-chips-inline" id="${msgId}_researchOutputs">
                        <label class="media-chip-sm"><input type="checkbox" value="text" checked><span class="chip-content">Text (Caption)</span></label>
                        <label class="media-chip-sm"><input type="checkbox" value="image"><span class="chip-content"><i class="fas fa-image me-1 text-primary"></i>Image</span></label>
                        <label class="media-chip-sm"><input type="checkbox" value="video"><span class="chip-content"><i class="fas fa-video me-1 text-purple"></i>Video</span></label>
                    </div>
                </div>
                <button type="button" class="btn btn-primary btn-sm mt-2 research-generate-btn" id="${msgId}_researchGenerateBtn">
                    <i class="fas fa-bolt me-1"></i>Generate Content
                </button>
            </div>
        `;
        assistantElem.find('.assistant-card').html(cardContent);

        $(`#${msgId}_researchGenerateBtn`).on('click', function () {
            const platforms = [];
            $(`#${msgId}_researchPlatforms input:checked`).each(function () { platforms.push($(this).val()); });
            const selectedOutputs = [];
            $(`#${msgId}_researchOutputs input:checked`).each(function () { selectedOutputs.push($(this).val()); });

            if (!platforms.length) {
                showToast('Select at least one platform to generate content!', 'warning');
                return;
            }
            if (!selectedOutputs.length) {
                showToast('Select at least one output type (text/image/video)!', 'warning');
                return;
            }

            const hasImage = !!activeImgPath;
            assistantElem.find('.assistant-card').html(buildPipelineCardHtml(msgId, hasImage));

            const requestBody = {
                story: story,
                image_path: activeImgPath,
                platforms: platforms,
                tone: tone,
                brand_voice: brandVoice,
                include_strategy: true,
                previous_context: lastAssistantContext,
                selected_outputs: selectedOutputs,
                target_company: targetCompany,
                precomputed_analysis: analysis
            };

            executeGeneration(msgId, requestBody, assistantElem, platforms, activeImgPath, selectedOutputs.join(', '), selectedOutputs);
        });
    }

    function renderAssistantResponse(msgId) {
        const historyObj = window.chatHistory[msgId];
        const rData = historyObj.responses[historyObj.currentIndex];
        const { requestBody, platforms, selectedOutputs } = historyObj;
        const { content, runId, usage, agentsExecuted, qualitySummary } = rData;
        const elem = $(`#${msgId}`);
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

        // Build Agents Breakdown Panel - visible by default (was hidden
        // behind the "N Agents Active" toggle, so it never actually showed
        // unless the user happened to click it); the button still lets you
        // collapse it if you want.
        let agentBreakdownHtml = `<div class="agent-pipeline-breakdown" id="${msgId}_pipeline_panel">`;
        agentBreakdownHtml += `<div class="fw-bold mb-1 text-primary">Agents Engaged in this Turn:</div>`;
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
            const icons = { facebook: 'fab fa-facebook color-fb', instagram: 'fab fa-instagram color-ig', linkedin: 'fab fa-linkedin color-li', youtube: 'fab fa-youtube color-yt' };
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
            const isRefined = pData.caption?.refined_by_critic || pData.quality?.self_corrected;

            // Hashtags - a single curated set (see agents/hashtag_agent.py);
            // tolerate the old reach_hashtags key too in case a cached/older
            // run's content is being re-rendered from history.
            const rawTags = pData.hashtags || {};
            const tagList = Array.isArray(rawTags.hashtags) ? rawTags.hashtags
                : Array.isArray(rawTags) ? rawTags
                : Array.isArray(rawTags.reach_hashtags) ? rawTags.reach_hashtags
                : [];
            const tagsHtml = tagList.map(t => `<span class="hashtag-pill">${escapeHtml(t)}</span>`).join(' ') || '<em>No hashtags</em>';
            const tagsStr = tagList.join(' ');

            const cardId = `${msgId}_caption_target_${p}`;
            const tagsCardId = `${msgId}_hashtags_target_${p}`;

            panelsHtml += `
                <div class="platform-panel-item" id="${msgId}_tab_${p}" style="display: ${displayStyle}">

                    <!-- Post Caption Card -->
                    <div class="post-box-card">
                        <div class="post-box-header">
                            <span class="post-box-title">
                                <i class="fas fa-pen-nib me-1"></i>Post Caption
                                ${isRefined ? '<span class="badge bg-success ms-2"><i class="fas fa-shield-check me-1"></i>Critic Refined</span>' : ''}
                            </span>
                            <button class="btn-copy-sm btn-copy-text" id="${cardId}_copy" data-text="${escapeAttr(primaryCap)}">
                                <i class="fas fa-copy me-1"></i>Copy
                            </button>
                        </div>

                        <div class="post-caption-text" id="${cardId}">${escapeHtml(primaryCap)}</div>
                    </div>

                    <!-- Hashtags Card -->
                    <div class="post-box-card">
                        <div class="post-box-header">
                            <span class="post-box-title"><i class="fas fa-hashtag me-1"></i>Curated Hashtags</span>
                            <button class="btn-copy-sm btn-copy-text" id="${tagsCardId}_copy" data-text="${escapeAttr(tagsStr)}">
                                <i class="fas fa-copy me-1"></i>Copy Tags
                            </button>
                        </div>

                        <div class="hashtags-container" id="${tagsCardId}">${tagsHtml}</div>
                    </div>

                    <!-- Media Output - a run reloaded from history already has
                         its generated media saved (see db.append_run_media,
                         content[platform].media.{image,video}) - show that
                         directly instead of a "Generating..." placeholder,
                         which was only ever meant for a generation actually
                         in progress right now. Only fall back to the
                         placeholder (and let the live-trigger block below
                         kick off a real generation) when there's genuinely
                         no saved media yet for a type that was requested. -->
                    <div id="${msgId}_media_${p}" class="media-container-slot">
                        ${pData.media?.image?.url ? buildGeneratedMediaHtml('image', pData.media.image)
                            : (selectedOutputs || []).includes('image') ? `
                        <div class="media-output-card mb-2" id="${msgId}_media_image_${p}">
                            <div class="media-output-header">
                                <span><i class="fas fa-spinner fa-spin me-2 text-primary"></i>Generating AI IMAGE...</span>
                            </div>
                            <div class="media-output-body text-center p-4">
                                <div class="spinner-border text-primary mb-2" role="status"></div>
                                <p class="text-muted small mb-0">Multi-agent media pipeline is processing image generation</p>
                            </div>
                        </div>
                        ` : ''}
                        ${pData.media?.video?.url ? buildGeneratedMediaHtml('video', pData.media.video)
                            : (selectedOutputs || []).includes('video') ? `
                        <div class="media-output-card" id="${msgId}_media_video_${p}">
                            <div class="media-output-header">
                                <span><i class="fas fa-spinner fa-spin me-2 text-purple"></i>Generating AI VIDEO...</span>
                            </div>
                            <div class="media-output-body text-center p-4">
                                <div class="spinner-border text-purple mb-2" role="status"></div>
                                <p class="text-muted small mb-0">Multi-agent media pipeline is processing video generation</p>
                            </div>
                        </div>
                        ` : ''}
                    </div>

                </div>
            `;
        });
        panelsHtml += `</div>`;

        const totalGens = historyObj.responses.length;
        const currentGen = historyObj.currentIndex + 1;
        const paginationHtml = totalGens > 1 ? `
            <div class="generation-pagination ms-2 d-inline-flex align-items-center bg-light rounded px-2 py-1 border">
                <i class="fas fa-chevron-left cursor-pointer text-secondary me-2 gen-prev" data-msg="${msgId}" ${currentGen === 1 ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
                <span class="small font-weight-bold">${currentGen} / ${totalGens}</span>
                <i class="fas fa-chevron-right cursor-pointer text-secondary ms-2 gen-next" data-msg="${msgId}" ${currentGen === totalGens ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
            </div>
        ` : '';

        const cardContent = `
            <div class="assistant-header">
                <div class="assistant-title d-flex align-items-center">
                    <div><i class="fas fa-sparkles text-primary me-1"></i>VortexSocial Studio Output</div>
                    ${paginationHtml}
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
            <div class="assistant-card-footer">
                <button class="btn btn-sm btn-outline-success btn-schedule-post" data-msg="${msgId}">
                    <i class="fas fa-calendar-plus me-1"></i>Schedule
                </button>
                <button class="btn btn-sm btn-outline-primary btn-regenerate" data-msg="${msgId}">
                    <i class="fas fa-sync-alt me-1"></i>Regenerate
                </button>
            </div>
        `;

        elem.find('.assistant-card').html(cardContent);

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

        // Bind Pagination
        elem.find('.gen-prev').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (h && h.currentIndex > 0) {
                h.currentIndex--;
                renderAssistantResponse(mId);
            }
        });

        elem.find('.gen-next').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (h && h.currentIndex < h.responses.length - 1) {
                h.currentIndex++;
                renderAssistantResponse(mId);
            }
        });

        // Bind Regenerate
        elem.find('.btn-regenerate').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (h) {
                const assistantElem = $(`#${mId}`);
                const hasImage = !!h.activeImgPath;
                assistantElem.replaceWith(appendAssistantThinking(mId, hasImage));
                executeGeneration(mId, h.requestBody, $(`#${mId}`), h.platforms, h.activeImgPath, h.mediaType, h.selectedOutputs);
            }
        });

        // Bind Schedule - opens the same schedulePostModal the History detail
        // modal uses (see $('#modalScheduleBtn')/$('#confirmSchedulePostBtn')
        // below), just pointed at this chat message's run/story instead of
        // whichever run the History modal last had open. Also always
        // pre-fills the date/time field with a sensible default (tomorrow),
        // since the field only shows a value once something sets it - opening
        // the modal any other way left it blank.
        elem.find('.btn-schedule-post').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (!h) return;
            const rDataForSchedule = h.responses[h.currentIndex];

            lastRunId = rDataForSchedule.runId;
            $('#modalStory').text(h.requestBody.story || 'Campaign Post');

            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            $('#schedDateTimeInput').val(tomorrow.toISOString().slice(0, 16));

            const schedModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('schedulePostModal'));
            schedModal.show();
        });

        // Trigger Media Generation asynchronously if mediaType requested -
        // but only for a (platform, type) pair that doesn't already have
        // saved media. A run reloaded from history now correctly lists its
        // already-generated types in selectedOutputs (see
        // loadHistoryIntoChat) so its existing image/video renders via
        // buildGeneratedMediaHtml above - without this check, viewing it
        // would immediately kick off a brand new, wasteful (and credit-
        // consuming) generation for content that already exists.
        if (selectedOutputs && selectedOutputs.length > 0) {
            platforms.forEach(p => {
                const pData = content[p] || {};
                const pCaption = pData.caption?.primary_caption || requestBody.story;
                // media_prompt (see api/routes.py) folds the Research Summary's
                // imagery descriptions & research notes in alongside the caption,
                // so image/video generation actually reflects the research shown
                // to the user - not just the short social caption text.
                const pMediaPrompt = pData.media_prompt || pCaption;
                if (selectedOutputs.includes('image') && !pData.media?.image?.url) {
                    triggerMediaGenInChat(p, pCaption, 'image', requestBody.tone, runId, historyObj.activeImgPath, `${msgId}_media_image_${p}`, pMediaPrompt);
                }
                if (selectedOutputs.includes('video') && !pData.media?.video?.url) {
                    triggerMediaGenInChat(p, pCaption, 'video', requestBody.tone, runId, historyObj.activeImgPath, `${msgId}_media_video_${p}`, pMediaPrompt);
                }
            });
        }
    }

    // Downloads one or more generated assets bundled into a single .zip via
    // the shared /api/download-zip endpoint (same one the Analysis Dashboard's
    // pipeline modal ZIP download uses), instead of downloading each file
    // individually.
    window.downloadAsZip = function (urls, btnEl) {
        const list = (Array.isArray(urls) ? urls : [urls]).filter(Boolean);
        if (!list.length) {
            showToast('Nothing to download yet.', 'warning');
            return;
        }
        const $btn = btnEl ? $(btnEl) : null;
        const originalHtml = $btn ? $btn.html() : null;
        if ($btn) $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Zipping...');

        fetch('/api/download-zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls: list })
        })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
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
                a.remove();
                window.URL.revokeObjectURL(url);
            })
            .catch(() => {
                showToast('Failed to download ZIP file. Please try again.', 'error');
            })
            .finally(() => {
                if ($btn) $btn.prop('disabled', false).html(originalHtml);
            });
    };

    function triggerMediaGenInChat(platform, caption, mediaType, tone, runId, imagePath, targetSlotId, mediaPrompt) {
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
                image_path: imagePath,
                image_prompt: mediaType === 'image' ? mediaPrompt : undefined,
                video_prompt: mediaType === 'video' ? mediaPrompt : undefined
            }),
            success: function (res) {
                const slot = $('#' + targetSlotId);
                if (res.success && res.url) {
                    const mediaHtml = mediaType === 'video' ? `
                        <div class="media-output-card">
                            <div class="media-output-header">
                                <span><i class="fas fa-video text-purple me-2"></i>Generated Video (${res.resolution || 'MP4'})</span>
                                <button type="button" class="btn-copy-sm" onclick="downloadAsZip('${res.url}', this)"><i class="fas fa-file-zipper me-1"></i>Download</button>
                            </div>
                            <div class="media-output-body">
                                <video src="${res.url}?v=${Date.now()}" controls class="media-output-video" autoplay loop></video>
                            </div>
                        </div>
                    ` : `
                        <div class="media-output-card">
                            <div class="media-output-header">
                                <span><i class="fas fa-image text-primary me-2"></i>Generated Image (${res.resolution || '1024x1024'})</span>
                                <button type="button" class="btn-copy-sm" onclick="downloadAsZip('${res.url}', this)"><i class="fas fa-file-zipper me-1"></i>Download</button>
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
                showToast('Could not load your history right now.', 'error');
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
                            <button class="btn-history-icon btn-view-details-item" data-id="${item.id}" title="View run details"><i class="fas fa-circle-info"></i></button>
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
            loadHistoryIntoChat(id);
        });

        $('.btn-view-details-item').on('click', function (e) {
            e.stopPropagation();
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

    // Loads a past run into the live chat workspace as a resumable
    // conversation (instead of the read-only Run Details modal), so the user
    // can send a follow-up message to fine-tune it with more information.
    function loadHistoryIntoChat(runId) {
        $.ajax({
            url: `/api/history/${runId}`,
            type: 'GET',
            success: function (r) {
                const run = r.run;
                if (!run) return;

                const platforms = Array.isArray(run.platforms) ? run.platforms : (run.platforms ? [run.platforms] : ['linkedin']);
                const content = run.content || {};

                startNewChat();
                $('#welcomeHero').addClass('d-none');

                messageCounter++;
                const msgId = 'msg_' + Date.now() + '_' + messageCounter;

                appendUserMessage(run.story, null, platforms, run.tone, 'Text (Caption)', 'Standard Enterprise');
                appendAssistantThinking(msgId, false);

                // Reflect what was actually generated for this run (see
                // db.append_run_media / content[platform].media.{image,video})
                // - this used to always be [], which meant a run that DID
                // include a generated image never showed it when reloaded
                // from history (the media card only ever renders when its
                // type is listed here).
                const savedOutputs = [];
                platforms.forEach(p => {
                    const media = content[p]?.media || {};
                    if (media.image?.url && !savedOutputs.includes('image')) savedOutputs.push('image');
                    if (media.video?.url && !savedOutputs.includes('video')) savedOutputs.push('video');
                });

                window.chatHistory[msgId] = {
                    responses: [{ content: content, runId: run.id, usage: null, agentsExecuted: null, qualitySummary: null }],
                    currentIndex: 0,
                    requestBody: { story: run.story, platforms: platforms, tone: run.tone, brand_voice: 'Standard Enterprise' },
                    platforms: platforms,
                    activeImgPath: null,
                    mediaType: 'none',
                    selectedOutputs: savedOutputs
                };
                renderAssistantResponse(msgId);

                // Seed multi-turn context the same way a live generation does,
                // so the next message the user sends continues refining this
                // run instead of starting from scratch.
                lastRunId = run.id;
                let contextSummary = `Brief: ${run.story}\nGenerated Captions:\n`;
                platforms.forEach(p => {
                    if (content[p]?.caption?.primary_caption) {
                        contextSummary += `[${p.toUpperCase()}]: ${content[p].caption.primary_caption}\n`;
                    }
                });
                lastAssistantContext = contextSummary;

                scrollToBottom();
                showToast('Loaded past conversation - send a message to keep refining it.', 'info');
            },
            error: function () {
                showToast('Could not load that conversation.', 'error');
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
                    const rawH = pData.hashtags;
                    const hTags = Array.isArray(rawH) ? rawH : (Array.isArray(rawH?.hashtags) ? rawH.hashtags : (Array.isArray(rawH?.primary_hashtags) ? rawH.primary_hashtags : []));
                    detailsHtml += `
                        <div class="col-md-6 col-lg-4">
                            <div class="ent-card h-100 p-3">
                                <h6 class="text-primary text-uppercase font-weight-bold mb-2">${platform}</h6>
                                <p class="small mb-2"><strong>Caption:</strong> ${escapeHtml(pData.caption?.primary_caption || 'N/A')}</p>
                                <p class="small text-muted mb-0"><strong>Tags:</strong> ${escapeHtml(hTags.join(' '))}</p>
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

    // ── RAG Memory Knowledge Graph Visualizer ─────────────────────────────
    let graphAnimationId = null;
    let graphNodes = [];
    let graphEdges = [];
    let graphSelectedNode = null;
    let graphDraggedNode = null;

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

    // ── Social Media Accounts & Scheduling Handlers ───────────────────────
    let currentSocialAccounts = [];

    $('#headerSocialSettingsBtn').on('click', function () {
        openSocialSettingsModal();
    });

    function openSocialSettingsModal() {
        const modalElem = document.getElementById('socialSettingsModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
            loadUserSocialAccounts();
            loadScheduledPosts();
        }
    }

    function loadUserSocialAccounts() {
        $.ajax({
            url: '/api/social/accounts',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                currentSocialAccounts = r.accounts || [];

                // Reset UI cards
                $('#fbStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#igStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');
                $('#liStatusBadge').removeClass('badge-connected').addClass('badge-disconnected').html('<i class="fas fa-circle-xmark me-1"></i>Not Connected');

                $('#fbAccountName').text('—'); $('#fbAccountId').text('—');
                $('#igAccountName').text('—'); $('#igAccountId').text('—');
                $('#liAccountName').text('—'); $('#liAccountId').text('—');

                currentSocialAccounts.forEach(acc => {
                    if (acc.status === 'connected') {
                        if (acc.platform === 'facebook') {
                            $('#fbStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#fbAccountName').text(acc.account_name || 'Facebook Page');
                            $('#fbAccountId').text(acc.account_id || 'N/A');
                        } else if (acc.platform === 'instagram') {
                            $('#igStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#igAccountName').text(acc.account_name || 'Instagram Account');
                            $('#igAccountId').text(acc.account_id || 'N/A');
                        } else if (acc.platform === 'linkedin') {
                            $('#liStatusBadge').removeClass('badge-disconnected').addClass('badge-connected').html('<i class="fas fa-circle-check me-1"></i>Connected');
                            $('#liAccountName').text(acc.account_name || 'LinkedIn Account');
                            $('#liAccountId').text(acc.account_id || 'N/A');
                        }
                    }
                });
            }
        });
    }

    window.openConnectModal = function (platform) {
        const titles = {
            facebook: 'Connect Facebook Page',
            instagram: 'Connect Instagram Business',
            linkedin: 'Connect LinkedIn Account',
            youtube: 'Connect YouTube Account'
        };
        const icons = {
            facebook: '<i class="fab fa-facebook color-fb"></i>',
            instagram: '<i class="fab fa-instagram color-ig"></i>',
            linkedin: '<i class="fab fa-linkedin color-li"></i>',
            youtube: '<i class="fab fa-youtube color-yt"></i>'
        };

        $('#connectPlatformInput').val(platform);
        $('#connectModalTitle').text(titles[platform] || 'Connect Account');
        $('#connectModalIcon').html(icons[platform] || '<i class="fas fa-plug"></i>');

        const existing = currentSocialAccounts.find(a => a.platform === platform) || {};
        $('#connectAccountNameInput').val(existing.account_name || '');
        $('#connectAccountIdInput').val(existing.account_id || '');
        $('#connectAccessTokenInput').val(existing.access_token || '');

        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('connectAccountModal'));
        modal.show();
    };

    $('#saveConnectAccountBtn').on('click', function () {
        const platform = $('#connectPlatformInput').val();
        const accountName = $('#connectAccountNameInput').val().trim();
        const accountId = $('#connectAccountIdInput').val().trim();
        const accessToken = $('#connectAccessTokenInput').val().trim();

        if (!accountName) {
            showToast('Please enter an Account Name or Handle', 'error');
            return;
        }

        $.ajax({
            url: '/api/social/accounts',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platform: platform,
                account_name: accountName,
                account_id: accountId,
                access_token: accessToken
            }),
            success: function (r) {
                if (r.success) {
                    showToast(`${platform.toUpperCase()} account connected successfully!`, 'success');
                    bootstrap.Modal.getInstance(document.getElementById('connectAccountModal')).hide();
                    loadUserSocialAccounts();
                }
            },
            error: function (xhr) {
                showToast('Failed to connect: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    });

    function loadScheduledPosts() {
        $.ajax({
            url: '/api/social/scheduled',
            type: 'GET',
            success: function (r) {
                if (!r.success) return;
                const posts = r.scheduled_posts || [];
                if (!posts.length) {
                    $('#scheduledPostsTbody').html(`
                        <tr>
                            <td colspan="6" class="text-center py-4 text-slate-400">No scheduled posts found. Use "Schedule Campaign Post" on any generated run.</td>
                        </tr>
                    `);
                    return;
                }

                let html = '';
                posts.forEach(p => {
                    const platforms = Array.isArray(p.platforms) ? p.platforms.map(pl => {
                        const icons = { facebook: '<i class="fab fa-facebook color-fb me-1"></i>', instagram: '<i class="fab fa-instagram color-ig me-1"></i>', linkedin: '<i class="fab fa-linkedin color-li me-1"></i>' };
                        return `${icons[pl] || ''}${pl.toUpperCase()}`;
                    }).join(' ') : p.platforms;

                    const statusBadges = {
                        pending: '<span class="badge bg-warning text-dark"><i class="fas fa-clock me-1"></i>Pending</span>',
                        published: '<span class="badge bg-success"><i class="fas fa-check-circle me-1"></i>Published</span>',
                        failed: '<span class="badge bg-danger"><i class="fas fa-triangle-exclamation me-1"></i>Failed</span>',
                        cancelled: '<span class="badge bg-secondary"><i class="fas fa-ban me-1"></i>Cancelled</span>'
                    };

                    const cancelBtn = p.status === 'pending'
                        ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelScheduledPostItem(${p.id})"><i class="fas fa-ban me-1"></i>Cancel</button>`
                        : '—';

                    html += `
                        <tr>
                            <td class="font-monospace">#${p.id}</td>
                            <td style="max-width: 250px;" class="text-truncate" title="${escapeAttr(p.story)}">${escapeHtml(p.story)}</td>
                            <td>${platforms}</td>
                            <td class="font-monospace text-navy font-semibold">${p.scheduled_at}</td>
                            <td>${statusBadges[p.status] || p.status}</td>
                            <td class="text-end">${cancelBtn}</td>
                        </tr>
                    `;
                });
                $('#scheduledPostsTbody').html(html);
            }
        });
    }

    window.cancelScheduledPostItem = function (postId) {
        if (!confirm('Are you sure you want to cancel this scheduled post?')) return;
        $.ajax({
            url: `/api/social/scheduled/${postId}/cancel`,
            type: 'POST',
            success: function (r) {
                if (r.success) {
                    showToast('Scheduled post cancelled', 'info');
                    loadScheduledPosts();
                }
            },
            error: function () {
                showToast('Failed to cancel scheduled post', 'error');
            }
        });
    };

    $('#modalScheduleBtn').on('click', function () {
        const modalElem = document.getElementById('historyModal');
        if (modalElem) bootstrap.Modal.getInstance(modalElem)?.hide();

        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const isoString = tomorrow.toISOString().slice(0, 16);
        $('#schedDateTimeInput').val(isoString);

        const schedModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('schedulePostModal'));
        schedModal.show();
    });

    $('#confirmSchedulePostBtn').on('click', function () {
        const scheduledAt = $('#schedDateTimeInput').val();
        if (!scheduledAt) {
            showToast('Please select a valid date and time', 'error');
            return;
        }

        const selectedPlatforms = [];
        if ($('#schedFbCheck').is(':checked')) selectedPlatforms.push('facebook');
        if ($('#schedIgCheck').is(':checked')) selectedPlatforms.push('instagram');
        if ($('#schedLiCheck').is(':checked')) selectedPlatforms.push('linkedin');

        if (!selectedPlatforms.length) {
            showToast('Please select at least one target platform', 'error');
            return;
        }

        const storyText = $('#modalStory').text() || 'Campaign Post';

        $.ajax({
            url: '/api/social/schedule',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                platforms: selectedPlatforms,
                scheduled_at: scheduledAt,
                content_json: { story: storyText },
                run_id: lastRunId
            }),
            success: function (r) {
                if (r.success) {
                    showToast('Post scheduled successfully for ' + r.scheduled_post.scheduled_at + '!', 'success');
                    bootstrap.Modal.getInstance(document.getElementById('schedulePostModal')).hide();
                    setTimeout(() => {
                        window.location.href = '/settings';
                    }, 1200);
                }
            },
            error: function (xhr) {
                showToast('Scheduling failed: ' + (xhr.responseJSON?.error || 'Error'), 'error');
            }
        });
    });

    // ── AI Models & Purpose Inspector Handler ─────────────────────────────
    $('#headerModelInfoBtn').on('click', function () {
        openModelArchitectureModal();
    });

    function openModelArchitectureModal() {
        const modalElem = document.getElementById('modelArchitectureModal');
        if (modalElem) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalElem);
            modal.show();
            loadModelsInfoData();
        }
    }

    function loadModelsInfoData() {
        $.ajax({
            url: '/api/models/info',
            type: 'GET',
            success: function (r) {
                if (!r.success || !r.models) return;

                const summary = r.summary || {};
                const categoryIcons = {
                    llm: { icon: 'fa-brain', class: 'cat-llm' },
                    vision: { icon: 'fa-eye', class: 'cat-vision' },
                    image: { icon: 'fa-image', class: 'cat-image' },
                    video: { icon: 'fa-video', class: 'cat-video' },
                    memory: { icon: 'fa-database', class: 'cat-memory' }
                };

                let html = `
                    <div class="col-12 mb-2">
                        <div class="runtime-metrics-bar p-3 bg-slate-50 border rounded-3 d-flex flex-wrap align-items-center justify-content-between gap-3">
                            <div class="d-flex align-items-center gap-2">
                                <span class="badge bg-success-subtle text-success border border-success-subtle px-2.5 py-1.5 font-bold fs-8">
                                    <i class="fas fa-circle-check me-1"></i>Runtime Active
                                </span>
                                <span class="text-slate-600 font-semibold fs-7">
                                    AWS Region: <strong class="text-navy">${escapeHtml(summary.aws_region || 'us-east-1')}</strong>
                                </span>
                            </div>
                            <div class="d-flex gap-4 fs-7">
                                <div><span class="text-slate-500">User Runs:</span> <strong class="text-navy">${summary.total_user_runs || 0}</strong></div>
                                <div><span class="text-slate-500">Tokens:</span> <strong class="text-navy font-monospace">${Number(summary.total_tokens_used || 0).toLocaleString()}</strong></div>
                                <div><span class="text-slate-500">Cost USD:</span> <strong class="text-emerald font-monospace">$${Number(summary.total_cost_usd || 0).toFixed(4)}</strong></div>
                            </div>
                        </div>
                    </div>
                `;

                r.models.forEach(m => {
                    const iconMeta = categoryIcons[m.category] || { icon: 'fa-microchip', class: 'cat-llm' };
                    const agentTags = (m.agents || []).map(a => `<span class="agent-pill-tag">${a}</span>`).join(' ');

                    const statusBadges = {
                        ACTIVE: '<span class="badge bg-success text-white"><i class="fas fa-circle me-1 fs-9"></i>ACTIVE</span>',
                        ONLINE: '<span class="badge bg-primary text-white"><i class="fas fa-signal me-1 fs-9"></i>ONLINE</span>',
                        STANDBY: '<span class="badge bg-secondary text-white"><i class="fas fa-pause me-1 fs-9"></i>STANDBY</span>'
                    };

                    html += `
                        <div class="col-md-6 col-lg-4">
                            <div class="model-purpose-card">
                                <div class="d-flex align-items-center justify-content-between mb-3">
                                    <div class="d-flex align-items-center gap-3">
                                        <div class="cat-icon-box ${iconMeta.class}">
                                            <i class="fas ${iconMeta.icon}"></i>
                                        </div>
                                        <div>
                                            <h6 class="font-bold text-navy mb-0">${escapeHtml(m.purpose)}</h6>
                                            <small class="text-primary font-semibold">${escapeHtml(m.provider)}</small>
                                        </div>
                                    </div>
                                    ${statusBadges[m.status] || '<span class="badge bg-info text-white">ONLINE</span>'}
                                </div>
                                <div class="mb-3">
                                    <span class="small text-slate-500 d-block mb-1">Active Model Slug:</span>
                                    <span class="slug-badge"><i class="fas fa-code-branch me-1 text-slate-400"></i>${escapeHtml(m.model_name)}</span>
                                </div>
                                <p class="small text-slate-600 mb-3 flex-grow-1" style="font-size: 12px; line-height: 1.5;">${escapeHtml(m.description)}</p>
                                <div class="pt-2 border-top">
                                    <small class="text-slate-500 font-semibold d-block mb-2">Assigned Agents &amp; Services:</small>
                                    <div class="d-flex gap-1 flex-wrap">
                                        ${agentTags}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                });

                $('#modelsInfoCardsContainer').html(html);
            },
            error: function () {
                $('#modelsInfoCardsContainer').html(`
                    <div class="col-12 text-center py-5 text-danger">
                        <i class="fas fa-triangle-exclamation fa-2x mb-2"></i>
                        <p>Failed to load dynamic AI Model architecture information.</p>
                    </div>
                `);
            }
        });
    }




});