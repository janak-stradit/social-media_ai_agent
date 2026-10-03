$(document).ready(function () {
    let uploadedImagePath = null;
    // Image commands (/3dbillboard, /metaad, ...) - declared up here because the
    // Generate button's state reads them from code that can run during start-up
    let imagePresets = [];
    let activePreset = null;          // the chosen command (from /api/image-presets)
    let presetAllSizes = false;       // /metaad: all sizes instead of the feed image
    let uploadedImageAnalysis = null; // the attached photo's analysis -> product notes
    const slash = { open: false, index: 0, matches: [] };
    // True while an attached image is uploading + being analyzed: Generate and
    // Analyze wait for it, otherwise the post would be made without the image.
    let imageUploading = false;
    let imageUploadXhr = null;
    let threadActiveImagePath = null;
    let lastRunId = null;
    let lastAssistantContext = null;
    // The thread's first brief - kept for every follow-up so the original
    // topic isn't lost once the user has sent more than one refinement.
    let threadRootBrief = null;
    // Platforms used by the thread's last generation - a follow-up that
    // doesn't pick any platform continues on these instead of detouring
    // through standalone research (which has no thread context).
    let lastThreadPlatforms = [];
    // The assistant message whose post the next follow-up refines (the latest
    // output by default, or whichever version "Refine this version" picked).
    let refineBaseMsgId = null;
    let messageCounter = 0;
    // The conversation every message is saved into, until "New Conversation"
    // (the server creates it on the first message and returns its id)
    let currentConversationId = null;
    const CONVERSATION_KEY = 'avir_active_conversation';

    function setConversation(id) {
        currentConversationId = id ? Number(id) : null;
        try {
            if (currentConversationId) sessionStorage.setItem(CONVERSATION_KEY, String(currentConversationId));
            else sessionStorage.removeItem(CONVERSATION_KEY);
        } catch (e) { /* storage blocked - the thread still works until a refresh */ }
        $('.history-card').each(function () {
            $(this).toggleClass('active', Number($(this).data('id')) === currentConversationId);
        });
    }

    function clearConversation() {
        setConversation(null);
    }
    window.chatHistory = {}; // Store generations per msgId

    // Builds the multi-turn context sent as previous_context: the original
    // brief, the latest refinement, each platform's caption and the visual
    // direction used for its image/video, so a follow-up like "enhance the
    // image" knows what was actually generated last turn.
    function buildThreadContext(latestBrief, platforms, content) {
        if (!threadRootBrief) threadRootBrief = latestBrief;
        let summary = `Original Brief: ${threadRootBrief}\n`;
        if (latestBrief && latestBrief !== threadRootBrief) {
            summary += `Latest Refinement Request: ${latestBrief}\n`;
        }
        summary += 'Generated Captions:\n';
        platforms.forEach(p => {
            if (content[p]?.caption?.primary_caption) {
                summary += `[${p.toUpperCase()}]: ${content[p].caption.primary_caption}\n`;
            }
        });
        platforms.forEach(p => {
            const visual = content[p]?.media_prompt;
            if (visual) {
                summary += `[${p.toUpperCase()} VISUAL DIRECTION]: ${visual.substring(0, 600)}\n`;
            }
        });
        return summary;
    }

    // Hashtags are a single curated set (see agents/hashtag_agent.py); also
    // tolerate the old reach_hashtags key from older runs in history.
    function getTagList(rawTags) {
        rawTags = rawTags || {};
        return Array.isArray(rawTags.hashtags) ? rawTags.hashtags
            : Array.isArray(rawTags) ? rawTags
            : Array.isArray(rawTags.reach_hashtags) ? rawTags.reach_hashtags
            : [];
    }

    // Makes the currently displayed version of msgId the post that the next
    // follow-up refines, and re-seeds the thread context from it.
    function adoptAsRefineBase(msgId) {
        const h = window.chatHistory[msgId];
        if (!h) return;
        const content = h.responses[h.currentIndex].content;
        refineBaseMsgId = msgId;
        lastThreadPlatforms = h.platforms.slice();
        lastAssistantContext = buildThreadContext(h.requestBody.story, h.platforms, content);
        // Unbranded copy first - the real logo is stamped on again after generation
        const image = h.platforms.map(p => {
            const img = content[p]?.media?.image || {};
            return img.clean_url || img.url || img.previous_clean_url || img.previous_url;
        }).find(Boolean);
        if (image) threadActiveImagePath = image;

        $('.btn-refine-base').each(function () {
            const isBase = $(this).attr('data-msg') === msgId;
            $(this).html(`<i class="fas fa-wand-magic-sparkles me-1"></i>${isBase ? 'Refining this version' : 'Refine this version'}`);
        });
        updateGenerateGate();
    }

    // Frozen copy of the base post at send time, so a later Regenerate of the
    // refinement re-applies it to the same version.
    function snapshotRefineBase(msgId) {
        const h = window.chatHistory[msgId];
        const rData = h.responses[h.currentIndex];
        return {
            msgId: msgId,
            runId: rData.runId,
            platforms: h.platforms.slice(),
            content: JSON.parse(JSON.stringify(rData.content || {})),
            qualitySummary: rData.qualitySummary,
            context: lastAssistantContext
        };
    }

    function toRefinePayload(content, platforms) {
        const out = {};
        platforms.forEach(p => {
            const d = content[p] || {};
            out[p] = {
                caption: d.caption?.primary_caption || '',
                hashtags: getTagList(d.hashtags),
                media_prompt: d.media_prompt || '',
                image_url: d.media?.image?.url || d.media?.image?.previous_url || null,
                image_clean_url: d.media?.image?.clean_url || d.media?.image?.previous_clean_url || null,
                image_prompt: d.media?.image?.prompt || d.media?.image?.previous_prompt || null,
                image_asset_id: d.media?.image?.asset_id || d.media?.image?.previous_asset_id || null,
                image_aspect: d.media?.image?.aspect || null,
                video_url: d.media?.video?.url || null
            };
        });
        return out;
    }

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

    // Phones: the sidebar is a slide-in drawer over the chat instead of a
    // column, so the desktop show/hide preference isn't touched there.
    const isPhone = () => window.matchMedia('(max-width: 768px)').matches;
    function setMobileDrawer(open) {
        $('#sidebar').toggleClass('mobile-open', open);
        $('#sidebarBackdrop').toggleClass('show', open);
        $('body').toggleClass('drawer-open', open);
    }
    window.closeMobileDrawer = function () { if (isPhone()) setMobileDrawer(false); };

    $('#sidebarToggleBtn, #sidebarHideBtn').on('click', function () {
        if (isPhone()) {
            setMobileDrawer(!$('#sidebar').hasClass('mobile-open'));
            return;
        }
        toggleSidebar();
    });
    $('#sidebarBackdrop').on('click', function () { setMobileDrawer(false); });
    // Picking a conversation or starting a new one closes the drawer
    $('#sidebar').on('click', '.history-card, #newChatBtn', function () { window.closeMobileDrawer(); });
    $(window).on('resize', function () { if (!isPhone()) setMobileDrawer(false); });

    // Phones: composer options (platforms, voice, tone, output) fold away
    $('#dockOptionsBtn').on('click', function () {
        const open = !$('#dropZone').hasClass('options-open');
        $('#dropZone').toggleClass('options-open', open);
        $(this).toggleClass('active', open).attr('aria-expanded', String(open));
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
        loadImageQuota();
        loadImagePresets();
        openModalFromHash();
        restoreConversation();
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

    // Writes genuinely NEW idea cards (and a greeting) from the user's whole
    // brand profile - products, audience, dos/don'ts, markets, credentials,
    // compliance rules - avoiding what's shown and recently posted (POST
    // /api/brand-profile/quick-prompts/generate). If that fails, falls back
    // to drawing a different subset from the existing pool.
    window.regenerateGreeting = function () {
        if (!_brandProfileCompanyName || !_brandProfileIdeaPool) return;
        const $btn = $('#regenerateGreetingBtn');
        if ($btn.prop('disabled')) return;

        const shownTitles = $('.quick-prompt-card .quick-prompt-header span').map(function () { return $(this).text(); }).get();
        $btn.prop('disabled', true).find('i').addClass('fa-spin');
        $('.quick-prompts-grid').addClass('is-loading').css('opacity', 0.5);

        $.ajax({
            url: '/api/brand-profile/quick-prompts/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ exclude_titles: shownTitles }),
            success: function (r) {
                const ideas = (r && r.post_ideas) || [];
                if (!ideas.length) return fallbackShuffle();
                _brandProfileIdeaPool = ideas.concat(_brandProfileIdeaPool.filter(i => !ideas.some(n => n.title === i.title)));
                renderQuickPromptCards(ideas);
                $('.welcome-title').text(r.greeting || pickGreeting(_brandProfileCompanyName));
            },
            error: function (xhr) {
                const res = xhr.responseJSON || {};
                if (res.credit_limit_exceeded) showToast(res.error, 'warning');
                fallbackShuffle();
            },
            complete: function () {
                $btn.prop('disabled', false).find('i').removeClass('fa-spin');
                $('.quick-prompts-grid').removeClass('is-loading').css('opacity', '');
            }
        });

        function fallbackShuffle() {
            $('.welcome-title').text(pickGreeting(_brandProfileCompanyName));
            renderQuickPromptCards(pickIdeaSubset(_brandProfileIdeaPool));
        }
    };

    // No brand profile yet (website skipped at onboarding): posts can't match
    // the user's brand, so point them to My Brand Configuration. Dismissable.
    function showBrandProfileNudge() {
        let dismissed = false;
        try { dismissed = localStorage.getItem('brand_nudge_dismissed') === '1'; } catch (e) { /* storage blocked */ }
        if (dismissed || $('#brandProfileNudge').length) return;
        $('.welcome-hero-card').after(`
            <div class="brand-nudge" id="brandProfileNudge" role="status">
                <div class="brand-nudge-icon"><i class="fas fa-wand-magic-sparkles"></i></div>
                <div class="brand-nudge-copy">
                    <div class="brand-nudge-title">Make every post sound like your brand</div>
                    <div class="brand-nudge-text">Add your website and we'll learn your voice, colors and products - every caption and image follows them.</div>
                </div>
                <a href="/brand-profile" class="btn-chat-send brand-nudge-cta">Add my website<i class="fas fa-arrow-right ms-2"></i></a>
                <button type="button" class="brand-nudge-close" title="Dismiss" aria-label="Dismiss"><i class="fas fa-xmark"></i></button>
            </div>
        `);
        $('#brandProfileNudge .brand-nudge-close').on('click', function () {
            try { localStorage.setItem('brand_nudge_dismissed', '1'); } catch (e) { /* storage blocked */ }
            $('#brandProfileNudge').remove();
        });
    }

    function loadBrandProfileQuickPrompts() {
        $.ajax({
            url: '/api/brand-profile/quick-prompts',
            type: 'GET',
            success: function (r) {
                if (r && r.needs_brand_profile) showBrandProfileNudge();
                const ideas = (r && r.post_ideas) || [];
                if (!ideas.length) return;

                _brandProfileIdeaPool = ideas;
                // Pool is newest-first (regenerated ideas are prepended), so lead with the latest
                renderQuickPromptCards(ideas.slice(0, QUICK_PROMPT_CARD_COUNT));

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
    $('#newChatBtn').on('click', function () {
        startNewChat();
    });

    function startNewChat(silent) {
        $('#chatThread').empty();
        $('#welcomeHero').removeClass('d-none');
        storyInput.val('').trigger('input');
        clearAttachment();
        threadActiveImagePath = null;
        lastAssistantContext = null;
        threadRootBrief = null;
        lastThreadPlatforms = [];
        refineBaseMsgId = null;
        clearConversation();
        updateGenerateGate();
        if (!silent) showToast('Started a new conversation', 'info');
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
        if (imageUploadXhr) imageUploadXhr.abort();
        clearAttachment();
        threadActiveImagePath = null;
    });

    function setImageUploading(on) {
        imageUploading = on;
        $('#dropZone').toggleClass('image-uploading', on);
        const busy = $('#dropZone').hasClass('dock-disabled');
        $('#analyzeBtn').prop('disabled', on || busy).attr('title', on ? 'Wait until the image has finished uploading' : 'Analyze Brief');
        updateGenerateGate();
    }

    function clearAttachment() {
        uploadedImagePath = null;
        uploadedImageAnalysis = null;
        $('#imageInput').val('');
        $('#previewImg').attr('src', '');
        $('#imagePreview').addClass('d-none');
        setAnalysisBadge('badge-neutral', 'Ready');
        if (typeof updateGenerateGate === 'function') updateGenerateGate();
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

        // A new image replaces one that is still uploading
        if (imageUploadXhr) imageUploadXhr.abort();
        uploadedImagePath = null;
        setImageUploading(true);
        setAnalysisBadge('badge-warn', '<i class="fas fa-spinner fa-spin me-1"></i>Uploading &amp; analyzing...');
        const xhr = imageUploadXhr = $.ajax({
            url: '/api/upload',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function (r) {
                uploadedImagePath = r.filepath;
                threadActiveImagePath = r.filepath;
                uploadedImageAnalysis = r.analysis || null;
                setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Analyzed');
                showToast('Visual asset uploaded & analyzed!', 'success');
            },
            error: function (_xhr, status) {
                if (status === 'abort') return;  // removed or replaced while uploading
                // Don't leave a preview that looks attached but would be ignored
                clearAttachment();
                showToast('Image upload failed - please attach it again.', 'error');
            },
            complete: function () {
                if (imageUploadXhr !== xhr) return;  // a newer upload took over
                imageUploadXhr = null;
                setImageUploading(false);
            }
        });
    }

    function setAnalysisBadge(cls, html) {
        $('#imageAnalysisStatus').attr('class', 'analysis-badge ' + cls).html(html);
    }

    // ── Story Analysis ─────────────────────────────────────────────────
    window.analyzeStory = function () {
        if (imageUploading) {
            showToast('Wait until the image has finished uploading.', 'warning');
            return;
        }
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
        if (!disabled && imageUploading) $('#analyzeBtn').prop('disabled', true);
        if (!disabled) updateGenerateGate();
    }

    // crypto.randomUUID only exists on HTTPS/localhost; the site may run on plain HTTP
    function newProgressId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        return 'p' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    }

    function setAgentStep(msgId, step, state) {
        const $item = $(`#${msgId}_step_${step}`);
        if (!$item.length || $item.data('state') === state) return;
        $item.data('state', state).removeClass('active completed');
        const $icon = $item.find('.agent-step-icon');
        if (state === 'active') {
            $item.addClass('active');
            $icon.html('<div class="spinner-border spinner-border-sm text-primary" role="status"></div>');
        } else if (state === 'done') {
            $item.addClass('completed');
            $icon.html('<i class="fas fa-check-circle text-success"></i>');
        }
    }

    // A finished caption, shown under the Caption step while hashtags and
    // quality checks are still running - so the wait doesn't feel empty.
    function renderCaptionPreview(msgId, platform, text) {
        const $list = $(`#${msgId}_captionPreviews`);
        if (!$list.length || !text) return;
        const key = String(platform).replace(/[^a-z0-9_-]/gi, '');
        if ($list.find(`[data-platform="${key}"]`).length) return;
        const names = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube', twitter: 'X', x: 'X', tiktok: 'TikTok' };
        const label = names[key.toLowerCase()] || key.charAt(0).toUpperCase() + key.slice(1);
        $list.removeClass('d-none').append(`
            <div class="caption-preview" data-platform="${key}">
                <div class="caption-preview-label"><i class="fas fa-eye me-1"></i>${escapeHtml(label)} caption - finishing touches in progress</div>
                <div class="caption-preview-text">${escapeHtml(text)}</div>
            </div>
        `);
    }

    // Plain-language message for a failed generation (the raw error stays
    // available under "Technical details").
    function friendlyGenerationError(xhr, errText) {
        if (xhr.status === 0) return "We couldn't reach the server. Check your internet connection and try again.";
        if ([502, 503, 504].includes(xhr.status) || /time(d)? ?out/i.test(errText)) {
            return 'The AI service took too long to answer. Please try again - it usually works on the next attempt.';
        }
        if (/all providers|LLM|HeyRoute|rate limit|429|overloaded|unavailable/i.test(errText)) {
            return 'The AI service is busy or unavailable right now. Please try again in a moment.';
        }
        return 'Something went wrong while creating your post. Please try again.';
    }

    // Polls the server's step states every second until stop() is called.
    function pollGenerationProgress(msgId, progressId) {
        let stopped = false;
        let timer = null;
        (function poll() {
            if (stopped) return;
            $.getJSON(`/api/generate/progress/${encodeURIComponent(progressId)}`)
                .done(function (res) {
                    Object.entries((res && res.steps) || {}).forEach(([step, value]) => {
                        if (step.startsWith('preview:')) renderCaptionPreview(msgId, step.slice(8), value);
                        else setAgentStep(msgId, step, value);
                    });
                })
                .always(function () {
                    if (!stopped) timer = setTimeout(poll, 1000);
                });
        })();
        return function stop() {
            stopped = true;
            clearTimeout(timer);
        };
    }

    function executeGeneration(msgId, requestBody, assistantElem, platforms, activeImgPath, mediaType, selectedOutputs) {
        setChatDockDisabled(true);
        // Step states come from the server (GET /api/generate/progress/<id>),
        // so the stepper shows what is really running - captions and hashtags
        // run at the same time, so both can be active together.
        const progressId = newProgressId();
        const prior = window.chatHistory[msgId];
        requestBody = Object.assign({}, requestBody, {
            progress_id: progressId,
            conversation_id: currentConversationId,
            version_of_run_id: prior && prior.responses.length ? prior.responses[0].runId : undefined
        });
        const stopProgress = pollGenerationProgress(msgId, progressId);

        window.currentGenerationRequest = $.ajax({
            url: '/api/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(requestBody),
            success: function (r) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                stopProgress();
                lastRunId = r.run_id || null;
                if (r.conversation_id) setConversation(r.conversation_id);

                if (!window.chatHistory[msgId]) {
                    window.chatHistory[msgId] = { responses: [], currentIndex: 0, requestBody, platforms, activeImgPath, mediaType, selectedOutputs };
                }

                const rData = { content: r.content, runId: lastRunId, usage: r.usage, agentsExecuted: r.agents_executed, qualitySummary: r.quality_summary };
                window.chatHistory[msgId].responses.push(rData);
                window.chatHistory[msgId].currentIndex = window.chatHistory[msgId].responses.length - 1;

                // This output is what the next follow-up refines
                adoptAsRefineBase(msgId);

                // Render finished Assistant Response inside Assistant Card
                renderAssistantResponse(msgId);
                renderHistory();
                loadUserUsageMetrics();
                scrollToBottom();
            },
            error: function (xhr, status, error) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                stopProgress();
                
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
                        <div class="gen-error-card" role="alert">
                            <div class="gen-error-title"><i class="fas fa-circle-exclamation me-2"></i>Your post couldn't be created</div>
                            <p class="gen-error-text">${escapeHtml(friendlyGenerationError(xhr, errText))}</p>
                            <div class="gen-error-actions">
                                <button type="button" class="btn-chat-send gen-retry-btn"><i class="fas fa-rotate-right me-2"></i>Try again</button>
                            </div>
                            <details class="gen-error-details"><summary>Technical details</summary><code>${escapeHtml(errText)}</code></details>
                        </div>
                    `);
                    assistantElem.find('.gen-retry-btn').on('click', function () {
                        // Same request again, in the same chat bubble
                        assistantElem.find('.assistant-card').html(buildPipelineCardHtml(msgId, !!activeImgPath));
                        executeGeneration(msgId, requestBody, assistantElem, platforms, activeImgPath, mediaType, selectedOutputs);
                    });
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
        if (imageUploading) {
            showToast('Wait until the image has finished uploading - then send.', 'warning');
            return;
        }
        // An image command: chosen from the "/" menu, or typed ("/metaad blue background").
        // A typed command replaces one already chosen.
        const typed = /^\/([\w-]+)(?:\s+([\s\S]*))?$/.exec(storyInput.val().trim());
        if (typed) {
            if (!presetById(typed[1].toLowerCase())) {
                showToast(`Unknown command /${typed[1]} - type / to see the image commands.`, 'warning');
                return;
            }
            choosePreset(typed[1].toLowerCase(), typed[2] || '');
        }
        if (activePreset) {
            runPreset(storyInput.val().trim());
            return;
        }
        if (imageGateBlocked()) {
            showToast(imageLimitText(window.__imageQuota), 'warning');
            return;
        }
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
        const pickedPlatforms = platforms.slice();
        const pickedOutputs = selectedOutputs.slice();

        // A follow-up on an existing post is refined surgically (only what the
        // message asks for changes, images are edited not regenerated) - unless
        // it targets a platform the post doesn't have yet, which needs the full
        // pipeline to write for that platform.
        const refineBase = refineBaseMsgId ? window.chatHistory[refineBaseMsgId] : null;
        const baseIsPreset = !!(refineBase && refineBase.preset);
        const canRefine = !!(refineBase && lastAssistantContext && story &&
            (baseIsPreset || pickedPlatforms.every(p => refineBase.platforms.includes(p))));

        // A follow-up in an existing thread continues on the same platforms
        // (and at least a caption) when none are picked, rather than falling
        // into research-then-ask, which would treat the follow-up ("generate
        // an image of this post") as a brand new, context-free topic.
        if (lastAssistantContext) {
            if (!platforms.length) platforms.push(...lastThreadPlatforms);
            if (!selectedOutputs.length) selectedOutputs.push('text');
        }
        const mediaType = selectedOutputs.join(', ') || 'none';
        const hasImage = !!(uploadedImagePath || threadActiveImagePath);
        const activeImgPath = uploadedImagePath || threadActiveImagePath;

        // Hide welcome hero on first message
        $('#welcomeHero').addClass('d-none');

        // Create unique message IDs
        messageCounter++;
        const msgId = 'msg_' + Date.now() + '_' + messageCounter;

        const refinePlatforms = canRefine
            ? (baseIsPreset || !pickedPlatforms.length ? refineBase.platforms.slice() : pickedPlatforms) : null;
        const attachedImage = uploadedImagePath;

        // 1. Append User Chat Message Bubble
        appendUserMessage(story, uploadedImagePath, baseIsPreset ? [] : (refinePlatforms || platforms), tone, mediaType, brandVoice);

        // Clear input area
        storyInput.val('').trigger('input');
        clearAttachment();
        scrollToBottom();

        if (canRefine) {
            runRefine(msgId, {
                instruction: story,
                platforms: refinePlatforms,
                tone: tone,
                brandVoice: brandVoice,
                selectedOutputs: pickedOutputs,
                attachedImage: attachedImage,
                targetCompany: targetCompany,
                base: snapshotRefineBase(refineBaseMsgId)
            });
            return;
        }

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
            include_strategy: false,
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

        // A public URL (replayed conversation) shows itself; a fresh upload shows its preview
        const attachSrc = imagePath && /^(\/|https?:)/.test(imagePath) ? imagePath : $('#previewImg').attr('src');
        const attachmentHtml = imagePath && attachSrc ? `
            <div class="chat-user-attachment">
                <img src="${escapeAttr(attachSrc)}" alt="Attached product photo">
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

    // Compliance result for one platform's caption (services/compliance_service.py):
    // what was checked, issues auto-fixed, issues needing a human (e.g. a
    // permit number), and disclaimers appended verbatim. Nothing when the
    // user has no compliance profile.
    function buildComplianceHtml(compliance) {
        if (!compliance) return '';
        const flags = compliance.flags || [];
        const attention = compliance.needs_attention || 0;
        const headerIcon = attention ? 'fa-triangle-exclamation text-warning' : 'fa-scale-balanced text-success';
        const headerText = attention
            ? `${attention} compliance item${attention > 1 ? 's' : ''} need${attention > 1 ? '' : 's'} your attention`
            : flags.length ? `${flags.length} compliance issue${flags.length > 1 ? 's' : ''} auto-fixed` : 'Compliance checked - no issues';
        const flagsHtml = flags.map(f => `
            <li class="mb-1">
                <span class="badge ${f.auto_fixed ? 'bg-success-subtle text-success' : 'bg-warning-subtle text-warning'} me-1">${f.auto_fixed ? 'Fixed' : 'Action needed'}</span>
                <strong>${escapeHtml(f.framework)}</strong> - ${escapeHtml(f.issue)}
                ${f.fix ? `<div class="text-muted small">${escapeHtml(f.fix)}</div>` : ''}
            </li>`).join('');
        const disclaimersHtml = (compliance.disclaimers_added || []).map(d =>
            `<li class="mb-1"><span class="badge bg-primary-subtle text-primary me-1">Disclaimer added</span>${escapeHtml(d)}</li>`).join('');
        return `
            <div class="post-box-card">
                <div class="post-box-header">
                    <span class="post-box-title"><i class="fas ${headerIcon} me-1"></i>${headerText}</span>
                    <span class="text-muted small">${compliance.rules_checked} rules checked</span>
                </div>
                ${flagsHtml || disclaimersHtml ? `<ul class="list-unstyled small mb-1">${flagsHtml}${disclaimersHtml}</ul>` : ''}
                <div class="text-muted" style="font-size: 0.7rem;">Guidance only, not legal advice.</div>
            </div>`;
    }

    // Renders a finished image/video card - shared by triggerMediaGenInChat
    // (a generation that just completed live) and renderAssistantResponse
    // (a run reloaded from history that already has saved media, see
    // db.append_run_media / content[platform].media.{image,video}) so both
    // paths produce the identical card instead of two hand-maintained copies.
    function buildGeneratedMediaHtml(mediaType, media, msgId, platform, reveal) {
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
        // "New image": Regenerate keeps the image, so this is how a post gets a fresh one
        const newImageBtn = msgId && platform ? `
                        <button type="button" class="btn-copy-sm" onclick="regenerateImage('${msgId}', '${platform}')"
                                title="Create a new image for this post (uses 1 of today's images); the caption stays"><i class="fas fa-rotate me-1"></i>New image</button>` : '';
        return `
            <div class="media-output-card">
                <div class="media-output-header">
                    <span><i class="fas fa-image text-primary me-2"></i>Generated Image (${media.resolution || '1024x1024'})</span>
                    <div class="media-output-actions">
                        <button type="button" class="btn-copy-sm" onclick="openImagePreview('${media.url}')" title="View the image larger"><i class="fas fa-expand me-1"></i>Preview</button>
                        <button type="button" class="btn-copy-sm" onclick="downloadAsZip('${media.url}', this)"><i class="fas fa-file-zipper me-1"></i>Download</button>${newImageBtn}
                    </div>
                </div>
                <div class="media-output-body">
                    <img src="${media.url}" class="media-output-img${reveal ? ' img-reveal' : ''}" alt="Generated media">
                </div>
            </div>
        `;
    }

    // Replaces a post's image with a new one (also on the platforms sharing it);
    // the caption and hashtags stay as they are
    window.regenerateImage = function (msgId, platform) {
        const h = window.chatHistory[msgId];
        if (!h) return;
        const r = h.responses[h.currentIndex];
        const content = r.content || {};
        const url = content[platform]?.media?.image?.url;
        const sharing = h.platforms.filter(p => p !== platform && url && content[p]?.media?.image?.url === url);
        const pData = content[platform] || {};
        const caption = pData.caption?.primary_caption || h.requestBody.story;
        $(`#${msgId}_media_image_${platform}`).html(imageCreatingHtml(sharing.length > 0 || platform === 'instagram', 'Creating a new image'));
        triggerMediaGenInChat(platform, caption, 'image', h.requestBody.tone, r.runId, h.activeImgPath,
            `${msgId}_media_image_${platform}`, pData.media_prompt || caption, msgId, sharing);
    };

    // ── Image preview (lightbox) ────────────────────────────────────────
    // Full-screen view of a generated image: fits the screen, Esc / click
    // outside / X closes it, and "open full size" shows the original in a tab.
    window.openImagePreview = function (src) {
        if (!src) return;
        let $box = $('#imageLightbox');
        if (!$box.length) {
            $('body').append(`
                <div class="image-lightbox" id="imageLightbox" role="dialog" aria-modal="true" aria-label="Image preview">
                    <div class="image-lightbox-toolbar">
                        <a class="image-lightbox-btn" id="imageLightboxOpen" target="_blank" rel="noopener" title="Open full size in a new tab"><i class="fas fa-up-right-from-square"></i></a>
                        <button type="button" class="image-lightbox-btn" id="imageLightboxClose" title="Close (Esc)"><i class="fas fa-xmark"></i></button>
                    </div>
                    <img class="image-lightbox-img" alt="Generated image preview">
                </div>
            `);
            $box = $('#imageLightbox');
            $box.on('click', function (e) { if (e.target === this) closeImagePreview(); });
            $('#imageLightboxClose').on('click', closeImagePreview);
            $(document).on('keydown', function (e) {
                if (e.key === 'Escape' && $('#imageLightbox').hasClass('show')) closeImagePreview();
            });
        }
        $box.find('.image-lightbox-img').attr('src', src);
        $('#imageLightboxOpen').attr('href', src);
        $box.addClass('show');
        $('body').addClass('lightbox-open');
        $('#imageLightboxClose').trigger('focus');
    };

    function closeImagePreview() {
        $('#imageLightbox').removeClass('show');
        $('body').removeClass('lightbox-open');
    }

    // Clicking a generated image opens the preview too
    $(document).on('click', '.media-output-img', function () { window.openImagePreview($(this).attr('src')); });

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

                <div class="agent-step-item active" id="${msgId}_step_story" data-state="active">
                    <div class="agent-step-icon"><div class="spinner-border spinner-border-sm text-primary" role="status"></div></div>
                    <span class="agent-step-name"><i class="fas fa-brain me-1 text-purple"></i>Story &amp; RAG Agent</span>
                    <span class="agent-step-desc">Analyzing narrative themes &amp; retrieving past brand memory</span>
                </div>

                ${visionStepHtml}

                <div class="agent-step-item" id="${msgId}_step_caption">
                    <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                    <span class="agent-step-name"><i class="fas fa-pen-nib me-1 text-primary"></i>Caption Agent</span>
                    <span class="agent-step-desc">Writing the caption for each platform</span>
                </div>
                <div class="caption-preview-list d-none" id="${msgId}_captionPreviews"></div>

                <div class="agent-step-item" id="${msgId}_step_hashtag">
                    <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                    <span class="agent-step-name"><i class="fas fa-hashtag me-1 text-warning"></i>Hashtag Agent</span>
                    <span class="agent-step-desc">Curating broad, niche &amp; brand hashtags (runs alongside captions)</span>
                </div>

                <div class="agent-step-item" id="${msgId}_step_reviewer">
                    <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                    <span class="agent-step-name"><i class="fas fa-shield-check me-1 text-danger"></i>Quality Checks</span>
                    <span class="agent-step-desc">Length, no CTAs or placeholders, plain text, hashtag limits - rewrites a caption only if a check fails</span>
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

    function appendRefineThinking(msgId) {
        const html = `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar">${assistantAvatarSvg()}</div>
                <div class="assistant-card">
                    <div class="assistant-header">
                        <div class="assistant-title"><i class="fas fa-wand-magic-sparkles"></i>Refining Your Post</div>
                        <button type="button" class="btn btn-sm btn-outline-danger" onclick="cancelGeneration('${msgId}')">
                            <i class="fas fa-stop me-1"></i>Stop
                        </button>
                    </div>
                    <div class="agent-stepper">
                        <div class="agent-stepper-title"><i class="fas fa-cogs me-1"></i>Autonomous Agent Working:</div>
                        <div class="agent-step-item active">
                            <div class="agent-step-icon"><div class="spinner-border spinner-border-sm text-primary" role="status"></div></div>
                            <span class="agent-step-name"><i class="fas fa-pen-ruler me-1 text-purple"></i>Refinement Editor</span>
                            <span class="agent-step-desc">Applying only your requested change to the current version</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
        const elem = $(html);
        $('#chatThread').append(elem);
        return elem;
    }

    // Applies a follow-up to opts.base (a snapshotRefineBase() copy) via
    // /api/refine. Untargeted parts carry over unchanged; if the server says
    // the message is actually a new post, falls back to the full pipeline.
    function runRefine(msgId, opts, assistantElem) {
        assistantElem = assistantElem || appendRefineThinking(msgId);
        scrollToBottom();
        setChatDockDisabled(true);
        const base = opts.base;

        window.currentGenerationRequest = $.ajax({
            url: '/api/refine',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                instruction: opts.instruction,
                platforms: opts.platforms,
                tone: opts.tone,
                previous_context: base.context,
                selected_outputs: opts.selectedOutputs,
                base_run_id: base.runId,
                conversation_id: currentConversationId,
                version_of_run_id: (window.chatHistory[msgId]?.responses || [])[0]?.runId,
                preset: (window.chatHistory[base.msgId] || {}).presetRun || undefined,
                reference_image_path: opts.attachedImage,
                base: toRefinePayload(base.content, opts.platforms)
            }),
            success: function (r) {
                window.currentGenerationRequest = null;
                if (r.image_quota) window.applyImageQuota(r.image_quota);
                if (r.conversation_id) setConversation(r.conversation_id);
                setChatDockDisabled(false);

                if (r.intent === 'new_content') {
                    // A different post, not an edit - start it fresh.
                    threadRootBrief = null;
                    const outputs = opts.selectedOutputs.length ? opts.selectedOutputs : ['text'];
                    const hasImage = !!opts.attachedImage;
                    assistantElem.replaceWith(appendAssistantThinking(msgId, hasImage));
                    executeGeneration(msgId, {
                        story: opts.instruction,
                        image_path: opts.attachedImage,
                        platforms: opts.platforms,
                        tone: opts.tone,
                        brand_voice: opts.brandVoice,
                        include_strategy: false,
                        previous_context: null,
                        selected_outputs: outputs,
                        target_company: opts.targetCompany
                    }, $(`#${msgId}`), opts.platforms, opts.attachedImage, outputs.join(', '), outputs);
                    return;
                }

                const targets = r.targets || [];
                const content = {};
                opts.platforms.forEach(p => {
                    const prev = base.content[p] || {};
                    const merged = Object.assign({}, prev, r.content[p] || {});
                    if (!targets.includes('caption')) merged.caption = prev.caption;
                    merged.compliance = targets.includes('caption') ? (r.content[p]?.compliance || null) : prev.compliance;
                    if (!targets.includes('hashtags')) merged.hashtags = prev.hashtags;
                    content[p] = merged;
                });

                // An edit of a command's images stays a command result
                const baseH = window.chatHistory[base.msgId] || {};
                if (base.content && base.content._preset) content._preset = base.content._preset;
                if (!window.chatHistory[msgId]) {
                    window.chatHistory[msgId] = {
                        responses: [],
                        currentIndex: 0,
                        requestBody: { story: opts.instruction, tone: opts.tone, brand_voice: opts.brandVoice },
                        platforms: opts.platforms,
                        activeImgPath: null,
                        mediaType: 'none',
                        selectedOutputs: [],
                        refine: opts,
                        preset: baseH.preset,
                        presetRun: baseH.presetRun
                    };
                }
                const h = window.chatHistory[msgId];
                h.responses.push({ content: content, runId: r.run_id, usage: r.usage, agentsExecuted: r.agents_executed, qualitySummary: base.qualitySummary });
                h.currentIndex = h.responses.length - 1;
                lastRunId = r.run_id || lastRunId;

                renderAssistantResponse(msgId);
                adoptAsRefineBase(msgId);
                (r.media_errors || []).forEach(e => showToast('Media refinement failed - ' + e, 'error'));
                renderHistory();
                loadUserUsageMetrics();
                scrollToBottom();
            },
            error: function (xhr, status) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                if (status === 'abort') {
                    assistantElem.remove();
                    showToast('Refinement cancelled', 'info');
                    return;
                }
                const res = xhr.responseJSON || {};
                const errText = res.error || 'Refinement failed';
                assistantElem.find('.assistant-card').html(`
                    <div class="alert alert-${res.credit_limit_exceeded ? 'warning' : 'danger'} mb-0">
                        <i class="fas fa-exclamation-triangle me-2"></i><strong>Error:</strong> ${escapeHtml(errText)}
                    </div>
                `);
                if (res.credit_limit_exceeded) openCreditRequestModal();
                showToast('Refinement error: ' + errText, 'error');
            }
        });
    }

    function runResearchThenAsk(msgId, story, targetCompany, activeImgPath, tone, brandVoice) {
        const assistantElem = appendResearchThinking(msgId);

        $.ajax({
            url: '/api/analyze-story',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: story, target_company: targetCompany, previous_context: lastAssistantContext }),
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
                        <span class="coming-soon" title="Coming soon"><label class="media-chip-sm"><input type="checkbox" value="video" disabled><span class="chip-content"><i class="fas fa-video me-1 text-purple"></i>Video<span class="soon-tag">Soon</span></span></label></span>
                    </div>
                </div>
                <button type="button" class="btn btn-primary btn-sm mt-2 research-generate-btn" id="${msgId}_researchGenerateBtn">
                    <i class="fas fa-bolt me-1"></i>Generate Content
                </button>
            </div>
        `;
        assistantElem.find('.assistant-card').html(cardContent);

        // This card's Image option follows today's image limit too
        if (window.__imageQuota) window.applyImageQuota(window.__imageQuota);
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
                include_strategy: false,
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
        if (historyObj.preset || (historyObj.responses[historyObj.currentIndex]?.content || {})._preset) {
            renderPresetResponse(msgId);
            return;
        }
        const rData = historyObj.responses[historyObj.currentIndex];
        const { requestBody, platforms, selectedOutputs } = historyObj;
        const { content, runId, usage, agentsExecuted, qualitySummary } = rData;
        const elem = $(`#${msgId}`);
        // Badges for Token Usage & Memory Context
        // Only real numbers - nothing is shown when a value isn't known (e.g. history items)
        const hasUsage = usage && usage.total_tokens;
        const totalTokens = hasUsage ? Number(usage.total_tokens).toLocaleString() : '';
        const costUsd = hasUsage ? '$' + Number(usage.cost_usd || 0).toFixed(4) : '';
        const memCount = usage?.memories_referenced || 0;
        const agentsCount = agentsExecuted?.length || 0;
        const checksTotal = qualitySummary?.checks_total || 0;

        const qualityBadgeHtml = checksTotal
            ? `<span class="badge-quality-tag me-1" title="Automated quality checks: length, no CTAs or placeholders, plain text, hashtag limits"><i class="fas fa-check-double text-success me-1"></i>${qualitySummary.checks_passed}/${checksTotal} checks passed</span>`
            : '';
        const pipelineBadgeHtml = agentsCount
            ? `<button class="btn-agent-pipeline-toggle me-1" id="${msgId}_pipeline_btn" title="View executed agents"><i class="fas fa-network-wired me-1"></i>${agentsCount} Agents Active</button>`
            : '';
        const costBadgeHtml = hasUsage
            ? `<span class="badge-cost-tag me-1" id="${msgId}_cost_badge" title="Tokens & USD cost - text generation plus any images">${costBadgeInner(usage)}</span>`
            : '';
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

            const tagList = getTagList(pData.hashtags);
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

                    ${buildComplianceHtml(pData.compliance)}

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
                        ${pData.media?.image?.limit_reached ? `<div id="${msgId}_media_image_${p}">${imageLimitCardHtml()}</div>`
                            : pData.media?.image?.url ? `<div id="${msgId}_media_image_${p}">${buildGeneratedMediaHtml('image', pData.media.image, msgId, p)}</div>`
                            : (selectedOutputs || []).includes('image') ? `
                        <div id="${msgId}_media_image_${p}">
                            ${imageCreatingHtml(platforms.length > 1 || p === 'instagram')}
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
                    <div><i class="fas fa-sparkles text-primary me-1"></i>AVIR Studio Output</div>
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
                <span class="coming-soon" title="Coming soon">
                    <button class="btn btn-sm btn-outline-success btn-schedule-post" data-msg="${msgId}" disabled>
                        <i class="fas fa-calendar-plus me-1"></i>Schedule<span class="soon-tag">Soon</span>
                    </button>
                </span>
                <button class="btn btn-sm btn-outline-primary btn-regenerate" data-msg="${msgId}">
                    <i class="fas fa-sync-alt me-1"></i>Regenerate
                </button>
                <button class="btn btn-sm btn-outline-secondary btn-refine-base" data-msg="${msgId}"
                    title="Your next message will edit this version">
                    <i class="fas fa-wand-magic-sparkles me-1"></i>${refineBaseMsgId === msgId ? 'Refining this version' : 'Refine this version'}
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
                if (refineBaseMsgId === mId) adoptAsRefineBase(mId);
                renderAssistantResponse(mId);
            }
        });

        elem.find('.gen-next').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (h && h.currentIndex < h.responses.length - 1) {
                h.currentIndex++;
                if (refineBaseMsgId === mId) adoptAsRefineBase(mId);
                renderAssistantResponse(mId);
            }
        });

        // Pick which version the next follow-up message edits (e.g. go back
        // to an earlier image if the latest refinement made it worse).
        elem.find('.btn-refine-base').on('click', function () {
            const pickedMsg = $(this).attr('data-msg');
            adoptAsRefineBase(pickedMsg);
            const ph = window.chatHistory[pickedMsg];
            const pContent = ph ? ph.responses[ph.currentIndex].content || {} : {};
            const imageId = ph && ph.platforms.map(p => pContent[p]?.media?.image?.asset_id).find(Boolean);
            if (currentConversationId && imageId) {
                $.ajax({
                    url: `/api/conversations/${currentConversationId}/active-image`,
                    type: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ image_id: imageId })
                });
            }
            storyInput.focus();
            showToast('Your next message will refine this version.', 'info');
        });

        // Bind Regenerate
        elem.find('.btn-regenerate').on('click', function () {
            const mId = $(this).attr('data-msg');
            const h = window.chatHistory[mId];
            if (h && h.refine) {
                // A refinement re-applies the same instruction to the same base version
                const assistantElem = $(`#${mId}`);
                assistantElem.replaceWith(appendRefineThinking(mId));
                runRefine(mId, h.refine, $(`#${mId}`));
                return;
            }
            if (h) {
                const assistantElem = $(`#${mId}`);
                const hasImage = !!h.activeImgPath;
                assistantElem.replaceWith(appendAssistantThinking(mId, hasImage));
                // New text, same image: the version being viewed keeps its image and
                // video (the image's own "New image" button replaces it on request)
                const viewed = h.responses[h.currentIndex] || {};
                const body = viewed.runId ? Object.assign({}, h.requestBody, { keep_media_from_run_id: viewed.runId }) : h.requestBody;
                executeGeneration(mId, body, $(`#${mId}`), h.platforms, h.activeImgPath, h.mediaType, h.selectedOutputs);
            }
        });

        // Bind Schedule - opens the schedulePostModal (see
        // $('#confirmSchedulePostBtn') below) for this chat message's
        // run/story. Currently disabled in the UI ("Coming soon"). Also always
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
            // Images: ONE square image for the whole post, shown on every
            // platform (a 1:1 image works on Instagram, Facebook and LinkedIn),
            // instead of a separately paid image per platform. Each platform
            // can still ask for its own ("Separate image for ...").
            const needImage = selectedOutputs.includes('image')
                ? platforms.filter(p => !((content[p] || {}).media?.image?.url || (content[p] || {}).media?.image?.limit_reached))
                : [];
            if (needImage.length) {
                const first = needImage[0];
                const fData = content[first] || {};
                const fCaption = fData.caption?.primary_caption || requestBody.story;
                triggerMediaGenInChat(first, fCaption, 'image', requestBody.tone, runId, historyObj.activeImgPath,
                    `${msgId}_media_image_${first}`, fData.media_prompt || fCaption, msgId, needImage.slice(1));
            }
            platforms.forEach(p => {
                const pData = content[p] || {};
                const pCaption = pData.caption?.primary_caption || requestBody.story;
                // media_prompt (see api/routes.py) folds the Research Summary's
                // imagery descriptions & research notes in alongside the caption,
                // so image/video generation actually reflects the research shown
                // to the user - not just the short social caption text.
                const pMediaPrompt = pData.media_prompt || pCaption;
                if (selectedOutputs.includes('video') && !pData.media?.video?.url) {
                    triggerMediaGenInChat(p, pCaption, 'video', requestBody.tone, runId, historyObj.activeImgPath, `${msgId}_media_video_${p}`, pMediaPrompt, msgId);
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

    // ChatGPT-style "creating image" placeholder: the shape of the coming image
    // (square or 16:9) with slowly moving colour blobs, a shimmering label and
    // an elapsed-time counter. Replaced by the image when it arrives.
    function imageCreatingHtml(square, label) {
        return `
            <div class="img-creating${square ? '' : ' is-wide'}" data-started="${Date.now()}" role="status" aria-live="polite">
                <div class="img-creating-blobs" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
                <div class="img-creating-grain" aria-hidden="true"></div>
                <div class="img-creating-caption">
                    <span class="img-creating-label">${escapeHtml(label || 'Creating image')}</span>
                    <span class="img-creating-time">0s</span>
                </div>
                <div class="img-creating-hint">Usually takes about a minute</div>
            </div>`;
    }
    setInterval(function () {
        $('.img-creating').each(function () {
            const secs = Math.floor((Date.now() - Number(this.dataset.started || Date.now())) / 1000);
            $(this).find('.img-creating-time').text(secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`);
            if (secs >= 75) $(this).find('.img-creating-hint').text('Almost there - larger images take a little longer');
        });
    }, 1000);

    // ── Daily image limit (Admin -> Image access) ──────────────────────
    // "Images today: 1 of 2" next to the Image option; at the limit the Image
    // option is switched off and its tooltip says when the limit resets.
    window.__imageQuota = null;

    function resetsInText(seconds) {
        const s = Math.max(0, Number(seconds || 0));
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
        return h ? `${h} h ${m} m` : `${m} m`;
    }

    function imageLimitText(q) {
        return `You've used your ${q.limit} image${q.limit === 1 ? '' : 's'} for today. `
            + `Your limit resets in ${resetsInText(q.resets_in_seconds)} (midnight UTC). For more, contact your admin.`;
    }

    window.applyImageQuota = function (q) {
        if (!q) return;
        window.__imageQuota = q;
        const $badge = $('#imageQuotaBadge');
        const blocked = !q.unlimited && q.remaining <= 0;
        if (q.unlimited) {
            $badge.html('<i class="fas fa-infinity me-1"></i>Unlimited images').attr('title', 'No daily image limit');
        } else {
            $badge.html(`<i class="fas fa-image me-1"></i>Images today: ${q.used} of ${q.limit}`)
                .attr('title', blocked ? imageLimitText(q) : `${q.remaining} left today - resets in ${resetsInText(q.resets_in_seconds)}`);
        }
        $badge.removeClass('d-none').toggleClass('is-empty', blocked).toggleClass('is-low', !blocked && !q.unlimited && q.remaining === 1);
        // Every Image option on the page (composer + research cards)
        $('input[type=checkbox][value=image]').each(function () {
            const $label = $(this).closest('label');
            if (blocked) {
                $(this).prop('checked', false).prop('disabled', true).trigger('change');
                $label.addClass('quota-blocked').attr('title', imageLimitText(q));
            } else if ($label.hasClass('quota-blocked')) {
                $(this).prop('disabled', false);
                $label.removeClass('quota-blocked').removeAttr('title');
            }
        });
        $('.image-limit-card .image-limit-text').text(imageLimitCardText(q));
        updateGenerateGate();
    };

    // Generate is off while today's images are used up and the request needs
    // one: the Image option is ticked, or the follow-up refines a post that has
    // an image. Text-only posts (e.g. after "New Conversation") still work.
    function imageGateBlocked() {
        const q = window.__imageQuota;
        if (!q || q.unlimited || q.remaining > 0) return false;
        if ($('input[name="outputOptions"][value="image"]:checked').length) return true;
        const h = refineBaseMsgId ? window.chatHistory && window.chatHistory[refineBaseMsgId] : null;
        if (!h) return false;
        const content = (h.responses[h.currentIndex] || {}).content || {};
        return h.platforms.some(p => content[p]?.media?.image);
    }

    function updateGenerateGate() {
        const presetReason = presetBlockReason();
        const gated = !activePreset && imageGateBlocked();  // a command checks its own limit
        const busy = $('#dropZone').hasClass('dock-disabled');
        const q = window.__imageQuota;
        $('#generateBtn').prop('disabled', busy || gated || imageUploading || !!presetReason).toggleClass('quota-gated', gated)
            .attr('title', presetReason || (gated ? imageLimitText(q)
                : (imageUploading ? 'Wait until the image has finished uploading' : 'Send & Generate Content')));
        renderPresetBar();
        $('#imageLimitDockNote').toggleClass('d-none', !gated).html(gated ? `
            <i class="fas fa-hourglass-half"></i>
            <span><strong>Daily image limit reached.</strong> This post has an image, so refining it resumes in
            ${resetsInText(q.resets_in_seconds)} (midnight UTC). For a text-only post,
            <a href="#" id="imageLimitNewChat">start a new conversation</a>.</span>` : '');
    }

    $(document).on('click', '#imageLimitNewChat', function (e) {
        e.preventDefault();
        startNewChat();
    });

    function loadImageQuota() {
        $.getJSON('/api/me/image-quota').done(r => window.applyImageQuota(r && r.quota));
    }
    window.loadImageQuota = loadImageQuota;

    // The image slot when the server refused because today's limit is used up
    function imageLimitCardText(q) {
        q = q || window.__imageQuota;
        return q && !q.unlimited && q.remaining <= 0 ? imageLimitText(q)
            : "This image wasn't changed because the daily image limit had been reached. You have images again - ask for the change again to create it.";
    }

    function imageLimitCardHtml(q) {
        const text = imageLimitCardText(q);
        return `
            <div class="image-limit-card" role="status">
                <div class="image-limit-icon"><i class="fas fa-hourglass-half"></i></div>
                <div>
                    <div class="image-limit-title">Daily image limit reached</div>
                    <div class="image-limit-text">${escapeHtml(text)}</div>
                </div>
            </div>`;
    }

    // ── Image commands: product photo + /3dbillboard, /metaad, ... ─────────
    // Typing "/" opens the command menu; the chosen command shows as a chip
    // above the box. Every image a command makes counts toward the daily
    // limit, which is checked before anything is sent (api/routes.py).
    // (state: imagePresets, activePreset, presetAllSizes, uploadedImageAnalysis, slash - declared at the top)
    const defaultPlaceholder = $('#storyInput').attr('placeholder');

    function loadImagePresets() {
        $.getJSON('/api/image-presets').done(r => { imagePresets = (r && r.presets) || []; });
    }

    function presetById(id) { return imagePresets.find(p => p.id === id) || null; }
    function presetImageCount(p, allSizes) {
        if (!p) return 0;
        return (allSizes ?? presetAllSizes) && p.all_sizes_images ? p.all_sizes_images : p.images;
    }
    function presetSizeLabels(p, allSizes) {
        return ((allSizes && p.all_sizes && p.all_sizes.length) ? p.all_sizes : p.sizes) || [];
    }
    // The attached photo, else the last image in this chat
    function presetProductImage() { return uploadedImagePath || threadActiveImagePath || null; }

    function productNotesFromAnalysis() {
        const a = uploadedImageAnalysis || {};
        const colors = Array.isArray(a.colors) ? a.colors.join(', ') : (a.colors || '');
        return [a.rich_description || a.raw_caption || '', colors ? `Colours: ${colors}` : ''].filter(Boolean).join(' ').slice(0, 600);
    }

    function presetBlockReason() {
        if (!activePreset) return null;
        if (activePreset.requires_image && !presetProductImage()) return `Attach a product photo to use ${activePreset.command}`;
        const q = window.__imageQuota;
        const need = presetImageCount(activePreset);
        if (q && !q.unlimited && q.remaining < need) {
            return q.remaining <= 0 ? imageLimitText(q)
                : `${activePreset.command} needs ${need} images and you have ${q.remaining} left today`;
        }
        return null;
    }

    // ── "/" menu ──
    function slashQuery() {
        const m = /^\/([\w-]*)$/.exec(storyInput.val());  // only while the box holds just "/word"
        return m ? m[1].toLowerCase() : null;
    }

    function closeSlashMenu() {
        slash.open = false;
        $('#slashMenu').addClass('d-none').empty();
        storyInput.attr('aria-expanded', 'false');
    }

    function renderSlashMenu() {
        const q = slashQuery();
        if (q === null || !imagePresets.length) { closeSlashMenu(); return; }
        slash.matches = imagePresets.filter(p => p.id.startsWith(q) || p.label.toLowerCase().includes(q));
        slash.index = Math.min(slash.index, Math.max(0, slash.matches.length - 1));
        const items = slash.matches.map((p, i) => `
            <button type="button" class="slash-item${i === slash.index ? ' active' : ''}" role="option"
                    aria-selected="${i === slash.index}" data-id="${escapeAttr(p.id)}" id="slashItem${i}">
                <span class="slash-icon"><i class="fas ${escapeAttr(p.icon)}"></i></span>
                <span class="slash-text">
                    <span class="slash-cmd">${escapeHtml(p.command)}</span><span class="slash-label">${escapeHtml(p.label)}</span>
                    <span class="slash-desc">${escapeHtml(p.description)}</span>
                </span>
                <span class="slash-meta">${p.images} image${p.images === 1 ? '' : 's'}${p.ad_copy ? ' + ad copy' : ''}</span>
            </button>`).join('');
        $('#slashMenu').html(`
            <div class="slash-menu-head"><span>Image commands</span><span class="slash-keys">↑↓ choose · Enter use · Esc close</span></div>
            ${items || `<div class="slash-empty">No command matches "/${escapeHtml(q)}"</div>`}`).removeClass('d-none');
        slash.open = true;
        storyInput.attr('aria-expanded', 'true').attr('aria-activedescendant', items ? `slashItem${slash.index}` : '');
    }

    function choosePreset(id, rest) {
        const p = presetById(id);
        if (!p) return;
        activePreset = p;
        presetAllSizes = false;
        storyInput.val(rest || '').trigger('input');
        closeSlashMenu();
        storyInput.attr('placeholder', p.placeholder).trigger('focus');
        updateGenerateGate();
    }

    window.clearPreset = function () {
        activePreset = null;
        presetAllSizes = false;
        storyInput.attr('placeholder', defaultPlaceholder);
        updateGenerateGate();
    };

    function renderPresetBar() {
        const $bar = $('#presetBar');
        if (!activePreset) { $bar.addClass('d-none').empty(); return; }
        const p = activePreset;
        const need = presetImageCount(p);
        const q = window.__imageQuota;
        const reason = presetBlockReason();
        const sizes = p.all_sizes_images ? `
            <div class="preset-sizes" role="group" aria-label="Image sizes">
                <button type="button" class="preset-size${presetAllSizes ? '' : ' active'}" data-all="0" aria-pressed="${!presetAllSizes}">${escapeHtml(p.sizes.join(', '))} · ${p.images} image</button>
                <button type="button" class="preset-size${presetAllSizes ? ' active' : ''}" data-all="1" aria-pressed="${presetAllSizes}">All ${p.all_sizes_images} sizes · ${p.all_sizes_images} images</button>
            </div>` : '';
        let status;
        if (p.requires_image && !presetProductImage()) {
            status = '<button type="button" class="preset-status warn" id="presetAttachBtn"><i class="fas fa-image me-1"></i>Attach a product photo</button>';
        } else if (reason) {
            status = `<span class="preset-status bad"><i class="fas fa-hourglass-half me-1"></i>${escapeHtml(reason)}</span>`;
        } else {
            status = `<span class="preset-status">${need} image${need === 1 ? '' : 's'}${p.ad_copy ? ' + ad copy' : ''}`
                + `${q && !q.unlimited ? ` · ${q.remaining} left today` : ''}`
                + `${!uploadedImagePath && threadActiveImagePath ? ' · uses the last image in this chat' : ''}</span>`;
        }
        $bar.html(`
            <span class="preset-chip"><i class="fas ${escapeAttr(p.icon)}"></i>${escapeHtml(p.command)}
                <span class="preset-chip-label">${escapeHtml(p.label)}</span>
                <button type="button" class="preset-chip-x" onclick="clearPreset()" aria-label="Remove ${escapeAttr(p.label)}">&times;</button>
            </span>${sizes}${status}`).removeClass('d-none');
    }

    storyInput.on('input', renderSlashMenu);
    storyInput.on('blur', () => setTimeout(closeSlashMenu, 150));
    // Capture phase: runs before the box's Enter-to-send handler
    storyInput[0].addEventListener('keydown', function (e) {
        if (slash.open) {
            if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && slash.matches.length) {
                e.preventDefault();
                const n = slash.matches.length;
                slash.index = (slash.index + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
                renderSlashMenu();
            } else if ((e.key === 'Enter' || e.key === 'Tab') && slash.matches.length && !e.shiftKey) {
                e.preventDefault();
                e.stopImmediatePropagation();
                choosePreset(slash.matches[slash.index].id);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closeSlashMenu();
            }
            return;
        }
        // Backspace in an empty box removes the command chip
        if (e.key === 'Backspace' && activePreset && !storyInput.val()) window.clearPreset();
    }, true);
    $(document).on('mousedown', '.slash-item', function (e) {
        e.preventDefault();  // keep focus in the box
        choosePreset($(this).data('id'));
    });
    $(document).on('click', '.preset-size', function () {
        presetAllSizes = $(this).data('all') === 1 || $(this).data('all') === '1';
        updateGenerateGate();
    });
    $(document).on('click', '#presetAttachBtn', () => $('#imageInput').trigger('click'));

    // ── Running a command ──
    function presetThinkingHtml(msgId, p, labels) {
        return `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar">${assistantAvatarSvg()}</div>
                <div class="assistant-card">
                    <div class="assistant-header"><div class="assistant-title"><i class="fas ${escapeAttr(p.icon)}"></i>${escapeHtml(p.label)}</div></div>
                    <div class="preset-grid${labels.length > 1 ? ' multi' : ''}">
                        ${labels.map(l => `<div class="preset-tile">${imageCreatingHtml(!/16:9/.test(l), 'Creating ' + l)}</div>`).join('')}
                    </div>
                </div>
            </div>`;
    }

    // opts.msgId: Regenerate an existing reply (adds a version to it)
    function runPreset(text, opts) {
        opts = opts || {};
        const p = opts.preset || activePreset;
        if (!p) return;
        if (!opts.msgId) {
            const reason = presetBlockReason();
            if (reason) { showToast(reason, 'warning'); return; }
        }
        const imagePath = opts.imagePath || presetProductImage();
        const allSizes = opts.allSizes != null ? opts.allSizes : presetAllSizes;
        const notes = opts.productNotes != null ? opts.productNotes : productNotesFromAnalysis();
        const labels = presetSizeLabels(p, allSizes);
        let msgId = opts.msgId;
        if (!msgId) {
            $('#welcomeHero').addClass('d-none');
            messageCounter++;
            msgId = 'msg_' + Date.now() + '_' + messageCounter;
            appendUserMessage(`${p.command}${text ? ' ' + text : ''}`, uploadedImagePath, [], null, p.label, 'Standard Enterprise');
            storyInput.val('').trigger('input');
            clearAttachment();
            window.clearPreset();
            $('#chatThread').append(presetThinkingHtml(msgId, p, labels));
        } else {
            $(`#${msgId}`).replaceWith(presetThinkingHtml(msgId, p, labels));
        }
        scrollToBottom();
        setChatDockDisabled(true);
        const prior = window.chatHistory[msgId];
        window.currentGenerationRequest = $.ajax({
            url: '/api/presets/run',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                preset: p.id, image_path: imagePath, text: text, all_sizes: allSizes, product_notes: notes,
                conversation_id: currentConversationId,
                version_of_run_id: prior && prior.responses.length ? prior.responses[0].runId : undefined
            }),
            success: function (r) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                if (r.conversation_id) setConversation(r.conversation_id);
                if (r.quota) window.applyImageQuota(r.quota);
                if (!window.chatHistory[msgId]) {
                    window.chatHistory[msgId] = {
                        responses: [], currentIndex: 0,
                        requestBody: { story: `${p.command} ${text}`.trim(), platforms: r.preset.keys, tone: 'Auto' },
                        platforms: r.preset.keys, activeImgPath: null, mediaType: 'image', selectedOutputs: ['image'],
                        preset: { id: p.id, command: p.command, label: p.label, icon: p.icon, text: text, imagePath: imagePath, allSizes: allSizes, productNotes: notes },
                        presetRun: r.preset
                    };
                }
                const h = window.chatHistory[msgId];
                h.platforms = r.preset.keys;
                h.presetRun = r.preset;
                h.responses.push({ content: r.content, runId: r.run_id, usage: r.usage, agentsExecuted: null, qualitySummary: null });
                h.currentIndex = h.responses.length - 1;
                lastRunId = r.run_id || lastRunId;
                renderAssistantResponse(msgId);
                adoptAsRefineBase(msgId);
                (r.errors || []).forEach(e => showToast('An image could not be created - ' + e, 'error'));
                renderHistory();
                loadUserUsageMetrics();
                scrollToBottom();
            },
            error: function (xhr, status) {
                window.currentGenerationRequest = null;
                setChatDockDisabled(false);
                if (status === 'abort') { $(`#${msgId}`).remove(); return; }
                const body = xhr.responseJSON || {};
                let inner;
                if (xhr.status === 403 && body.code === 'image_limit_reached') {
                    if (body.quota) window.applyImageQuota(body.quota);
                    inner = `<div class="image-limit-card" role="status"><div class="image-limit-icon"><i class="fas fa-hourglass-half"></i></div>
                        <div><div class="image-limit-title">Not enough images left today</div><div class="image-limit-text">${escapeHtml(body.error || '')}</div></div></div>`;
                } else {
                    inner = `<div class="alert alert-warning mb-0"><i class="fas fa-triangle-exclamation me-2"></i>${escapeHtml(body.error || 'The images could not be created. Please try again.')}</div>`;
                }
                $(`#${msgId} .assistant-card`).html(`
                    <div class="assistant-header"><div class="assistant-title"><i class="fas ${escapeAttr(p.icon)}"></i>${escapeHtml(p.label)}</div></div>${inner}`);
            }
        });
    }

    // The reply of an image command: the images (exact sizes), ad copy for
    // /metaad, Regenerate, and "Refine this version" for chat edits.
    function renderPresetResponse(msgId) {
        const h = window.chatHistory[msgId];
        const r = h.responses[h.currentIndex];
        const content = r.content || {};
        const meta = content._preset || h.presetRun || {};
        const label = (h.preset && h.preset.label) || meta.label || 'Image command';
        const icon = (h.preset && h.preset.icon) || meta.icon || 'fa-wand-magic-sparkles';
        const total = h.responses.length, cur = h.currentIndex + 1;
        const pager = total > 1 ? `
            <div class="generation-pagination ms-2 d-inline-flex align-items-center bg-light rounded px-2 py-1 border">
                <i class="fas fa-chevron-left cursor-pointer text-secondary me-2 gen-prev" data-msg="${msgId}" ${cur === 1 ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
                <span class="small font-weight-bold">${cur} / ${total}</span>
                <i class="fas fa-chevron-right cursor-pointer text-secondary ms-2 gen-next" data-msg="${msgId}" ${cur === total ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
            </div>` : '';
        const tiles = h.platforms.map(k => {
            const img = content[k]?.media?.image;
            if (!img) return '';
            if (img.limit_reached) return `<div class="preset-tile">${imageLimitCardHtml()}</div>`;
            const sizeLabel = { feed: 'Feed', portrait: 'Feed', story: 'Story', billboard: 'Billboard', showcase: 'Showcase', lifestyle: 'Lifestyle', catalog: 'Catalog', festive: 'Festive' }[k] || capitalize(k);
            const name = `${sizeLabel}${img.aspect ? ' ' + img.aspect : ''}`;
            return `
                <figure class="preset-tile" ${img.aspect ? `data-aspect="${escapeAttr(img.aspect)}"` : ''}>
                    <img class="media-output-img preset-img img-reveal" src="${escapeAttr(img.url)}" alt="${escapeAttr(label + ' - ' + name)}" loading="lazy">
                    <figcaption>
                        <span>${escapeHtml(name)}${img.width ? ` · ${img.width}×${img.height}` : ''}</span>
                        <span class="preset-tile-actions">
                            <button type="button" class="preset-mini" data-preview="${escapeAttr(img.url)}" title="Preview"><i class="fas fa-expand"></i></button>
                            <a class="preset-mini" href="${escapeAttr(img.url)}" download title="Download"><i class="fas fa-download"></i></a>
                        </span>
                    </figcaption>
                </figure>`;
        }).join('');
        const ad = meta.ad_copy;
        const adText = ad ? `${ad.primary_text}\n\nHeadline: ${ad.headline}\nDescription: ${ad.description}\nButton: ${ad.cta}` : '';
        const adHtml = ad ? `
            <div class="preset-adcopy">
                <div class="preset-adcopy-head"><span><i class="fab fa-meta me-1"></i>Ad copy</span>
                    <button type="button" class="preset-copy" data-copy="${escapeAttr(adText)}"><i class="fas fa-copy me-1"></i>Copy all</button></div>
                <dl>
                    <dt>Primary text</dt><dd>${escapeHtml(ad.primary_text)}</dd>
                    <dt>Headline</dt><dd><strong>${escapeHtml(ad.headline)}</strong></dd>
                    <dt>Description</dt><dd>${escapeHtml(ad.description)}</dd>
                    <dt>Button</dt><dd><span class="preset-cta">${escapeHtml(ad.cta)}</span></dd>
                </dl>
            </div>` : '';
        const usage = r.usage;
        const costHtml = usage && usage.cost_usd
            ? `<span class="badge-cost-tag me-1" id="${msgId}_cost_badge" title="Images${ad ? ' + ad copy' : ''}">${costBadgeInner(usage)}</span>` : '';
        const elem = $(`#${msgId}`);
        elem.find('.assistant-card').html(`
            <div class="assistant-header">
                <div class="assistant-title"><div><i class="fas ${escapeAttr(icon)} text-primary me-1"></i>${escapeHtml(label)}${meta.occasion ? ` · ${escapeHtml(meta.occasion)}` : ''}</div>${pager}</div>
                <div class="assistant-meta-tags">${costHtml}<span class="assistant-run-tag">${r.runId ? 'Run #' + r.runId : 'Generated'}</span></div>
            </div>
            ${meta.text ? `<div class="preset-brief"><i class="fas fa-quote-left me-1"></i>${escapeHtml(meta.text)}</div>` : ''}
            ${meta.occasion_auto ? `<div class="preset-brief"><i class="fas fa-calendar-day me-1"></i>Made for the next festival, <strong>${escapeHtml(meta.occasion)}</strong>. For another occasion, type e.g. <em>/festive Eid</em>.</div>` : ''}
            <div class="preset-grid${h.platforms.length > 1 ? ' multi' : ''}">${tiles}</div>
            ${adHtml}
            <div class="assistant-card-footer">
                <button class="btn btn-sm btn-outline-primary btn-preset-regen" data-msg="${msgId}"><i class="fas fa-sync-alt me-1"></i>Regenerate</button>
                <button class="btn btn-sm btn-outline-secondary btn-refine-base" data-msg="${msgId}" title="Your next message will edit this version">
                    <i class="fas fa-wand-magic-sparkles me-1"></i>${refineBaseMsgId === msgId ? 'Refining this version' : 'Refine this version'}</button>
            </div>
            <div class="preset-tip"><i class="fas fa-lightbulb me-1"></i>Want a change? Just type it - e.g. "make the background darker".</div>`);

        elem.find('.gen-prev, .gen-next').on('click', function () {
            h.currentIndex = Math.max(0, Math.min(h.responses.length - 1, h.currentIndex + ($(this).hasClass('gen-next') ? 1 : -1)));
            if (refineBaseMsgId === msgId) adoptAsRefineBase(msgId);
            renderAssistantResponse(msgId);
        });
        elem.find('[data-preview]').on('click', function () { window.openImagePreview($(this).data('preview')); });
        elem.find('.preset-copy').on('click', function () {
            navigator.clipboard.writeText($(this).data('copy')).then(() => showToast('Ad copy copied', 'success'));
        });
        elem.find('.btn-refine-base').on('click', function () {
            adoptAsRefineBase(msgId);
            const imageId = h.platforms.map(k => content[k]?.media?.image?.asset_id).find(Boolean);
            if (currentConversationId && imageId) {
                $.ajax({ url: `/api/conversations/${currentConversationId}/active-image`, type: 'POST',
                         contentType: 'application/json', data: JSON.stringify({ image_id: imageId }) });
            }
            storyInput.trigger('focus');
            showToast('Your next message will edit these images.', 'info');
        });
        elem.find('.btn-preset-regen').on('click', function () {
            const pr = h.preset || {};
            const p = presetById(pr.id) || { id: pr.id, command: '/' + pr.id, label: label, icon: icon, sizes: [], all_sizes: [] };
            runPreset(pr.text || '', { msgId: msgId, preset: p, imagePath: pr.imagePath, allSizes: !!pr.allSizes, productNotes: pr.productNotes || '' });
        });
    }

    const PLATFORM_NAMES = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube' };
    const platformName = p => PLATFORM_NAMES[p] || (p.charAt(0).toUpperCase() + p.slice(1));

    // A platform that was showing the shared image asks for its own (paid) one
    window.generateSeparateImage = function (msgId, platform) {
        const h = window.chatHistory[msgId];
        if (!h) return;
        const r = h.responses[h.currentIndex];
        const pData = (r.content || {})[platform] || {};
        const caption = pData.caption?.primary_caption || h.requestBody.story;
        $(`#${msgId}_media_image_${platform}`).html(
            imageCreatingHtml(platform === 'instagram', `Creating a ${platformName(platform)} image`));
        triggerMediaGenInChat(platform, caption, 'image', h.requestBody.tone, r.runId, h.activeImgPath,
            `${msgId}_media_image_${platform}`, pData.media_prompt || caption, msgId, []);
    };

    // Cost badge text: text generation + images (images arrive after the text,
    // so the badge is updated again when each one is done - see addMediaCost)
    function costBadgeInner(usage) {
        const media = Number(usage.media_cost_usd || 0);
        return `<i class="fas fa-bolt text-warning me-1"></i>${Number(usage.total_tokens || 0).toLocaleString()} tok | $${Number(usage.cost_usd || 0).toFixed(4)}`
            + (media > 0 ? `<span class="cost-media-note"> · incl. ${usage.media_count > 1 ? usage.media_count + ' images' : 'image'} $${media.toFixed(2)}</span>` : '');
    }

    // The server already adds the image's cost to the saved run (credits);
    // this keeps the message's badge and the credit meter in step with it.
    function addMediaCost(msgId, cost) {
        cost = Number(cost || 0);
        const h = msgId && window.chatHistory[msgId];
        if (!h || cost <= 0) return;
        const r = h.responses[h.currentIndex];
        r.usage = r.usage || {};
        r.usage.cost_usd = Number(r.usage.cost_usd || 0) + cost;
        r.usage.media_cost_usd = Number(r.usage.media_cost_usd || 0) + cost;
        r.usage.media_count = Number(r.usage.media_count || 0) + 1;
        $(`#${msgId}_cost_badge`).html(costBadgeInner(r.usage));
        loadUserUsageMetrics();
    }

    function triggerMediaGenInChat(platform, caption, mediaType, tone, runId, imagePath, targetSlotId, mediaPrompt, msgId, sharePlatforms) {
        const shareWith = mediaType === 'image' ? (sharePlatforms || []) : [];
        shareWith.forEach(p => $(`#${msgId}_media_image_${p}`).html(
            imageCreatingHtml(true, 'Creating one image for all platforms')));
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
                video_prompt: mediaType === 'video' ? mediaPrompt : undefined,
                share_with_platforms: shareWith.length ? shareWith : undefined
            }),
            success: function (res) {
                const slot = $('#' + targetSlotId);
                if (res.success && res.url) {
                    // Record the media on the message's own content, so a
                    // follow-up edits this image instead of regenerating it,
                    // and paging between versions doesn't re-trigger generation.
                    const h = msgId && window.chatHistory[msgId];
                    const sharedTargets = res.shared_platforms || [];
                    addMediaCost(msgId, res.cost);  // one charge, even when shared by several platforms
                    if (res.quota) window.applyImageQuota(res.quota);
                    [platform, ...sharedTargets].forEach(target => {
                        const pContent = h && h.responses[h.currentIndex].content[target];
                        if (pContent) {
                            pContent.media = pContent.media || {};
                            pContent.media[mediaType] = { url: res.url, clean_url: res.clean_url || null, prompt: res.prompt, resolution: res.resolution || res.size, asset_id: res.asset_id || null };
                        }
                    });
                    if (h && refineBaseMsgId === msgId) adoptAsRefineBase(msgId);
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
                    ` : buildGeneratedMediaHtml('image', { url: res.url, resolution: res.resolution || res.size }, msgId, platform, true);
                    slot.html(mediaHtml);
                    // The same image in the other platforms' slots, each able to get its own
                    sharedTargets.forEach(target => {
                        $(`#${msgId}_media_image_${target}`).html(mediaHtml + `
                            <div class="shared-image-note">
                                <span><i class="fas fa-link me-1"></i>Same image as ${escapeHtml(platformName(platform))} - one image for the whole post</span>
                                <button type="button" class="btn-copy-sm" onclick="generateSeparateImage('${msgId}', '${target}')">
                                    <i class="fas fa-wand-magic-sparkles me-1"></i>Separate image for ${escapeHtml(platformName(target))}
                                </button>
                            </div>`);
                    });
                } else {
                    const failHtml = `
                        <div class="alert alert-warning py-2 px-3 small mt-2">
                            <i class="fas fa-exclamation-circle me-1"></i>Media generation info: ${escapeHtml(res.error || 'Complete')}
                        </div>
                    `;
                    slot.html(failHtml);
                    shareWith.forEach(p => $(`#${msgId}_media_image_${p}`).html(failHtml));
                }
            },
            error: function (xhr) {
                const body = xhr.responseJSON || {};
                if (xhr.status === 403 && body.code === 'image_limit_reached') {
                    const card = imageLimitCardHtml(body.quota || window.__imageQuota || {});
                    $('#' + targetSlotId).html(card);
                    shareWith.forEach(p => $(`#${msgId}_media_image_${p}`).html(card));
                    window.applyImageQuota(body.quota);
                    return;
                }
                const errHtml = `
                    <div class="alert alert-danger py-2 px-3 small mt-2">
                        <i class="fas fa-exclamation-circle me-1"></i>Could not render media preview.
                    </div>
                `;
                $('#' + targetSlotId).html(errHtml);
                shareWith.forEach(p => $(`#${msgId}_media_image_${p}`).html(errHtml));
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
            url: `/api/conversations?limit=30&archived=${isArchived}`,
            type: 'GET',
            success: function (r) {
                const history = r.conversations || [];
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
            const countTag = item.message_count > 1
                ? `<span class="history-tone-tag" title="${item.message_count} messages in this conversation"><i class="fas fa-comments me-1"></i>${item.message_count}</span>` : '';
            const dateStr = item.timestamp || 'Recent';

            const actionBtn = isArchived
                ? `<button class="btn-history-icon btn-unarchive-item" data-id="${item.id}" title="Restore conversation"><i class="fas fa-box-open"></i></button>`
                : `<button class="btn-history-icon btn-archive-item" data-id="${item.id}" title="Archive conversation"><i class="fas fa-box-archive"></i></button>`;

            html += `
                <div class="history-card${Number(item.id) === currentConversationId ? ' active' : ''}" data-id="${item.id}">
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
                            ${countTag}
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
            loadConversation(id);
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
            url: `/api/conversations/${runId}/archive`,
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
            url: `/api/conversations/${runId}/unarchive`,
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

    // Opens a saved conversation: every message and reply is replayed in order
    // (regenerated replies become versions of their message), the reply with
    // the conversation's active image becomes what the next message refines,
    // and new messages keep going into this same conversation.
    function loadConversation(convId, opts) {
        opts = opts || {};
        $.ajax({
            url: `/api/conversations/${convId}`,
            type: 'GET',
            success: function (r) {
                const runs = r.runs || [];
                if (!runs.length) return;
                const conv = r.conversation || {};

                startNewChat(true);
                $('#welcomeHero').addClass('d-none');
                threadRootBrief = runs[0].story;

                const msgByRun = {};
                const order = [];
                runs.forEach(run => {
                    const content = run.content || {};
                    const meta = content._meta || {};
                    const platforms = Array.isArray(run.platforms) ? run.platforms : (run.platforms ? [run.platforms] : ['linkedin']);
                    const rData = {
                        content: content, runId: run.id,
                        usage: { total_tokens: run.tokens_used, cost_usd: run.cost_usd },
                        agentsExecuted: content._agents || null, qualitySummary: content._quality || null
                    };

                    // A regenerated reply: another version of that message
                    const ownerMsg = meta.version_of && msgByRun[meta.version_of];
                    if (ownerMsg) {
                        const owner = window.chatHistory[ownerMsg];
                        owner.responses.push(rData);
                        owner.currentIndex = owner.responses.length - 1;
                        msgByRun[run.id] = ownerMsg;
                        return;
                    }

                    messageCounter++;
                    const msgId = 'msg_' + Date.now() + '_' + messageCounter;
                    const presetMeta = content._preset || null;  // an image command's run
                    const msgPlatforms = presetMeta ? (presetMeta.keys || []) : platforms;
                    if (presetMeta) {
                        appendUserMessage(run.story, presetMeta.source_image_url || null, [], null, presetMeta.label, 'Standard Enterprise');
                    } else {
                        appendUserMessage(run.story, meta.image_url || null, platforms, run.tone, 'Text (Caption)', 'Standard Enterprise');
                    }
                    appendAssistantThinking(msgId, false);

                    // Show the media this reply already has (no new generation)
                    const savedOutputs = [];
                    msgPlatforms.forEach(p => {
                        const media = content[p]?.media || {};
                        if ((media.image?.url || media.image?.limit_reached) && !savedOutputs.includes('image')) savedOutputs.push('image');
                        if (media.video?.url && !savedOutputs.includes('video')) savedOutputs.push('video');
                    });
                    const h = {
                        responses: [rData],
                        currentIndex: 0,
                        requestBody: { story: run.story, platforms: msgPlatforms, tone: run.tone, brand_voice: 'Standard Enterprise' },
                        platforms: msgPlatforms,
                        activeImgPath: null,
                        mediaType: 'none',
                        selectedOutputs: savedOutputs
                    };
                    if (presetMeta) {
                        h.preset = { id: presetMeta.id, command: '/' + presetMeta.id, label: presetMeta.label, icon: presetMeta.icon,
                                     text: presetMeta.text || '', imagePath: presetMeta.source_image_url, allSizes: !!presetMeta.all_sizes, productNotes: '' };
                        h.presetRun = presetMeta;
                    }
                    // A refinement: Regenerate re-applies it to the version it refined
                    const baseMsg = meta.refined_from_run_id && msgByRun[meta.refined_from_run_id];
                    if (baseMsg) {
                        const bh = window.chatHistory[baseMsg];
                        const bResp = bh.responses.find(x => x.runId === meta.refined_from_run_id) || bh.responses[bh.currentIndex];
                        h.refine = {
                            instruction: run.story, platforms: platforms, tone: run.tone, brandVoice: 'Standard Enterprise',
                            selectedOutputs: [], attachedImage: null, targetCompany: 'None',
                            base: {
                                msgId: baseMsg, runId: meta.refined_from_run_id, platforms: bh.platforms.slice(),
                                content: JSON.parse(JSON.stringify(bResp.content || {})), qualitySummary: bResp.qualitySummary, context: null
                            }
                        };
                    }
                    window.chatHistory[msgId] = h;
                    msgByRun[run.id] = msgId;
                    order.push(msgId);
                });

                // The reply showing the active image is what "it" refers to
                let baseMsgId = order[order.length - 1];
                if (conv.active_image_id) {
                    order.forEach(id => {
                        const h = window.chatHistory[id];
                        h.responses.forEach((resp, idx) => {
                            const hit = h.platforms.some(p => resp.content?.[p]?.media?.image?.asset_id === conv.active_image_id);
                            if (hit) { baseMsgId = id; h.currentIndex = idx; }
                        });
                    });
                }

                order.forEach(id => renderAssistantResponse(id));
                setConversation(convId);
                lastRunId = runs[runs.length - 1].id;
                adoptAsRefineBase(baseMsgId);
                scrollToBottom();
                if (!opts.quiet) showToast('Conversation loaded - send a message to continue it.', 'info');
            },
            error: function (xhr) {
                if (xhr.status === 404) clearConversation();
                if (!opts.quiet) showToast('Could not load that conversation.', 'error');
            }
        });
    }

    // The tab's open conversation survives a page refresh
    function restoreConversation() {
        let saved = null;
        try { saved = sessionStorage.getItem(CONVERSATION_KEY); } catch (e) { /* storage blocked */ }
        if (saved && !$('#chatThread').children().length && !storyInput.val()) loadConversation(saved, { quiet: true });
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