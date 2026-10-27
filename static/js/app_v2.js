$(document).ready(function () {
    let uploadedImagePath = null;
    let draftOwner = null;            // who is signed in, for the draft of the brief (see restoreDraft)
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
        $('.cl-item').each(function () {
            $(this).toggleClass('active', Number($(this).data('id')) === currentConversationId);
        });
        if (typeof updateHeaderConversation === 'function') updateHeaderConversation();
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
    $('#sidebar').on('click', '.cl-row, #newChatBtn', function () { window.closeMobileDrawer(); });
    $(window).on('resize', function () { if (!isPhone()) setMobileDrawer(false); });

    // Phones: composer options (platforms, voice, tone, output) fold away
    // Composer "Style" button: brand voice + tone behind one menu. The button
    // names what was changed, so a non-default choice is never hidden.
    function updateStyleMenuLabel() {
        const tone = $('#toneSelect').val();
        const voice = $('#brandVoiceSelect').val();
        const language = $('#languageSelect').val();
        const parts = [];
        if (language && language !== 'en') parts.push($('#languageSelect option:selected').data('short'));
        if (voice && voice !== 'Standard Enterprise') parts.push(voice);
        if (tone) parts.push($('#toneSelect option:selected').text());
        $('#styleMenuLabel').text(parts.length ? parts.join(' · ') : 'Style');
        $('#styleMenuBtn').toggleClass('is-set', parts.length > 0);
        if (typeof updateComposerSummary === 'function') updateComposerSummary();
    }
    function setStyleMenuOpen(open) {
        $('#styleMenuPanel').toggleClass('d-none', !open);
        $('#styleMenuBtn').attr('aria-expanded', open ? 'true' : 'false').toggleClass('open', open);
    }
    $('#styleMenuBtn').on('click', function () { setStyleMenuOpen($('#styleMenuPanel').hasClass('d-none')); });
    $(document).on('click', function (e) {
        if (!$(e.target).closest('#styleMenu').length) setStyleMenuOpen(false);
    });
    $(document).on('keydown', function (e) {
        if (e.key === 'Escape' && !$('#styleMenuPanel').hasClass('d-none')) { setStyleMenuOpen(false); $('#styleMenuBtn').focus(); }
    });
    $('#toneSelect, #brandVoiceSelect, #languageSelect').on('change', updateStyleMenuLabel);
    updateStyleMenuLabel();

    // Post language: the menu's choices come from the server; the choice is
    // remembered in this browser, and every Generate / Refine request carries
    // it (a prefilter, so each request builder doesn't have to). The list is
    // English + the user's country's languages: the Country on the brand
    // profile page, else its markets, else the browser's country.
    const LANGUAGE_KEY = 'avir_post_language';
    function loadLanguages() {
        $.getJSON('/api/languages', { detected: detectCountry() }).done(function (r) {
            const $select = $('#languageSelect');
            const current = $select.val();
            $select.html((r.languages || []).map(l =>
                `<option value="${escapeAttr(l.code)}" data-short="${escapeAttr(l.short)}">${escapeHtml(l.name)}</option>`).join(''));
            let saved = current;
            try { saved = localStorage.getItem(LANGUAGE_KEY) || current; } catch (e) { /* storage blocked */ }
            // A language the country doesn't have falls back to English
            $select.val(saved && $select.find(`option[value="${saved}"]`).length ? saved : 'en');
            updateStyleMenuLabel();
        });
    }
    // The browser's country, for users without brand markets: the time zone
    // (more telling than the browser language, which is often en-US
    // everywhere), then the locale's region. The server ignores codes it
    // doesn't know.
    const TIMEZONE_COUNTRIES = {
        'Asia/Kolkata': 'IN', 'Asia/Calcutta': 'IN', 'Asia/Karachi': 'PK', 'Asia/Dhaka': 'BD',
        'Asia/Kathmandu': 'NP', 'Asia/Colombo': 'LK', 'Asia/Dubai': 'AE', 'Asia/Riyadh': 'SA',
        'Asia/Qatar': 'QA', 'Asia/Kuwait': 'KW', 'Asia/Bahrain': 'BH', 'Asia/Muscat': 'OM',
        'Asia/Amman': 'JO', 'Asia/Beirut': 'LB', 'Asia/Baghdad': 'IQ', 'Asia/Tehran': 'IR',
        'Asia/Jerusalem': 'IL', 'Europe/Istanbul': 'TR', 'Africa/Cairo': 'EG',
        'America/Toronto': 'CA', 'America/Vancouver': 'CA', 'America/Montreal': 'CA', 'America/Edmonton': 'CA',
        'America/Winnipeg': 'CA', 'America/Halifax': 'CA', 'America/Mexico_City': 'MX', 'America/Sao_Paulo': 'BR',
        'America/Argentina/Buenos_Aires': 'AR', 'America/Bogota': 'CO', 'America/Santiago': 'CL',
        'America/Lima': 'PE', 'America/Caracas': 'VE', 'Europe/London': 'GB', 'Europe/Dublin': 'IE',
        'Europe/Paris': 'FR', 'Europe/Berlin': 'DE', 'Europe/Madrid': 'ES', 'Europe/Lisbon': 'PT',
        'Europe/Rome': 'IT', 'Europe/Amsterdam': 'NL', 'Europe/Brussels': 'BE', 'Europe/Zurich': 'CH',
        'Europe/Vienna': 'AT', 'Europe/Stockholm': 'SE', 'Europe/Oslo': 'NO', 'Europe/Copenhagen': 'DK',
        'Europe/Helsinki': 'FI', 'Europe/Warsaw': 'PL', 'Europe/Prague': 'CZ', 'Europe/Budapest': 'HU',
        'Europe/Bucharest': 'RO', 'Europe/Athens': 'GR', 'Europe/Kyiv': 'UA', 'Europe/Kiev': 'UA',
        'Europe/Moscow': 'RU', 'Asia/Shanghai': 'CN', 'Asia/Hong_Kong': 'HK', 'Asia/Taipei': 'TW',
        'Asia/Tokyo': 'JP', 'Asia/Seoul': 'KR', 'Asia/Singapore': 'SG', 'Asia/Kuala_Lumpur': 'MY',
        'Asia/Jakarta': 'ID', 'Asia/Bangkok': 'TH', 'Asia/Ho_Chi_Minh': 'VN', 'Asia/Saigon': 'VN',
        'Asia/Manila': 'PH', 'Pacific/Auckland': 'NZ', 'Africa/Lagos': 'NG', 'Africa/Nairobi': 'KE',
        'Africa/Dar_es_Salaam': 'TZ', 'Africa/Addis_Ababa': 'ET', 'Africa/Johannesburg': 'ZA',
        'Africa/Casablanca': 'MA', 'Africa/Algiers': 'DZ', 'Africa/Tunis': 'TN',
    };
    function detectCountry() {
        let zone = '';
        try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* no Intl */ }
        if (TIMEZONE_COUNTRIES[zone]) return TIMEZONE_COUNTRIES[zone];
        if (zone.startsWith('Australia/')) return 'AU';
        if (/^(America\/(New_York|Chicago|Denver|Los_Angeles|Phoenix|Anchorage|Detroit|Boise|Indiana\/)|Pacific\/Honolulu)/.test(zone)) return 'US';
        const region = (navigator.languages || [navigator.language || '']).map(l => (l.split('-')[1] || '')).find(r => /^[A-Z]{2}$/i.test(r));
        return region ? region.toUpperCase() : '';
    }
    loadLanguages();
    $('#languageSelect').on('change', function () {
        try { localStorage.setItem(LANGUAGE_KEY, this.value); } catch (e) { /* storage blocked */ }
    });
    $.ajaxPrefilter(function (options) {
        if (!/^\/api\/(generate|refine)$/.test(options.url || '') || typeof options.data !== 'string') return;
        try {
            const body = JSON.parse(options.data);
            if (body && !body.language) {
                body.language = $('#languageSelect').val() || 'en';
                options.data = JSON.stringify(body);
            }
        } catch (e) { /* not JSON: leave it */ }
    });

    // Composer "Options": platforms, what to make and voice / tone / language
    // open in a panel above the toolbar. It always starts closed; the one-line
    // summary next to the button says what is picked (or "Pick a platform").
    function setComposerOptions(open) {
        $('#dropZone').toggleClass('options-open', open);
        $('#dockOptionsBtn').toggleClass('active', open).attr('aria-expanded', String(open));
    }
    window.openComposerOptions = function () { setComposerOptions(true); };
    $('#dockOptionsBtn').on('click', function () { setComposerOptions(!$('#dropZone').hasClass('options-open')); });

    function updateComposerSummary() {
        const names = $('.platform-chips-inline input:checked').map(function () {
            return $(this).closest('label').find('.chip-content').text().trim();
        }).get();
        const make = [];
        if ($('#mediaText').prop('checked')) make.push('caption');
        if ($('#mediaImage').prop('checked')) make.push('image');
        if ($('#mediaVideo').prop('checked')) make.push('video');
        const style = $('#styleMenuLabel').text().trim();
        const parts = [names.length ? names.join(', ') : 'Pick a platform', make.join(' + ') || 'caption'];
        if (style && style !== 'Style') parts.push(style);
        $('#composerSummary').text(parts.join(' · ')).toggleClass('is-missing', !names.length);
    }
    $('#dropZone').on('change', 'input, select', updateComposerSummary);
    setComposerOptions(false);
    try { localStorage.removeItem('avir_composer_options_open'); } catch (e) { /* storage blocked */ }  // old "remember open" choice
    setTimeout(updateComposerSummary, 400);  // after last time's choices are restored

    // Chat header: full at the top of the chat, a slim bar (with the
    // conversation's name) once the chat is scrolled. Two thresholds, so it
    // doesn't flicker at the edge.
    (function compactHeader() {
        const $ws = $('#chatWorkspace'), $bar = $('#chatHeaderBar');
        if (!$ws.length || !$bar.length) return;
        let compact = false, ticking = false;
        const update = () => {
            ticking = false;
            const top = $ws.scrollTop();
            const next = compact ? top > 12 : top > 64;
            if (next !== compact) { compact = next; $bar.toggleClass('is-compact', compact); }
        };
        $ws.on('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } });
        $('#chatHeaderTop').on('click', () => $ws.stop().animate({ scrollTop: 0 }, 250));
    })();
    function updateHeaderConversation() {
        const name = $('.cl-item.active .cl-title').first().text().trim();
        $('#chatHeaderConv').text(name ? ' · ' + name : '');
    }

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
                draftOwner = user.id || user.email || null;

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

    // "2 of 3 this week" in the header bar - the weekly posting goal from the
    // Content Calendar. Refreshed with the usage numbers after each generation.
    function loadGoalPill() {
        $.getJSON('/api/me/goal?tz=' + (-new Date().getTimezoneOffset())).done(function (r) {
            const p = r && r.progress;
            if (!p) return;
            const shown = Math.max(p.goal, Math.min(p.done, 7));
            $('#goalPillDots').html(Array.from({ length: shown }, (_, i) => `<i class="${i < p.done ? 'on' : ''}"></i>`).join(''));
            $('#goalPillText').text(p.goal_met ? `Goal reached: ${p.done} this week` : `${p.done} of ${p.goal} this week`);
            window.__weekProgress = p;  // for the "scheduled" card's goal line
            $('#goalPill').removeClass('d-none').toggleClass('is-met', !!p.goal_met)
                .attr('aria-label', `${p.done} of ${p.goal} posts created this week. Open your Content Calendar.`);
        });
    }

    // The credit request form asks in the user's own currency (rupees for
    // Indian brands); the server converts rupees to USD (/api/credit-requests)
    function applyCreditRequestCurrency(currency) {
        const inr = currency === 'INR';
        if ($('#creditRequestForm').data('currency') === currency) return;
        $('#creditRequestForm').data('currency', currency);
        const presets = inr ? [500, 1000, 2500, 5000] : [10, 25, 50, 100];
        const symbol = inr ? '₹' : '$';
        $('#requestedAmountLabel').text(inr ? 'Requested extension amount (₹):' : 'Requested extension amount (USD):');
        $('#requestedAmountSymbol').text(symbol);
        $('#requestedAmountInput').attr({ max: inr ? 100000 : 1000, step: 1 }).val(presets[0]);
        $('#requestedAmountPresets').html(presets.map(v =>
            `<button type="button" class="preset-btn" data-amount="${v}">+${symbol}${v.toLocaleString(inr ? 'en-IN' : 'en-US')}</button>`).join(''));
    }
    $(document).on('click', '#requestedAmountPresets .preset-btn', function () {
        $('#requestedAmountInput').val($(this).data('amount'));
    });

    function loadUserUsageMetrics() {
        loadGoalPill();
        $.ajax({
            url: '/api/metrics/usage',
            type: 'GET',
            success: function (r) {
                if (r.success) {
                    window.avirSetMoney(r);
                    applyCreditRequestCurrency(r.currency);
                    const formattedTokens = Number(r.total_tokens || 0).toLocaleString() + ' Tokens';
                    const formattedCost = window.avirMoney(r.total_cost_usd, r.currency === 'INR' ? 2 : 4);
                    const rem = window.avirMoney(r.remaining_credits);
                    const lim = window.avirMoney(r.credit_limit);
                    const used = window.avirMoney(r.used_credits);

                    $('#headerTokens').text(Number(r.total_tokens || 0).toLocaleString());
                    $('#headerCost').text(formattedCost);
                    $('#dropHeaderTokens').text(formattedTokens);
                    $('#dropHeaderCost').text(formattedCost);

                    $('#headerCreditsRemaining').text(rem);
                    $('#headerCreditLimit').text(lim);
                    if (r.credit_capped !== false) {  // paid plans show the plan there instead (user_menu.js)
                        $('#dropHeaderCreditsRemaining').text(rem);
                        $('#dropHeaderCreditLimit').text(lim);
                    }

                    $('#modalCreditLimit').text(lim);
                    $('#modalUsedCredits').text(used);
                    $('#modalRemainingCredits').text(rem);

                    // Update credit pill badge class
                    const pill = $('#headerCreditPill');
                    if (pill.length) {
                        pill.removeClass('warn danger');
                        if (r.remaining_credits <= 0) {
                            pill.addClass('danger');
                        } else if (r.remaining_credits < Number(r.credit_limit || 0) * 0.2) {
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
                        $('#pendingReqAmount').text(window.avirMoney(r.pending_request.requested_amount));
                    } else {
                        $('#pendingRequestNotice').addClass('d-none');
                    }
                }
            }
        });
    }

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
        if (takePendingImage() || openEmailedIdea()) restoreDraft(draftOwner);  // a new post: nothing to reopen
        else restoreConversation();
    });

    function openModalFromHash() {
        const hash = window.location.hash;
        if (hash === '#request-credit') {
            $('#dropRequestCreditBtn').trigger('click');
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
        $('#brandIdeasGrid').html(html);
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

        const shownTitles = $('#brandIdeasGrid .quick-prompt-card .quick-prompt-header span').map(function () { return $(this).text(); }).get();
        $btn.prop('disabled', true).find('i').addClass('fa-spin');
        $('#brandIdeasGrid').addClass('is-loading').css('opacity', 0.5);

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
                $('#brandIdeasGrid').removeClass('is-loading').css('opacity', '');
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
                if (r && r.company_name) _brandProfileCompanyName = r.company_name;  // also names the brand in Preview
                const ideas = (r && r.post_ideas) || [];
                if (!ideas.length) return;

                _brandProfileIdeaPool = ideas;
                window.setBriefExamples(ideas);
                // Pool is newest-first (regenerated ideas are prepended), so lead with the latest
                renderQuickPromptCards(ideas.slice(0, QUICK_PROMPT_CARD_COUNT));

                if (r.company_name) {
                    _brandProfileCompanyName = r.company_name;
                    $('.welcome-title').text(pickGreeting(r.company_name));
                    $('.welcome-desc').text('These ideas are grounded in your brand - pick one, or type your own brief below.');
                    $('#regenerateGreetingBtn').removeClass('d-none');
                    loadIdeaFeed(false);  // trending / post types / dates for their industry
                }
            }
        });
    }

    // ── "Ideas for you": trending in the user's industry, post types that
    // work, upcoming dates (/api/ideas/feed, services/trend_service.py). The
    // first feed of the day takes a minute or two to write, so the "From your
    // brand" cards stay up until it arrives; a card fills the composer and
    // picks its platform and output.
    const IDEA_GROUPS = {
        trending: { icon: 'fa-arrow-trend-up', empty: 'No trending topics found for your industry this week.' },
        playbook: { icon: 'fa-layer-group', empty: 'No post types yet - try refreshing.' },
        dates: { icon: 'fa-calendar-day', empty: 'No occasions in your markets in the next 30 days.' }
    };
    const IDEA_FORMATS = { text: ['fa-align-left', 'Text'], image: ['fa-image', 'Image'], video: ['fa-video', 'Video'] };
    const IDEA_PLATFORMS = {
        linkedin: ['fa-linkedin', 'LinkedIn'], instagram: ['fa-instagram', 'Instagram'],
        facebook: ['fa-facebook', 'Facebook'], youtube: ['fa-youtube', 'YouTube']
    };
    let _ideaFeed = null;
    let _ideaTabPicked = false;  // the user chose a tab: don't switch it under them

    function ideaCardHtml(idea, group) {
        const format = IDEA_FORMATS[idea.format] || IDEA_FORMATS.text;
        const platform = IDEA_PLATFORMS[idea.platform] || IDEA_PLATFORMS.linkedin;
        // One quiet line saying where the idea comes from: the news story, the post type or the date
        let origin = '';
        if (group === 'trending') {
            const name = escapeHtml(idea.source_title || 'This week');
            origin = `<i class="fas fa-arrow-trend-up"></i>In the news` + (idea.source_url
                ? ` · <a class="idea-source" href="${escapeAttr(idea.source_url)}" target="_blank" rel="noopener noreferrer" title="${escapeAttr(idea.topic || '')}">${name}<i class="fas fa-arrow-up-right-from-square"></i></a>`
                : (idea.source_title ? ` · ${name}` : ''));
        } else if (group === 'dates') {
            origin = `<i class="fas fa-calendar-day"></i>${escapeHtml(idea.occasion || 'Upcoming date')}`;
        } else {
            origin = `<i class="fas fa-layer-group"></i>${escapeHtml((idea.post_type || 'Post type').split(':')[0])}`;
        }
        return `
            <div class="idea-card" role="button" tabindex="0" data-prompt="${escapeAttr(idea.prompt)}"
                 data-platform="${escapeAttr(idea.platform)}" data-format="${escapeAttr(idea.format)}">
                <div class="idea-origin">${origin}</div>
                <h3 class="idea-title">${escapeHtml(idea.title)}</h3>
                <p class="idea-text">${escapeHtml(idea.summary || idea.why_now || idea.prompt)}</p>
                <div class="idea-foot">
                    <span class="idea-makes"><i class="fas ${format[0]}"></i>${format[1]} for <i class="fab ${platform[0]}"></i>${platform[1]}</span>
                    <span class="idea-use">Use this idea<i class="fas fa-arrow-right"></i></span>
                </div>
            </div>`;
    }

    function ideaFeedMetaText(feed, group) {
        const when = feed.trends_updated_at ? new Date(feed.trends_updated_at) : null;
        if (group === 'trending') {
            return `Built on this week's news in ${feed.industry}`
                + (when ? ` · updated ${when.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}` : '')
                + '. Click an idea to start a post.';
        }
        return group === 'dates'
            ? 'Occasions coming up in your markets. Click an idea to start a post.'
            : 'Formats that work for your industry, any week. Click an idea to start a post.';
    }

    function showIdeaGroup(group) {
        $('.idea-tab').each(function () {
            const on = $(this).data('group') === group;
            $(this).toggleClass('active', on).attr('aria-selected', on ? 'true' : 'false');
        });
        const isBrand = group === 'brand';
        $('#brandIdeasSection').toggleClass('d-none', !isBrand);
        $('#ideaFeedGrid').toggleClass('d-none', isBrand);
        $('#refreshIdeaFeedBtn').toggleClass('d-none', isBrand || !_ideaFeed);
        if (isBrand) return $('#ideaFeedMeta').addClass('d-none');
        if (!_ideaFeed) {
            $('#ideaFeedMeta').addClass('d-none');
            $('#ideaFeedMeta').text('Finding what your industry is talking about this week. The first look of the day takes a minute or two.').removeClass('d-none');
            return $('#ideaFeedGrid').html('<div class="idea-card is-loading" aria-hidden="true"><span></span><span></span><span></span></div>'.repeat(4));
        }
        const ideas = (_ideaFeed.groups || {})[group] || [];
        const meta = ideaFeedMetaText(_ideaFeed, group);
        $('#ideaFeedMeta').text(meta).toggleClass('d-none', !ideas.length);
        $('#ideaFeedGrid').html(ideas.length
            ? ideas.map(idea => ideaCardHtml(idea, group)).join('')
            : `<div class="idea-feed-empty">${IDEA_GROUPS[group].empty}</div>`);
    }

    function loadIdeaFeed(refresh) {
        $('#ideaFeedBar').removeClass('d-none');
        const $busy = $('.idea-tab-busy').addClass('on');
        const $btn = $('#refreshIdeaFeedBtn').prop('disabled', true);
        $btn.find('i').addClass('fa-spin');
        $.getJSON('/api/ideas/feed' + (refresh ? '?refresh=1' : '')).done(function (r) {
            const feed = r && r.feed;
            if (!feed || !feed.groups) return;
            _ideaFeed = feed;
            const active = $('.idea-tab.active').data('group');
            // Lead with what's timely once it's here, unless the user already chose a tab
            const lead = (feed.groups.trending || []).length ? 'trending' : 'playbook';
            showIdeaGroup(refresh || _ideaTabPicked ? active : lead);
        }).fail(function (xhr) {
            const body = xhr.responseJSON || {};
            if (refresh) showToast(body.error || 'Could not load new ideas.', 'error');
            if (!_ideaFeed && $('.idea-tab.active').data('group') !== 'brand') {
                $('#ideaFeedGrid').html(`<div class="idea-feed-empty">${escapeHtml(body.error || 'Could not load ideas right now.')}</div>`);
            }
        }).always(function () {
            $busy.removeClass('on');
            $btn.prop('disabled', false).find('i').removeClass('fa-spin');
        });
    }

    $(document).on('click', '.idea-tab', function () {
        _ideaTabPicked = true;
        showIdeaGroup($(this).data('group'));
    });
    $('#refreshIdeaFeedBtn').on('click', function () { loadIdeaFeed(true); });
    $(document).on('click', '.idea-source', function (e) { e.stopPropagation(); });  // the link, not the card
    $(document).on('keydown', '.idea-card', function (e) {
        if (e.target === this && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); this.click(); }
    });

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

    // ── Saved briefs ─────────────────────────────────────────────────────
    // A brief starred under a sent message is kept on the account (/api/briefs)
    // and listed behind the star next to the paperclip, to load again.
    let savedBriefs = [];
    const sameBrief = (a, b) => String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
    const savedBriefFor = text => savedBriefs.find(b => sameBrief(b.text, text));

    function briefActionsHtml(text, platforms) {
        const saved = !!savedBriefFor(text);
        return `
                <div class="chat-user-actions">
                    <button type="button" class="chat-user-action btn-save-brief${saved ? ' is-saved' : ''}" data-brief="${escapeAttr(text)}"
                        data-platforms="${escapeAttr((platforms || []).join(','))}" aria-pressed="${saved}"
                        title="${saved ? 'Saved. Click to remove it from your saved briefs.' : 'Keep this brief to use again'}">
                        <i class="${saved ? 'fas' : 'far'} fa-star"></i><span>${saved ? 'Saved' : 'Save brief'}</span></button>
                    <button type="button" class="chat-user-action btn-reuse-brief" data-brief="${escapeAttr(text)}" data-platforms="${escapeAttr((platforms || []).join(','))}"
                        title="Start a new post from this brief, for another platform or another day">
                        <i class="fas fa-rotate-right"></i><span>Use again</span></button>
                </div>`;
    }

    function refreshBriefStars() {
        $('.btn-save-brief').each(function () {
            const saved = !!savedBriefFor($(this).attr('data-brief'));
            $(this).toggleClass('is-saved', saved).attr('aria-pressed', saved)
                .attr('title', saved ? 'Saved. Click to remove it from your saved briefs.' : 'Keep this brief to use again')
                .html(`<i class="${saved ? 'fas' : 'far'} fa-star"></i><span>${saved ? 'Saved' : 'Save brief'}</span>`);
        });
        $('#savedBriefsCount').text(savedBriefs.length || '').toggleClass('d-none', !savedBriefs.length);
    }

    function renderSavedBriefs() {
        $('#savedBriefsList').html(savedBriefs.length ? savedBriefs.map(b => `
            <li class="saved-brief">
                <button type="button" class="saved-brief-use" data-brief-id="${b.id}">
                    <span class="saved-brief-text">${escapeHtml(b.text)}</span>
                    ${b.platforms.length ? `<span class="saved-brief-meta">${escapeHtml(b.platforms.map(p => window.PostPreview.platformName(p)).join(', '))}</span>` : ''}
                </button>
                <button type="button" class="saved-brief-remove" data-brief-remove="${b.id}" aria-label="Remove this saved brief" title="Remove"><i class="fas fa-xmark"></i></button>
            </li>`).join('')
            : '<li class="saved-brief-empty">No saved briefs yet. Click <strong><i class="far fa-star"></i> Save brief</strong> under a message you sent to keep it here.</li>');
    }

    function setSavedBriefs(list) {
        savedBriefs = list || [];
        renderSavedBriefs();
        refreshBriefStars();
    }

    function setSavedBriefsOpen(open) {
        $('#savedBriefsPanel').prop('hidden', !open);
        $('#savedBriefsBtn').attr('aria-expanded', open);
    }

    // The brief goes into the box of a new conversation, with its platforms ticked
    // ── Start from a link or PDF (POST /api/repurpose) ───────────────────
    // The page or document is read, the AI suggests 3-5 post angles, and the
    // chosen one is loaded into the brief like a saved brief.
    const LONE_URL = /^https?:\/\/\S+$/i;
    let repurposeAngles = [];

    function openRepurpose(url) {
        $('#repurposeUrl').val(url || '');
        $('#repurposeFile').val('');
        $('#repurposeFileLabel').text('Upload a PDF (up to 15 MB)');
        $('#repurposeStatus, #repurposeResult').addClass('d-none').empty();
        $('#repurposeForm').removeClass('d-none');
        bootstrap.Modal.getOrCreateInstance(document.getElementById('repurposeModal')).show();
        if (url) runRepurpose({ url: url });
        else setTimeout(() => $('#repurposeUrl').trigger('focus'), 300);
    }

    function runRepurpose(source) {
        const isFile = source instanceof File;
        const started = Date.now();
        $('#repurposeResult').addClass('d-none').empty();
        $('#repurposeGo, #repurposeFile').prop('disabled', true);
        const $status = $('#repurposeStatus').removeClass('d-none is-error');
        const tick = () => {
            const s = Math.round((Date.now() - started) / 1000);
            $status.html(`<span class="spinner-border spinner-border-sm me-2"></span>${isFile ? 'Reading your PDF' : 'Reading the page'} and finding post ideas... ${s}s`);
        };
        tick();
        const clock = setInterval(tick, 1000);
        let request;
        if (isFile) {
            const form = new FormData();
            form.append('file', source);
            request = $.ajax({ url: '/api/repurpose', type: 'POST', data: form, processData: false, contentType: false });
        } else {
            request = $.ajax({ url: '/api/repurpose', type: 'POST', contentType: 'application/json', data: JSON.stringify(source) });
        }
        request.done(function (r) {
            $status.addClass('d-none');
            repurposeAngles = r.angles || [];
            const formats = { text: 'Text post', image: 'With an image', carousel: 'Carousel' };
            $('#repurposeResult').removeClass('d-none').html(`
                <div class="repurpose-source"><strong>${escapeHtml(r.title || 'Your content')}</strong>
                    ${r.summary ? `<p>${escapeHtml(r.summary)}</p>` : ''}</div>
                <div class="repurpose-angles">${repurposeAngles.map((a, i) => `
                    <div class="repurpose-angle">
                        <div class="repurpose-angle-head"><strong>${escapeHtml(a.title)}</strong>
                            <span class="repurpose-format">${formats[a.format] || 'Text post'}</span></div>
                        <p>${escapeHtml(a.brief)}</p>
                        <button type="button" class="btn btn-sm btn-dark" data-repurpose-use="${i}">Use this</button>
                    </div>`).join('')}</div>`);
        }).fail(function (xhr) {
            $status.addClass('is-error').text((xhr.responseJSON || {}).error || 'Could not read that. Please try again.');
        }).always(function () {
            clearInterval(clock);
            $('#repurposeGo, #repurposeFile').prop('disabled', false);
        });
    }

    $('#repurposeBtn').on('click', () => openRepurpose(''));
    $('#repurposeForm').on('submit', function (e) {
        e.preventDefault();
        const url = $('#repurposeUrl').val().trim();
        if (!url) { $('#repurposeUrl').trigger('focus'); return; }
        runRepurpose({ url: url });
    });
    $('#repurposeFile').on('change', function () {
        const file = this.files && this.files[0];
        if (!file) return;
        $('#repurposeFileLabel').text(file.name);
        runRepurpose(file);
    });
    $(document).on('click', '[data-repurpose-use]', function () {
        const angle = repurposeAngles[Number($(this).data('repurpose-use'))];
        if (!angle) return;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('repurposeModal')).hide();
        loadBriefIntoComposer(angle.brief, null);
        if (angle.format !== 'text') {  // tick Image when the idea wants a visual and it's available
            const $image = $('input[name="outputOptions"][value="image"]');
            if ($image.length && !$image.prop('disabled')) $image.prop('checked', true).trigger('change');
        }
    });
    // A link pasted on its own into the brief: offer to read it
    storyInput.on('input', function () {
        $('#repurposeHint').toggleClass('d-none', !LONE_URL.test(storyInput.val().trim()));
    });
    $('#repurposeHint').on('click', function () {
        const url = storyInput.val().trim();
        $(this).addClass('d-none');
        storyInput.val('').trigger('input');
        openRepurpose(url);
    });

    // ── Smart defaults: the composer starts with last time's platforms and
    // outputs (people keep what's pre-selected), and says so.
    const SETUP_KEY = 'avir_last_setup';
    function rememberSetup(platforms) {
        if (!platforms || !platforms.length) return;
        const outputs = $('input[name="outputOptions"]:checked').map(function () { return this.value; }).get();
        try { localStorage.setItem(SETUP_KEY, JSON.stringify({ platforms: platforms, outputs: outputs })); } catch (e) { /* storage blocked */ }
    }
    function restoreSetup() {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(SETUP_KEY) || 'null'); } catch (e) { return; }
        if (!saved || !Array.isArray(saved.platforms) || !saved.platforms.length) return;
        if ($('.platform-chips-inline input[type="checkbox"]:checked').length) return;  // something was chosen already
        $('.platform-chips-inline input[type="checkbox"]').each(function () {
            if (saved.platforms.includes(this.value)) $(this).prop('checked', true).trigger('change');
        });
        (saved.outputs || []).forEach(function (value) {
            const $o = $(`input[name="outputOptions"][value="${value}"]`);
            if ($o.length && !$o.prop('disabled')) $o.prop('checked', true).trigger('change');
        });
        $('#lastSetupNote').removeClass('d-none');
    }
    // Any change by hand: the note no longer applies
    $(document).on('change', '.platform-chips-inline input, input[name="outputOptions"]', function (e) {
        if (e.originalEvent) $('#lastSetupNote').addClass('d-none');
    });
    setTimeout(restoreSetup, 600);  // after the plan/quota checks have enabled or disabled outputs

    // ── Examples in the empty brief: from the user's own brand when there is
    // one (recognition is easier than inventing from a blank box)
    const GENERIC_EXAMPLES = [
        'Announce our new service with 3 key benefits',
        'Share a customer success story with one clear result',
        'Give 3 practical tips our customers can use this week',
        'Invite people to our upcoming event or webinar',
    ];
    let briefExamples = GENERIC_EXAMPLES.slice();
    let exampleIndex = 0;
    function rotateBriefExample() {
        const $box = $('#storyInput');
        if ($box.val() || $box.prop('disabled')) return;
        $box.attr('placeholder', `What would you like to post about? e.g. ${briefExamples[exampleIndex % briefExamples.length]}`);
        exampleIndex += 1;
    }
    window.setBriefExamples = function (ideas) {
        const titles = (ideas || []).map(i => (i && (i.title || i.prompt) || '').trim()).filter(t => t && t.length <= 90);
        if (titles.length) { briefExamples = titles.slice(0, 8); exampleIndex = 0; rotateBriefExample(); }
    };
    rotateBriefExample();
    setInterval(rotateBriefExample, 4500);

    function loadBriefIntoComposer(text, platforms) {
        if ($('#chatThread').children().length) startNewChat(true);
        storyInput.val(text).trigger('input').trigger('focus');
        closeSlashMenu();
        if (platforms && platforms.length) {
            $('.platform-chips-inline input[type="checkbox"]').each(function () {
                $(this).prop('checked', platforms.includes($(this).val())).trigger('change');
            });
        }
        showToast('Brief loaded. Choose the platforms and press Generate.', 'info');
    }

    $.get('/api/briefs').done(r => setSavedBriefs(r.briefs));

    $(document).on('click', '.btn-save-brief', function () {
        const text = $(this).attr('data-brief');
        const existing = savedBriefFor(text);
        const request = existing
            ? $.ajax({ url: `/api/briefs/${existing.id}`, type: 'DELETE' })
            : $.ajax({ url: '/api/briefs', type: 'POST', contentType: 'application/json',
                       data: JSON.stringify({ text: text, platforms: ($(this).attr('data-platforms') || '').split(',').filter(Boolean) }) });
        request.done(r => {
            setSavedBriefs(r.briefs);
            showToast(existing ? 'Removed from your saved briefs.' : 'Saved. Find it under the star next to the paperclip.', 'success');
        }).fail(xhr => showToast((xhr.responseJSON || {}).error || 'Could not save the brief.', 'error'));
    });
    $(document).on('click', '.btn-reuse-brief', function () {
        loadBriefIntoComposer($(this).attr('data-brief'), ($(this).attr('data-platforms') || '').split(',').filter(Boolean));
    });
    $(document).on('click', '#savedBriefsBtn', function (e) {
        e.stopPropagation();
        setSavedBriefsOpen($('#savedBriefsPanel').prop('hidden'));
    });
    $(document).on('click', '.saved-brief-use', function () {
        const brief = savedBriefs.find(b => b.id === Number($(this).data('brief-id')));
        if (!brief) return;
        setSavedBriefsOpen(false);
        loadBriefIntoComposer(brief.text, brief.platforms);
        $.ajax({ url: `/api/briefs/${brief.id}/use`, type: 'POST' });
    });
    $(document).on('click', '[data-brief-remove]', function (e) {
        e.stopPropagation();
        $.ajax({ url: `/api/briefs/${$(this).data('brief-remove')}`, type: 'DELETE' }).done(r => setSavedBriefs(r.briefs));
    });
    $(document).on('click', function (e) {
        if (!e.target.closest('#savedBriefsPanel, #savedBriefsBtn')) setSavedBriefsOpen(false);
    });
    $(document).on('keydown', function (e) {
        if (e.key === 'Escape' && !$('#savedBriefsPanel').prop('hidden')) {
            setSavedBriefsOpen(false);
            $('#savedBriefsBtn').trigger('focus');
        }
    });

    // ── An image picked in the Image Library ("Use in a new post") ─────────
    // arrives attached to a new conversation (static/js/library.js)
    function takePendingImage() {
        let url = null;
        try {
            url = sessionStorage.getItem('avir_pending_image');
            sessionStorage.removeItem('avir_pending_image');
        } catch (e) { return false; }
        if (!url || !/^\/static\/uploads\/[^/\\]+$/.test(url)) return false;
        const path = 'static/uploads/' + url.split('/').pop();  // the form an upload is known by
        uploadedImagePath = path;
        threadActiveImagePath = path;
        uploadedImageAnalysis = null;
        $('#previewImg').attr('src', url);
        $('#imagePreview').removeClass('d-none');
        setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>From your library');
        updateGenerateGate();
        showToast('Image attached. Describe the post you want to make with it.', 'info');
        return true;
    }

    // ── Draft of the brief ──────────────────────────────────────────────
    // What is typed and not yet sent is kept in this browser, so a refresh or
    // a closed tab doesn't lose it. Sending (or clearing) the box removes it.
    const DRAFT_KEY = 'avir.studio.draft';
    const DRAFT_MAX_AGE_MS = 7 * 24 * 3600 * 1000;
    const openedWithLink = /[?&](idea|do)=/.test(window.location.search);  // those fill the box themselves
    let draftUser = null, draftTimer = null;  // draftUser stays empty until restoreDraft: nothing is saved or removed before

    function saveDraft() {
        if (!draftUser) return;
        const text = storyInput.val();
        try {
            if (text.trim()) localStorage.setItem(DRAFT_KEY, JSON.stringify({ user: draftUser, text: text, at: Date.now() }));
            else localStorage.removeItem(DRAFT_KEY);
        } catch (e) { /* storage unavailable (private window): no draft */ }
    }

    function restoreDraft(userId) {
        if (draftUser) return;  // once per page
        draftUser = userId;
        if (!userId || openedWithLink) return;
        let draft = null;
        try { draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) { return; }
        if (!draft || !draft.text) return;
        if (draft.user !== userId || Date.now() - draft.at > DRAFT_MAX_AGE_MS) {  // someone else's, or stale
            try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* nothing to remove */ }
            return;
        }
        if (storyInput.val().trim()) return;
        storyInput.val(draft.text).trigger('input');
        closeSlashMenu();
        showToast('Your unsent draft is back in the message box.', 'info');
    }

    storyInput.on('input', function () {
        clearTimeout(draftTimer);
        draftTimer = setTimeout(saveDraft, 400);
    });
    window.addEventListener('pagehide', saveDraft);

    // ── Quick Prompt Templates ─────────────────────────────────────────
    $(document).on('click', '.quick-prompt-card, .idea-card:not(.is-loading)', function () {
        const promptText = $(this).attr('data-prompt');
        if (!$(this).hasClass('idea-card')) {
            storyInput.val(promptText).trigger('input').focus();
            return showToast('Prompt loaded into brief input!', 'info');
        }
        useIdea({ prompt: promptText, platform: $(this).attr('data-platform'), format: $(this).attr('data-format') });
    });

    // The weekly ideas email's "Create this post" button opens
    // /dashboard?idea=<token>: start a fresh chat with that idea loaded.
    // Returns true when the page was opened from such a link.
    function openEmailedIdea() {
        // A suggested day clicked on the Content Calendar hands its idea over here (calendar.js)
        let handed = null;
        try {
            handed = sessionStorage.getItem('avir_pending_idea');
            sessionStorage.removeItem('avir_pending_idea');
        } catch (e) { /* storage blocked */ }
        if (handed) {
            try {
                const idea = JSON.parse(handed);
                $.when(window.__quotaLoaded).always(function () { useIdea(idea); });
                return true;
            } catch (e) { /* not an idea: carry on */ }
        }
        const token = new URLSearchParams(window.location.search).get('idea');
        if (!token) return false;
        history.replaceState(null, '', window.location.pathname);  // a reload shouldn't load it again
        $.getJSON('/api/ideas/link/' + encodeURIComponent(token)).done(function (r) {
            if (r && r.idea) $.when(window.__quotaLoaded).always(function () { useIdea(r.idea); });
        }).fail(function (xhr) {
            showToast((xhr.responseJSON || {}).error || 'This idea link is no longer available.', 'warning');
        });
        return true;
    }

    // An idea (an "Ideas for you" card, or one from the weekly email) fills
    // the brief and picks its platform and output. Image and Video are only
    // ticked when this user can use them today.
    function useIdea(idea) {
        storyInput.val(idea.prompt || '').trigger('input').focus();
        const platform = idea.platform;
        const format = idea.format || 'text';
        $('.platform-chips-inline input[type="checkbox"]').each(function () {
            $(this).prop('checked', $(this).val() === platform).trigger('change');
        });
        $('input[name="outputOptions"]').each(function () {
            const want = $(this).val() === 'text' || $(this).val() === format;
            $(this).prop('checked', want && !$(this).prop('disabled')).trigger('change');
        });
        const missed = format !== 'text' && !$(`input[name="outputOptions"][value="${format}"]`).prop('checked');
        showToast(missed
            ? `Idea loaded. ${format === 'video' ? 'Video' : 'Image'} isn't available on your account right now, so it's set to text.`
            : 'Idea loaded with its platform and output - edit the brief or press Generate.', 'info');
    }

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

    // Paste an image (a screenshot, or "Copy image" from anywhere) to attach it.
    // Copied text is left alone - spreadsheets and documents put a picture of
    // the cells on the clipboard next to the text, and the text is what is meant.
    $(document).on('paste', function (e) {
        const clip = e.originalEvent && e.originalEvent.clipboardData;
        if (!clip || Array.from(clip.types || []).includes('text/plain')) return;
        const el = e.target;
        const elsewhere = el && el !== storyInput[0] && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
        if (elsewhere || $('.modal.show').length || dropZone.hasClass('dock-disabled')) return;
        const item = Array.from(clip.items || []).find(it => it.kind === 'file' && /^image\//.test(it.type));
        const file = item && item.getAsFile();
        if (!file) return;
        e.preventDefault();
        handleImageUpload(file);
        showToast('Image pasted and attached to your message.', 'info');
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
        setAnalysisBadge('badge-warn', '<i class="fas fa-spinner fa-spin me-1"></i>Uploading...');
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
                if (r.analysis_pending && !r.analysis) {
                    // Ready to use now; the AI analysis finishes in the background
                    setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Uploaded <span class="analysis-sub"><i class="fas fa-spinner fa-spin ms-1 me-1"></i>analysing</span>');
                    watchImageAnalysis(r.image_id, r.filepath);
                } else {
                    setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Analyzed');
                }
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

    // The upload's background AI analysis: picked up when it's done (it feeds
    // the product notes of image commands). Nothing waits for it - a post
    // generated before it finishes uses that same analysis on the server.
    let analysisWatch = null;
    function watchImageAnalysis(imageId, filepath) {
        clearTimeout(analysisWatch);
        const started = Date.now();
        (function check() {
            if (uploadedImagePath !== filepath) return;  // removed or replaced
            $.getJSON('/api/upload/analysis', { image_id: imageId }).done(function (r) {
                if (uploadedImagePath !== filepath) return;
                if (r && r.ready) {
                    uploadedImageAnalysis = r.analysis || null;
                    setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Analyzed');
                } else if (Date.now() - started < 180000) {
                    analysisWatch = setTimeout(check, 2000);
                } else {
                    setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Uploaded');  // still usable
                }
            }).fail(function () {
                if (uploadedImagePath === filepath && Date.now() - started < 180000) analysisWatch = setTimeout(check, 4000);
            });
        })();
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
                btn.prop('disabled', false).html('<i class="fas fa-brain"></i><span class="d-none d-md-inline ms-1">Research first</span>');
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
        $item.data('state', state).removeClass('active completed d-none');
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
        const $existing = $list.find(`[data-platform="${key}"]`);
        if ($existing.length) {
            // The live draft becomes the finished caption
            if ($existing.hasClass('is-draft')) {
                smoothText($existing.removeClass('is-draft').find('.caption-preview-text')[0], text);
                $existing.find('.caption-preview-label').html(`<i class="fas fa-eye me-1"></i>${escapeHtml(captionPlatformLabel(key))} caption - finishing touches in progress`);
            }
            return;
        }
        const names = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube', twitter: 'X', x: 'X', tiktok: 'TikTok' };
        const label = names[key.toLowerCase()] || key.charAt(0).toUpperCase() + key.slice(1);
        $list.removeClass('d-none').append(`
            <div class="caption-preview" data-platform="${key}">
                <div class="caption-preview-label"><i class="fas fa-eye me-1"></i>${escapeHtml(label)} caption - finishing touches in progress</div>
                <div class="caption-preview-text">${escapeHtml(text)}</div>
            </div>
        `);
    }

    function captionPlatformLabel(key) {
        const names = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube', twitter: 'X', x: 'X', tiktok: 'TikTok' };
        return names[key.toLowerCase()] || key.charAt(0).toUpperCase() + key.slice(1);
    }

    // A caption while it's being written (server: "draft:<platform>")
    function renderCaptionDraft(msgId, platform, text) {
        const $list = $(`#${msgId}_captionPreviews`);
        if (!$list.length || !text) return;
        const key = String(platform).replace(/[^a-z0-9_-]/gi, '');
        let $item = $list.find(`[data-platform="${key}"]`);
        if (!$item.length) {
            $item = $(`
                <div class="caption-preview is-draft" data-platform="${key}">
                    <div class="caption-preview-label"><i class="fas fa-pen-nib me-1"></i>Writing the ${escapeHtml(captionPlatformLabel(key))} caption...</div>
                    <div class="caption-preview-text"></div>
                </div>`);
            $list.removeClass('d-none').append($item);
            // The post is what people wait for: fold the research away and bring the draft into view
            $(`#${msgId}_pipeline_research`).addClass('is-folded');
            try { $item[0].scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { /* old browsers */ }
        }
        if (!$item.hasClass('is-draft')) return;  // already finished
        smoothText($item.find('.caption-preview-text')[0], text);
    }

    // The story step's research, live inside the pipeline card
    function renderPipelineResearch(msgId, raw) {
        let data;
        try { data = JSON.parse(raw); } catch (e) { return; }
        const $step = $(`#${msgId}_step_story`);
        if (!$step.length) return;
        let $live = $(`#${msgId}_pipeline_research`);
        if (!$live.length) {
            $live = $(`<div class="research-live writing pipeline-research" id="${msgId}_pipeline_research" aria-live="polite"
                            title="Click to show or hide the research"></div>`);
            $live.on('click', function () { $(this).toggleClass('is-folded'); });
            $step.after($live);
        }
        if ($step.hasClass('completed') || $step.data('state') === 'done') $live.removeClass('writing');
        if (data.stage === 'thinking') return;
        renderResearchLive(msgId, data, $live, $step.find('.agent-step-desc'));
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
        // A running clock, so a long AI answer never looks like a frozen page
        const started = Date.now();
        const clock = setInterval(function () {
            const s = Math.round((Date.now() - started) / 1000);
            $(`#${msgId}_timer`).text(s < 20 ? `Working... ${s}s` : `Working... ${s}s (usually 30-60s)`);
        }, 1000);
        (function poll() {
            if (stopped) return;
            $.getJSON(`/api/generate/progress/${encodeURIComponent(progressId)}`)
                .done(function (res) {
                    const steps = (res && res.steps) || {};
                    Object.entries(steps).forEach(([step, value]) => {
                        if (step.startsWith('preview:')) renderCaptionPreview(msgId, step.slice(8), value);
                        else if (step.startsWith('draft:')) {
                            if (!steps[`preview:${step.slice(6)}`]) renderCaptionDraft(msgId, step.slice(6), value);
                        } else if (step === 'research') renderPipelineResearch(msgId, value);
                        else setAgentStep(msgId, step, value);
                    });
                })
                .always(function () {
                    if (!stopped) timer = setTimeout(poll, 600);
                });
        })();
        return function stop() {
            stopped = true;
            clearTimeout(timer);
            clearInterval(clock);
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
        rememberSetup(platforms);

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
        appendUserMessage(story, uploadedImagePath, baseIsPreset ? [] : (refinePlatforms || platforms), tone, mediaType, brandVoice,
            { followUp: canRefine });

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

    function appendUserMessage(text, imagePath, platforms, tone, mediaType, brandVoice, opts) {
        const isBrief = !(opts && opts.followUp);
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
                ${isBrief ? briefActionsHtml(text, platforms) : ''}
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
            : flags.length ? `${flags.length} compliance issue${flags.length > 1 ? 's' : ''} auto-fixed` : 'Compliance checked';
        const flagsHtml = flags.map(f => `
            <li class="mb-1">
                <span class="badge ${f.auto_fixed ? 'bg-success-subtle text-success' : 'bg-warning-subtle text-warning'} me-1">${f.auto_fixed ? 'Fixed' : 'Action needed'}</span>
                <strong>${escapeHtml(f.framework)}</strong> - ${escapeHtml(f.issue)}
                ${f.fix ? `<div class="text-muted small">${escapeHtml(f.fix)}</div>` : ''}
            </li>`).join('');
        const disclaimersHtml = (compliance.disclaimers_added || []).map(d =>
            `<li class="mb-1"><span class="badge bg-primary-subtle text-primary me-1">Disclaimer added</span>${escapeHtml(d)}</li>`).join('');
        const added = (compliance.disclaimers_added || []).length;
        const extra = [`${compliance.rules_checked} rules`, added ? `disclaimer added` : ''].filter(Boolean).join(' · ');
        // One line when all is well; open by itself when something needs the user
        return `
            <details class="compliance-line${attention ? ' needs-attention' : ''}"${attention || compliance.stale ? ' open' : ''}>
                <summary><i class="fas ${attention ? 'fa-triangle-exclamation' : 'fa-circle-check'} me-1"></i>${headerText}
                    <span class="compliance-extra">${extra}</span><i class="fas fa-chevron-down compliance-caret"></i></summary>
                <div class="compliance-body">
                    ${flagsHtml || disclaimersHtml ? `<ul class="list-unstyled small mb-1">${flagsHtml}${disclaimersHtml}</ul>` : ''}
                    ${compliance.stale ? '<div class="compliance-stale"><i class="fas fa-clock-rotate-left me-1"></i>Checked before your edit. The caption is checked again when you send it for approval.</div>' : ''}
                    <div class="text-muted" style="font-size: 0.7rem;">Guidance only, not legal advice.</div>
                </div>
            </details>`;
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
        // "New image": "Rewrite caption" keeps the image, so this is how a post gets a fresh one
        const newImageBtn = msgId && platform ? `
                        <button type="button" class="btn-copy-sm" onclick="regenerateImage('${msgId}', '${platform}')"
                                title="Create a new image for this post (uses 1 of today's images); the caption stays"><i class="fas fa-rotate me-1"></i>New image</button>` : '';
        return `
            <div class="media-output-card">
                <div class="media-output-header">
                    <span><i class="fas fa-image text-primary me-2"></i>Generated Image (${media.resolution || '1024x1024'})</span>
                    <div class="media-output-actions">
                        <button type="button" class="btn-copy-sm" onclick="openImagePreview('${media.url}')" title="View the image larger"><i class="fas fa-expand me-1"></i>Enlarge</button>
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
                    <span class="assistant-run-tag gen-timer me-2" id="${msgId}_timer" aria-live="off">Working...</span>
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
                    <span class="agent-step-desc">Curating broad, niche &amp; brand hashtags (runs alongside the research and captions)</span>
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

                <!-- Shown only when the user has industry/market rules (server step "compliance") -->
                <div class="agent-step-item d-none" id="${msgId}_step_compliance">
                    <div class="agent-step-icon"><i class="fas fa-circle-notch text-muted"></i></div>
                    <span class="agent-step-name"><i class="fas fa-scale-balanced me-1 text-indigo"></i>Compliance Check</span>
                    <span class="agent-step-desc">Checking the caption against your industry's advertising rules</span>
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
                            <span class="research-elapsed ms-auto" data-started="${Date.now()}" aria-live="off">0s</span>
                        </div>
                        <div class="research-live writing" id="${msgId}_research_live" aria-live="polite"></div>
                        <div class="research-wait-note">Research usually takes under a minute; it can take longer when the AI service is busy.</div>
                    </div>
                </div>
            </div>
        `;
        const elem = $(html);
        $('#chatThread').append(elem);
        return elem;
    }
    // Live "how long so far" on the research step - a long wait is visibly still working
    setInterval(function () {
        $('.research-elapsed').each(function () {
            const secs = Math.floor((Date.now() - Number(this.dataset.started || Date.now())) / 1000);
            $(this).text(secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`);
        });
    }, 1000);

    function appendRefineThinking(msgId, selectedOutputs) {
        // A refine that makes a video holds the request open for the whole
        // render (10+ minutes), so it shows the video loader instead of a bare
        // "refining" spinner
        const wantsVideo = (selectedOutputs || []).includes('video');
        const html = `
            <div class="chat-message-assistant" id="${msgId}">
                <div class="assistant-avatar">${assistantAvatarSvg()}</div>
                <div class="assistant-card">
                    <div class="assistant-header">
                        <div class="assistant-title"><i class="fas ${wantsVideo ? 'fa-video' : 'fa-wand-magic-sparkles'}"></i>${wantsVideo ? 'Creating Your Video' : 'Refining Your Post'}</div>
                        <button type="button" class="btn btn-sm btn-outline-danger" onclick="cancelGeneration('${msgId}')">
                            <i class="fas fa-stop me-1"></i>Stop
                        </button>
                    </div>
                    ${wantsVideo ? videoCreatingHtml() : `
                    <div class="agent-stepper">
                        <div class="agent-stepper-title"><i class="fas fa-cogs me-1"></i>Autonomous Agent Working:</div>
                        <div class="agent-step-item active">
                            <div class="agent-step-icon"><div class="spinner-border spinner-border-sm text-primary" role="status"></div></div>
                            <span class="agent-step-name"><i class="fas fa-pen-ruler me-1 text-purple"></i>Refinement Editor</span>
                            <span class="agent-step-desc">Applying only your requested change to the current version</span>
                        </div>
                    </div>`}
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
        assistantElem = assistantElem || appendRefineThinking(msgId, opts.selectedOutputs);
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
                if (res.credit_limit_exceeded) {
                    assistantElem.find('.assistant-card').html(`
                        <div class="alert alert-warning mb-0">
                            <i class="fas fa-exclamation-triangle me-2"></i><strong>Error:</strong> ${escapeHtml(errText)}
                        </div>
                    `);
                    openCreditRequestModal();
                } else {
                    assistantElem.find('.assistant-card').html(errorCardHtml("Your change couldn't be made", xhr, errText));
                    assistantElem.find('.gen-retry-btn').on('click', function () {
                        // The same instruction on the same version, in the same chat bubble
                        assistantElem.replaceWith(appendRefineThinking(msgId, opts.selectedOutputs));
                        runRefine(msgId, opts, $(`#${msgId}`));
                    });
                }
                showToast('Refinement error: ' + errText, 'error');
            }
        });
    }

    // ── Smooth streaming ──────────────────────────────────────────────────
    // Streamed text arrives in bursts (an update every ~half second). Instead
    // of jumping, the new part is typed out so it flows into the next update;
    // when it falls behind it speeds up to catch up.
    const smoothState = new WeakMap();
    const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function smoothText(el, target) {
        if (!el) return;
        target = String(target || '');
        let st = smoothState.get(el);
        if (!st) {
            st = { shown: el.textContent || '', target: '', raf: null };
            smoothState.set(el, st);
        }
        st.target = target;
        if (!target.startsWith(st.shown)) st.shown = '';  // the text changed rather than grew: retype it
        if (prefersReducedMotion) {
            st.shown = target;
            el.textContent = target;
            return;
        }
        if (st.raf) return;  // already typing - it picks up the new target
        let last = performance.now();
        const tick = (now) => {
            const remaining = st.target.length - st.shown.length;
            if (remaining <= 0) { st.raf = null; return; }
            const elapsed = Math.min(now - last, 100);
            last = now;
            // Finish the backlog in ~0.9 s, never slower than ~45 characters a second
            const perMs = Math.max(remaining / 900, 0.045);
            let next = st.shown.length + Math.max(1, Math.round(perMs * elapsed));
            const space = st.target.indexOf(' ', next);  // end on a whole word when it's close
            if (space !== -1 && space - next < 4) next = space;
            st.shown = st.target.slice(0, next);
            el.textContent = st.shown;
            followStream();
            st.raf = requestAnimationFrame(tick);
        };
        st.raf = requestAnimationFrame(tick);
    }

    // Follow streamed text only when the reader is already at the bottom -
    // scrolling up to read stops the page from jumping
    function followStream() {
        const ws = document.getElementById('chatWorkspace');
        if (ws && ws.scrollHeight - ws.scrollTop - ws.clientHeight < 160) ws.scrollTop = ws.scrollHeight;
    }

    // The research while it's being written (server: progress field "research")
    const RESEARCH_SECTIONS = [
        ['themes', 'Themes', 'pills'],
        ['research_notes', 'Facts & trends', 'list'],
        ['hooks', 'Post ideas', 'list']
    ];

    function renderResearchLive(msgId, data, $live, $desc) {
        $live = $live || $(`#${msgId}_research_live`);
        if (!$live.length || !data) return;
        $desc = $desc || $(`#${msgId} .agent-step-desc`);
        if (data.stage === 'thinking') {
            $desc.text('Thinking about your topic...');
            return;
        }
        const ideas = (data.hooks || []).length;
        if (!$live.hasClass('writing')) {
            $desc.text(ideas ? `Research ready - ${ideas} post idea${ideas === 1 ? '' : 's'}` : 'Research ready');
        } else {
            $desc.text(ideas ? `Writing the research - ${ideas} post idea${ideas === 1 ? '' : 's'} so far` : 'Writing the research...');
        }
        RESEARCH_SECTIONS.forEach(([key, title, kind]) => {
            const items = data[key] || [];
            if (!items.length) return;
            let $sec = $live.find(`[data-sec="${key}"]`);
            if (!$sec.length) {
                $sec = $(`<div class="research-live-sec" data-sec="${key}"><div class="research-live-title">${title}</div>
                    ${kind === 'pills' ? '<div class="research-theme-pills"></div>' : '<ul class="research-notes-list"></ul>'}</div>`);
                $live.append($sec);
            }
            const $box = $sec.find(kind === 'pills' ? '.research-theme-pills' : 'ul');
            if (key === 'hooks') $sec.find('.research-live-title').text(`Post ideas (${items.length})`);
            items.forEach((text, i) => {
                let $item = $box.children().eq(i);
                if (!$item.length) {
                    $item = $(kind === 'pills' ? '<span class="research-theme-pill live-new"></span>' : '<li class="live-new"></li>');
                    $box.append($item);
                }
                smoothText($item[0], text);  // the item still being written is typed out as it grows
            });
        });
        followStream();
    }

    function pollResearchProgress(msgId, progressId) {
        let stopped = false;
        let timer = null;
        (function poll() {
            if (stopped) return;
            $.getJSON(`/api/generate/progress/${encodeURIComponent(progressId)}`)
                .done(function (res) {
                    const raw = res && res.steps && res.steps.research;
                    if (!raw || stopped) return;
                    try { renderResearchLive(msgId, JSON.parse(raw)); } catch (e) { /* half-written progress: next poll */ }
                })
                .always(function () { if (!stopped) timer = setTimeout(poll, 600); });
        })();
        return function stop() { stopped = true; clearTimeout(timer); };
    }

    function runResearchThenAsk(msgId, story, targetCompany, activeImgPath, tone, brandVoice) {
        const assistantElem = appendResearchThinking(msgId);
        const progressId = newProgressId();
        const stopLive = pollResearchProgress(msgId, progressId);

        $.ajax({
            url: '/api/analyze-story',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ story: story, target_company: targetCompany, previous_context: lastAssistantContext, progress_id: progressId }),
            complete: stopLive,
            success: function (r) {
                renderResearchResult(msgId, assistantElem, r, story, targetCompany, activeImgPath, tone, brandVoice);
                scrollToBottom();
            },
            error: function (xhr) {
                const errText = xhr.responseJSON?.error || 'Research failed';
                assistantElem.find('.assistant-card').html(errorCardHtml("The research couldn't be finished", xhr, errText));
                assistantElem.find('.gen-retry-btn').on('click', function () {
                    assistantElem.remove();
                    runResearchThenAsk(msgId, story, targetCompany, activeImgPath, tone, brandVoice);
                });
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

        // Same two-column layout as a post: the next step (make the post) on the
        // left, the research on the right
        const cardContent = `
            <div class="assistant-header">
                <div class="assistant-title"><i class="fas fa-magnifying-glass"></i>Research Summary</div>
            </div>
            <div class="out-layout">
            <aside class="out-side research-cta-block" aria-label="Turn this into a post">
                <p class="out-eyebrow">Next step</p>
                <h3 class="out-title">Turn this into a post</h3>
                <p class="out-sub">${themes.length + researchNotes.length + hooks.length} findings ready to use</p>
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
                        <span class="coming-soon video-gate" title="Coming soon"><label class="media-chip-sm"><input type="checkbox" value="video" disabled><span class="chip-content"><i class="fas fa-video me-1 text-purple"></i>Video<span class="soon-tag">Soon</span></span></label></span>
                    </div>
                </div>
                <button type="button" class="btn btn-primary btn-sm mt-2 research-generate-btn" id="${msgId}_researchGenerateBtn">
                    <i class="fas fa-bolt me-1"></i>Generate Content
                </button>
            </aside>
            <div class="out-main research-result-block">
                ${themesHtml}
                ${notesHtml}
                ${hooksHtml}
            </div>
            </div>
        `;
        assistantElem.find('.assistant-card').html(cardContent);

        // This card's Image option follows today's image limit too
        if (window.__imageQuota) window.applyImageQuota(window.__imageQuota);
        window.applyVideoAccess(window.__videoEnabled || !!window.__videoLock, window.__videoLock);  // and its Video option, the user's video access
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

    // ── Editing a post by hand ("Edit") ──────────────────────────────────
    // The caption and hashtags of one platform (or of a carousel: target
    // 'carousel') are changed in place and saved to the run - no generation,
    // no cost (POST /api/history/<run>/edit, services/post_edit_service.py).
    // The AI's text is kept, so "Restore original" brings it back.
    const textLength = text => Array.from(text).length;  // as the server counts: an emoji is one character

    // An image command's own caption: a /carousel's, or a /hiring post's
    const commandCopy = meta => (meta || {}).carousel || (meta || {}).hiring || null;

    function postTextOf(msgId, target) {
        const h = window.chatHistory[msgId];
        const r = h && h.responses[h.currentIndex];
        if (!r) return null;
        const content = r.content || {};
        if (target === 'carousel') {
            const car = commandCopy(content._preset || h.presetRun);
            return car ? { node: car, caption: car.caption || '', tags: car.hashtags || [], runId: r.runId } : null;
        }
        const node = content[target] || {};
        return { node: node, caption: node.caption?.primary_caption || '', tags: getTagList(node.hashtags), runId: r.runId };
    }

    function setPostNode(msgId, target, node) {
        const h = window.chatHistory[msgId];
        const content = h.responses[h.currentIndex].content;
        if (target === 'carousel') {
            const meta = content._preset || h.presetRun;
            meta[meta.hiring ? 'hiring' : 'carousel'] = node;
        }
        else content[target] = node;
    }

    // Nothing to save an edit to when the post wasn't saved (no run id)
    function editActionsHtml(msgId, target, post, focus) {
        if (!post.runId) return '';
        return `${post.node.edited && !focus ? `<button type="button" class="btn-copy-sm btn-restore-post" data-msg="${msgId}" data-target="${target}" title="Go back to the text AVIR AI wrote"><i class="fas fa-rotate-left me-1"></i>Restore original</button>` : ''}
                <button type="button" class="btn-copy-sm btn-edit-post" data-msg="${msgId}" data-target="${target}"${focus ? ` data-focus="${focus}"` : ''}><i class="fas fa-pen me-1"></i>Edit</button>`;
    }

    const editedBadgeHtml = post => post.node.edited
        ? '<span class="badge-edited" title="Changed by hand after it was generated">Edited</span>' : '';

    // One platform's caption, compliance notes and hashtags
    function postTextBlockHtml(msgId, p) {
        const post = postTextOf(msgId, p);
        const pData = post.node;
        const primaryCap = post.caption || 'No caption generated.';
        const isRefined = pData.caption?.refined_by_critic || pData.quality?.self_corrected;
        const tagsHtml = post.tags.map(t => `<span class="hashtag-pill">${escapeHtml(t)}</span>`).join(' ') || '<em>No hashtags</em>';
        const cardId = `${msgId}_caption_target_${p}`;
        const tagsCardId = `${msgId}_hashtags_target_${p}`;
        return `
                    <div class="post-text-block post-text-flat" id="${msgId}_text_${p}">
                    <!-- Post Caption Card -->
                    <div class="post-box-card">
                        <div class="post-box-header">
                            <span class="post-box-title">
                                <i class="fas fa-pen-nib me-1"></i>Post Caption
                                ${isRefined ? '<span class="badge bg-success ms-2"><i class="fas fa-shield-check me-1"></i>Critic Refined</span>' : ''}
                                ${editedBadgeHtml(post)}
                            </span>
                            <span class="post-box-actions">
                                ${editActionsHtml(msgId, p, post)}
                                <button class="btn-copy-sm btn-copy-text" id="${cardId}_copy" data-text="${escapeAttr(primaryCap)}">
                                    <i class="fas fa-copy me-1"></i>Copy
                                </button>
                            </span>
                        </div>

                        <div class="post-caption-text" id="${cardId}">${escapeHtml(primaryCap)}</div>
                    </div>

                    ${buildComplianceHtml(pData.compliance)}

                    <!-- Hashtags: one line (the first few), all of them and Edit / Copy on click -->
                    <details class="hashtag-line">
                        <summary>
                            <i class="fas fa-hashtag me-1"></i><strong>${post.tags.length} hashtag${post.tags.length === 1 ? '' : 's'}</strong>
                            <span class="hashtag-peek">${escapeHtml(post.tags.slice(0, 3).join(' '))}${post.tags.length > 3 ? ` +${post.tags.length - 3} more` : ''}</span>
                            <i class="fas fa-chevron-down compliance-caret"></i>
                        </summary>
                        <div class="post-box-card hashtag-body">
                            <div class="post-box-header">
                                <span class="post-box-title"><i class="fas fa-hashtag me-1"></i>Hashtags</span>
                                <span class="post-box-actions">
                                    ${editActionsHtml(msgId, p, post, 'tags')}
                                    <button class="btn-copy-sm btn-copy-text" id="${tagsCardId}_copy" data-text="${escapeAttr(post.tags.join(' '))}">
                                        <i class="fas fa-copy me-1"></i>Copy Tags
                                    </button>
                                </span>
                            </div>
                            <div class="hashtags-container" id="${tagsCardId}">${tagsHtml}</div>
                        </div>
                    </details>
                    </div>`;
    }

    // A /carousel's caption (shared by its slides), or a /hiring post's
    function carouselTextBlockHtml(msgId) {
        const post = postTextOf(msgId, 'carousel');
        const text = post ? window.PostPreview.postText(post.caption, post.tags) : '';
        if (!text) return '';
        const hiring = !!post.node.job;
        return `
            <div class="preset-adcopy post-text-block" id="${msgId}_text_carousel">
                <div class="preset-adcopy-head"><span><i class="fas ${hiring ? 'fa-user-plus' : 'fa-images'} me-1"></i>${hiring ? 'Job post caption' : 'Carousel caption'}${editedBadgeHtml(post)}</span>
                    <span class="post-box-actions">${editActionsHtml(msgId, 'carousel', post)}
                    <button type="button" class="preset-copy" data-copy="${escapeAttr(text)}"><i class="fas fa-copy me-1"></i>Copy</button></span></div>
                <div class="post-caption-text">${escapeHtml(post.caption)}</div>
                ${post.tags.length ? `<div class="hashtags-container mt-2">${post.tags.map(t => `<span class="hashtag-pill">${escapeHtml(t)}</span>`).join(' ')}</div>` : ''}
            </div>
            ${hiring && post.node.compliance ? buildComplianceHtml(post.node.compliance) : ''}`;
    }

    function refreshTextBlock(msgId, target) {
        // An image command's card has nothing still loading, so it is simply drawn again
        if (target === 'carousel') { renderAssistantResponse(msgId); return; }
        $(`#${msgId}_text_${target}`).replaceWith(postTextBlockHtml(msgId, target));
    }

    function postEditorHtml(msgId, target, post) {
        const id = `${msgId}_edit_${target}`;
        const name = target === 'carousel' ? (post.node.job ? 'job' : 'carousel') : window.PostPreview.platformName(target);
        return `
            <div class="post-text-block post-text-flat" id="${msgId}_text_${target}">
            <div class="post-box-card post-editor" data-msg="${msgId}" data-target="${target}">
                <div class="post-box-header">
                    <span class="post-box-title"><i class="fas fa-pen me-1"></i>Edit ${escapeHtml(name)} post</span>
                    <span class="post-editor-hint">Your changes are saved to this post. Nothing is regenerated.</span>
                </div>
                <label class="post-editor-label" for="${id}_caption">Caption</label>
                <textarea class="post-editor-input post-editor-caption" id="${id}_caption" rows="6">${escapeHtml(post.caption)}</textarea>
                <label class="post-editor-label" for="${id}_tags">Hashtags <span>separated by spaces</span></label>
                <textarea class="post-editor-input post-editor-tags" id="${id}_tags" rows="2">${escapeHtml(post.tags.join(' '))}</textarea>
                <div class="post-editor-error" role="alert" hidden></div>
                <div class="post-editor-bar">
                    <span class="post-editor-count" aria-live="polite"></span>
                    <span class="post-editor-buttons">
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-edit-cancel">Cancel</button>
                        <button type="button" class="btn btn-sm btn-outline-primary btn-edit-save"><i class="fas fa-check me-1"></i>Save</button>
                    </span>
                </div>
            </div>
            </div>`;
    }

    function growEditorInput(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight + 2, 480) + 'px';
    }

    // Live count against the platform's limit (caption and hashtags are posted together)
    function updateEditorCount($ed) {
        const PP = window.PostPreview;
        const caption = $ed.find('.post-editor-caption').val();
        const tags = PP.cleanHashtags($ed.find('.post-editor-tags').val());
        const length = textLength(PP.postText(caption, tags)), limit = PP.limitFor($ed.data('target'));
        const over = length > limit, tooMany = tags.length > PP.MAX_HASHTAGS;
        $ed.find('.post-editor-count').toggleClass('is-over', over || tooMany).text(
            `${length.toLocaleString()} / ${limit.toLocaleString()} characters · ${tags.length} hashtag${tags.length === 1 ? '' : 's'}`
            + (over ? ` · ${(length - limit).toLocaleString()} over the limit` : tooMany ? ` · at most ${PP.MAX_HASHTAGS}` : ''));
        $ed.find('.btn-edit-save').prop('disabled', over || tooMany || !caption.trim());
    }

    function openPostEditor(msgId, target, focus) {
        const post = postTextOf(msgId, target);
        if (!post || !post.runId) return;
        $(`#${msgId}_text_${target}`).replaceWith(postEditorHtml(msgId, target, post));
        const $ed = $(`#${msgId}_text_${target} .post-editor`);
        $ed.find('.post-editor-input').each(function () { growEditorInput(this); });
        updateEditorCount($ed);
        const field = $ed.find(focus === 'tags' ? '.post-editor-tags' : '.post-editor-caption')[0];
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
    }

    // "Send for approval" again after an edit: the request already sent holds the old text
    function refreshApprovalButton(msgId) {
        const $footer = $(`#${msgId} .assistant-card-footer`);
        $footer.find('.btn-send-approval, a[href^="/approve/"]').first().replaceWith(approvalButtonHtml(msgId));
        $footer.find('.btn-send-approval').off('click').on('click', () => openApproval(msgId));
    }

    function savePostEdit(msgId, target, body, $ed) {
        const post = postTextOf(msgId, target);
        const $busy = $ed ? $ed.find('.btn-edit-save, .btn-edit-cancel') : $();
        $busy.prop('disabled', true);
        $.ajax({
            url: `/api/history/${post.runId}/edit`, type: 'POST', contentType: 'application/json',
            data: JSON.stringify(Object.assign({ target: target }, body)),
        }).done(function (r) {
            setPostNode(msgId, target, r.node);
            const h = window.chatHistory[msgId];
            const wasSent = h.approval && h.approval[h.currentIndex];
            if (wasSent) delete h.approval[h.currentIndex];
            refreshTextBlock(msgId, target);
            if (wasSent && target !== 'carousel') refreshApprovalButton(msgId);
            if (refineBaseMsgId === msgId) adoptAsRefineBase(msgId);  // the next message edits this text
            showToast(body.restore ? 'Back to the original text.' : (wasSent ? 'Saved. Send it for approval again to share the new text.' : 'Saved.'), 'success');
        }).fail(function (xhr) {
            const message = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not save your changes. Please try again.';
            if ($ed) {
                $ed.find('.post-editor-error').text(message).prop('hidden', false);
                $busy.prop('disabled', false);
                updateEditorCount($ed);
            } else {
                showToast(message, 'error');
            }
        });
    }

    $(document).on('click', '.btn-edit-post', function () {
        openPostEditor($(this).data('msg'), $(this).data('target'), $(this).data('focus'));
    });
    $(document).on('input', '.post-editor-input', function () {
        growEditorInput(this);
        const $ed = $(this).closest('.post-editor');
        $ed.find('.post-editor-error').prop('hidden', true);
        updateEditorCount($ed);
    });
    $(document).on('click', '.btn-edit-cancel', function () {
        const $ed = $(this).closest('.post-editor');
        refreshTextBlock($ed.data('msg'), $ed.data('target'));
    });
    $(document).on('click', '.btn-edit-save', function () {
        const $ed = $(this).closest('.post-editor');
        savePostEdit($ed.data('msg'), $ed.data('target'),
            { caption: $ed.find('.post-editor-caption').val(), hashtags: $ed.find('.post-editor-tags').val() }, $ed);
    });
    $(document).on('keydown', '.post-editor-input', function (e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {  // Ctrl/Cmd + Enter saves
            e.preventDefault();
            $(this).closest('.post-editor').find('.btn-edit-save:not(:disabled)').trigger('click');
        }
    });
    $(document).on('click', '.btn-restore-post', function () {
        savePostEdit($(this).data('msg'), $(this).data('target'), { restore: true }, null);
    });

    // ── "Preview": the post as each platform shows it ─────────────────────
    // (the cards of the approval review page: static/js/post_preview.js)
    const PREVIEW_ICONS = { facebook: 'fab fa-facebook', instagram: 'fab fa-instagram', linkedin: 'fab fa-linkedin', youtube: 'fab fa-youtube' };
    const previewState = { posts: [], active: 0, slide: 0 };

    // What a message would post, per platform: {platform, caption, hashtags, images, media, slideTitles, on}.
    // An image command has no platform of its own: a carousel goes to LinkedIn
    // and Instagram as slides, other commands to Instagram and Facebook as one image.
    function postsForMessage(msgId) {
        const h = window.chatHistory[msgId];
        const content = (h.responses[h.currentIndex] || {}).content || {};
        const meta = content._preset;
        if (meta && meta.hiring) {
            // /hiring: the square image for LinkedIn and Facebook, the portrait for Instagram
            const url = k => content[k]?.media?.image?.url;
            const square = url('feed') || url('portrait'), tall = url('portrait') || square;
            return ['linkedin', 'instagram', 'facebook'].map(p => ({
                platform: p, caption: meta.hiring.caption || '', hashtags: meta.hiring.hashtags || [],
                images: [p === 'instagram' ? tall : square].filter(Boolean), media: 'single', on: true,
            }));
        }
        if (meta) {
            const images = h.platforms.map(k => content[k]?.media?.image?.url).filter(Boolean);
            const car = meta.carousel;
            const titles = car ? h.platforms.map(k => ((car.slides || []).find(sl => sl.key === k) || {}).title || '') : [];
            const base = { caption: car ? car.caption : '', hashtags: car ? car.hashtags || [] : [] };
            return car
                ? ['linkedin', 'instagram', 'facebook'].map(p => ({ ...base, platform: p, images, media: 'carousel', slideTitles: titles, on: p !== 'facebook' }))
                : ['instagram', 'facebook', 'linkedin'].map(p => ({ ...base, platform: p, images: images.slice(0, 1), media: 'single', on: p !== 'linkedin' }));
        }
        return h.platforms.map(p => {
            const pData = content[p] || {};
            const url = pData.media?.image?.url;
            return { platform: p, caption: pData.caption?.primary_caption || '', hashtags: getTagList(pData.hashtags),
                     images: url ? [url] : [], media: url ? 'single' : undefined };
        });
    }

    function previewMediaHtml(post) {
        const images = post.images || [];
        const square = post.platform === 'instagram';
        if (!images.length) return '';
        if (post.media !== 'carousel' || images.length < 2) return window.PostPreview.imageHtml(images[0], square);
        const i = Math.min(previewState.slide, images.length - 1);
        return `
            <div class="ap-carousel">
                ${window.PostPreview.imageHtml(images[i], square)}
                <span class="ap-carousel-count">${i + 1} / ${images.length}</span>
                <button type="button" class="ap-carousel-nav prev pp-slide" data-step="-1" ${i === 0 ? 'disabled' : ''} aria-label="Previous slide"><i class="fas fa-chevron-left"></i></button>
                <button type="button" class="ap-carousel-nav next pp-slide" data-step="1" ${i === images.length - 1 ? 'disabled' : ''} aria-label="Next slide"><i class="fas fa-chevron-right"></i></button>
            </div>`;
    }

    function renderPostPreview() {
        const PP = window.PostPreview;
        const posts = previewState.posts, post = posts[previewState.active];
        const text = PP.postText(post.caption, post.hashtags);
        const length = textLength(text), limit = PP.limitFor(post.platform);
        $('#ppTabs').html(posts.length > 1 ? posts.map((p, i) => `
            <button type="button" role="tab" class="pp-tab${i === previewState.active ? ' active' : ''}" data-index="${i}" aria-selected="${i === previewState.active}">
                <i class="${PREVIEW_ICONS[p.platform] || 'fas fa-share'} me-1"></i>${PP.platformName(p.platform)}</button>`).join('') : '');
        $('#ppStage').html(PP.cardHtml(post.platform, {
            text: text, mediaHtml: previewMediaHtml(post), brand: _brandProfileCompanyName || 'Your brand', fold: true,
        }));
        const notes = [`<span class="${length > limit ? 'is-over' : ''}">${length.toLocaleString()} / ${limit.toLocaleString()} characters</span>`];
        if (post.hashtags.length) notes.push(`${post.hashtags.length} hashtag${post.hashtags.length === 1 ? '' : 's'}`);
        if (post.media === 'carousel') notes.push(`${post.images.length} slides`);
        if (post.platform === 'instagram' && !post.images.length) notes.push('<span class="is-over"><i class="fas fa-triangle-exclamation me-1"></i>Instagram posts need an image</span>');
        $('#ppMeta').html(notes.join(' &middot; '));
    }

    function openPostPreview(msgId) {
        if (!window.chatHistory[msgId] || !window.PostPreview) return;
        const h = window.chatHistory[msgId];
        const meta = ((h.responses[h.currentIndex] || {}).content || {})._preset;
        previewState.posts = postsForMessage(msgId).map(post =>
            // an ad's own text stands in for a caption it doesn't have
            (!post.caption && meta && meta.ad_copy) ? { ...post, caption: meta.ad_copy.primary_text || '' } : post);
        if (!previewState.posts.length) return;
        // Open on the platform tab being looked at in the chat
        const shown = ($(`#${msgId} .platform-tab-chat.active`).attr('data-target') || '').replace(`${msgId}_tab_`, '');
        previewState.active = Math.max(0, previewState.posts.findIndex(p => p.platform === shown));
        previewState.slide = 0;
        if (!document.getElementById('postPreviewModal')) {
            $('body').append(`
                <div class="modal fade" id="postPreviewModal" tabindex="-1" aria-labelledby="postPreviewTitle" aria-hidden="true">
                    <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable pp-dialog">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title" id="postPreviewTitle"><i class="fas fa-eye me-2"></i>Post preview</h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                            </div>
                            <div class="modal-body">
                                <div class="pp-tabs" id="ppTabs" role="tablist"></div>
                                <div class="pp-stage" id="ppStage"></div>
                                <div class="pp-meta" id="ppMeta"></div>
                                <p class="pp-note">This is roughly how the post looks in the feed. The likes and comments are placeholders, and where "more" cuts the caption varies by device.</p>
                            </div>
                        </div>
                    </div>
                </div>`);
        }
        renderPostPreview();
        bootstrap.Modal.getOrCreateInstance(document.getElementById('postPreviewModal')).show();
    }

    $(document).on('click', '.btn-preview-post', function () { openPostPreview($(this).data('msg')); });
    // Drawing the preview again replaces the button that was clicked: focus goes
    // back to its replacement, so the keyboard (Tab, Esc) keeps working in the dialog
    $(document).on('click', '#ppTabs .pp-tab', function () {
        previewState.active = Number($(this).data('index'));
        previewState.slide = 0;
        renderPostPreview();
        $('#ppTabs .pp-tab.active').trigger('focus');
    });
    $(document).on('click', '#ppStage .pp-slide', function () {
        const step = Number($(this).data('step'));
        previewState.slide += step;
        renderPostPreview();
        const $same = $(`#ppStage .pp-slide.${step > 0 ? 'next' : 'prev'}:not(:disabled)`);
        ($same.length ? $same : $('#ppStage .pp-slide:not(:disabled)')).first().trigger('focus');
    });

    // An error with "Try again" when trying again can help (no connection,
    // the AI service timed out or is busy); otherwise the server's own message.
    function errorCardHtml(title, xhr, errText, note) {
        const retry = xhr.status === 0 || xhr.status >= 500 || xhr.status === 429 || xhr.status === 408;
        return `
            <div class="gen-error-card" role="alert">
                <div class="gen-error-title"><i class="fas fa-circle-exclamation me-2"></i>${escapeHtml(title)}</div>
                <p class="gen-error-text">${escapeHtml(retry ? friendlyGenerationError(xhr, errText) : errText)}</p>
                ${retry ? `
                <div class="gen-error-actions">
                    <button type="button" class="btn-chat-send gen-retry-btn"><i class="fas fa-rotate-right me-2"></i>Try again</button>
                </div>
                ${note ? `<p class="gen-error-note">${escapeHtml(note)}</p>` : ''}
                <details class="gen-error-details"><summary>Technical details</summary><code>${escapeHtml(errText)}</code></details>` : ''}
            </div>`;
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
        const costUsd = hasUsage ? window.avirMoney(usage.cost_usd, (window.__avirMoney || {}).currency === 'INR' ? 2 : 4) : '';
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
        // Folded away until the "N Agents Active" button opens it; nothing at all
        // when no agents were recorded (it used to show an empty box)
        let agentBreakdownHtml = agentsCount ? `<div class="agent-pipeline-breakdown d-none" id="${msgId}_pipeline_panel">` : '';
        if (agentsCount) agentBreakdownHtml += `<div class="fw-bold mb-1 text-primary">Agents Engaged in this Turn:</div>`;
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
        if (agentsCount) agentBreakdownHtml += `</div>`;

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

            panelsHtml += `
                <div class="platform-panel-item" id="${msgId}_tab_${p}" style="display: ${displayStyle}">

                    ${postTextBlockHtml(msgId, p)}

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
                        ` : `<div id="${msgId}_media_image_${p}"></div>`}
                        ${pData.media?.video?.url ? buildGeneratedMediaHtml('video', pData.media.video)
                            : (selectedOutputs || []).includes('video') ? `
                        <div id="${msgId}_media_video_${p}">${videoCreatingHtml()}</div>
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
                <span class="small font-weight-bold">Version ${currentGen} of ${totalGens}</span>
                <i class="fas fa-chevron-right cursor-pointer text-secondary ms-2 gen-next" data-msg="${msgId}" ${currentGen === totalGens ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
            </div>
        ` : '';

        // Ready-to-publish layout: the checklist (renderChecklist) and the
        // Schedule / Send for approval buttons on the left, the post on the right
        const cardContent = `
            <div class="assistant-header">
                <div class="assistant-title d-flex align-items-center">
                    <div title="${runId ? 'Run #' + runId : ''}"><i class="fas fa-sparkles text-primary me-1"></i>AVIR Studio Output</div>
                    ${paginationHtml}
                </div>
                <div class="assistant-meta-tags">
                    ${qualityBadgeHtml}
                    ${pipelineBadgeHtml}
                    ${memBadgeHtml}
                    ${costBadgeHtml}
                </div>
            </div>
            ${agentBreakdownHtml}
            <div class="out-layout">
                <aside class="out-side" aria-label="Ready to publish">
                    <div class="out-check" id="${msgId}_checklist" aria-live="polite"></div>
                    <div class="assistant-card-footer action-row out-cta">
                        ${scheduleButtonHtml(msgId)}
                        ${approvalButtonHtml(msgId)}
                    </div>
                </aside>
                <div class="out-main">
            ${tabsHtml}
            ${panelsHtml}
            ${outMoreRowHtml(msgId, `
                <button class="dropdown-item btn-regenerate" data-msg="${msgId}"
                    title="${historyObj.refine ? 'Apply the same change again, as a new version' : 'Write the caption again, as a new version. Your image is kept.'}">
                    <i class="fas fa-sync-alt me-2"></i>${historyObj.refine ? 'Redo this change' : 'Rewrite caption'}
                </button>
                <button class="dropdown-item btn-refine-base" data-msg="${msgId}" title="Your next message will edit this version">
                    <i class="fas fa-wand-magic-sparkles me-2"></i>${refineBaseMsgId === msgId ? 'Refining this version' : 'Refine this version'}
                </button>`)}
                </div>
            </div>
        `;

        elem.find('.assistant-card').html(cardContent);
        elem.data('outPlatform', platforms[0]).removeData('outEyebrow');
        watchChecklist(msgId);

        elem.find('.btn-send-approval').on('click', () => openApproval(msgId));

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
            elem.data('outPlatform', targetId.split('_tab_').pop());
            renderChecklist(msgId);
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
                assistantElem.replaceWith(appendRefineThinking(mId, h.refine && h.refine.selectedOutputs));
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
    // Video placeholder: a dark 16:9 "screen" with slow moving light, the
    // current stage, elapsed time and an estimated progress line. A video
    // takes 10-15 minutes, so the stage and the line follow the clock (the
    // server reports nothing until the video is done).
    const VIDEO_STAGES = [
        [0, 'Reading your brief'],
        [20, 'Composing the scene'],
        [90, 'Rendering frames'],
        [420, 'Adding motion and sound'],
        [660, 'Finishing your video']
    ];
    function videoCreatingHtml() {
        return `
            <div class="vid-creating" data-started="${Date.now()}" role="status" aria-live="polite">
                <div class="vid-creating-glow" aria-hidden="true"><span></span><span></span><span></span></div>
                <div class="vid-creating-grain" aria-hidden="true"></div>
                <div class="vid-creating-badge"><i class="fas fa-video"></i>Video</div>
                <div class="vid-creating-play" aria-hidden="true"><i class="fas fa-play"></i></div>
                <div class="vid-creating-foot">
                    <div class="vid-creating-caption">
                        <span class="vid-creating-label">${VIDEO_STAGES[0][1]}</span>
                        <span class="vid-creating-time">0s</span>
                    </div>
                    <div class="vid-creating-bar" aria-hidden="true"><span></span></div>
                    <div class="vid-creating-hint">Usually takes 10 to 15 minutes. Keep this tab open.</div>
                </div>
            </div>`;
    }
    setInterval(function () {
        $('.vid-creating').each(function () {
            const secs = Math.floor((Date.now() - Number(this.dataset.started || Date.now())) / 1000);
            const stage = VIDEO_STAGES.filter(st => secs >= st[0]).pop()[1];
            const $label = $(this).find('.vid-creating-label');
            if ($label.text() !== stage) $label.text(stage);
            $(this).find('.vid-creating-time').text(secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, '0')}s`);
            // Eases toward the end and never reaches it: about 80% at ten minutes
            $(this).find('.vid-creating-bar span').css('width', (96 * (1 - Math.exp(-secs / 360))).toFixed(1) + '%');
            if (secs >= 900) $(this).find('.vid-creating-hint').text('Taking longer than usual - still working, keep this tab open.');
        });
    }, 1000);

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
        if (q.blocked_by === 'month') {
            return `You've used all the images in your ${q.plan ? q.plan.charAt(0).toUpperCase() + q.plan.slice(1) : ''} plan for this month. `
                + 'Upgrade your plan or ask your admin for an image pack.';
        }
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

    // Video is "coming soon" unless an admin switched it on for this user
    // (Admin -> Users -> Image access -> Video); admins always have it.
    // lock: null (usable), or {tag, title} - the plan has no video ("Starter"),
    // or no videos are left ("0 left"); title explains it on hover
    window.applyVideoAccess = function (enabled, lock) {
        window.__videoEnabled = !!enabled && !lock;
        window.__videoLock = lock || null;
        $('.video-gate').each(function () {
            const $input = $(this).find('input');
            const usable = !!enabled && !lock;
            const tag = !enabled ? 'Soon' : (lock ? lock.tag : '');
            $(this).toggleClass('coming-soon', !usable)
                .attr('title', usable ? null : (!enabled ? 'Coming soon' : lock.title));
            $(this).find('.soon-tag').text(tag).toggleClass('d-none', usable);
            if (!usable && $input.prop('checked')) $input.prop('checked', false).trigger('change');
            $input.prop('disabled', !usable);
        });
    };

    // What the Video option should say for this user's plan and video credits
    function videoLock(r) {
        const v = r && r.video_quota;
        if (!v || !v.enabled) return null;
        if (r.features && r.features.video === false) {
            const plan = (r.upgrade_plans || {}).video || 'a higher';
            return { tag: plan, title: `Video is on the ${plan} plan and above` };
        }
        if (v.blocked_by === 'month') return { tag: '0 left', title: 'No videos left - ask your admin for a video pack' };
        return null;
    }

    function loadImageQuota() {
        // kept so an emailed idea waits for it before ticking Image / Video (openEmailedIdea)
        window.__quotaLoaded = $.getJSON('/api/me/image-quota').done(function (r) {
            window.__planFeatures = (r && r.features) || null;
            window.__upgradePlans = (r && r.upgrade_plans) || {};
            window.applyImageQuota(r && r.quota);
            window.applyVideoAccess(!!(r && r.video_quota && r.video_quota.enabled), videoLock(r));
            // approval buttons drawn before the plan was known
            $('.btn-send-approval').each(function () {
                if (window.__planFeatures && !window.__planFeatures.approvals) $(this).replaceWith(approvalButtonHtml($(this).data('msg')));
            });
        });
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
    // /carousel: "7 slides on ..." -> 7 (3-10, default 5) - same rule as services/carousel_service.slide_count
    function carouselSlideCount(text) {
        const m = /\b(\d{1,2})\s*(?:slides?|cards?|pages?)\b/i.exec(text || '');
        return m ? Math.max(3, Math.min(10, Number(m[1]))) : 5;
    }
    function presetImageCount(p, allSizes, text) {
        if (!p) return 0;
        if (p.carousel) return carouselSlideCount(text != null ? text : storyInput.val());
        return (allSizes ?? presetAllSizes) && p.all_sizes_images ? p.all_sizes_images : p.images;
    }
    function presetSizeLabels(p, allSizes, text) {
        if (p.carousel) return Array.from({ length: carouselSlideCount(text) }, (_, i) => `slide ${i + 1}`);
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
                <span class="slash-meta">${p.carousel ? '3-10 slides + caption' : `${p.images} image${p.images === 1 ? '' : 's'}${p.ad_copy ? ' + ad copy' : ''}${p.hiring ? ' + caption' : ''}`}</span>
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
            status = `<span class="preset-status">${p.carousel ? `${need} slides + caption (type e.g. "7 slides" to change)` : `${need} image${need === 1 ? '' : 's'}`}${p.ad_copy ? ' + ad copy' : ''}${p.hiring ? ' + job post caption · a new design each time' : ''}`
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
    storyInput.on('input', () => { if (activePreset && activePreset.carousel) renderPresetBar(); });
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
        const labels = presetSizeLabels(p, allSizes, text);
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
                    inner = errorCardHtml("The images couldn't be created", xhr, body.error || 'The images could not be created.',
                        'Images already made before the error may still count towards today\'s limit.');
                }
                $(`#${msgId} .assistant-card`).html(`
                    <div class="assistant-header"><div class="assistant-title"><i class="fas ${escapeAttr(p.icon)}"></i>${escapeHtml(p.label)}</div></div>${inner}`);
                $(`#${msgId} .gen-retry-btn`).on('click', function () {
                    runPreset(text, { msgId: msgId, preset: p, imagePath: imagePath, allSizes: allSizes, productNotes: notes });
                });
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
                <span class="small font-weight-bold">Version ${cur} of ${total}</span>
                <i class="fas fa-chevron-right cursor-pointer text-secondary ms-2 gen-next" data-msg="${msgId}" ${cur === total ? 'style="opacity: 0.5; pointer-events: none;"' : ''}></i>
            </div>` : '';
        const tiles = h.platforms.map(k => {
            const img = content[k]?.media?.image;
            if (!img) return '';
            if (img.limit_reached) return `<div class="preset-tile">${imageLimitCardHtml()}</div>`;
            const slide = ((meta.carousel || {}).slides || []).find(sl => sl.key === k);
            const sizeLabel = { feed: 'Feed', portrait: 'Feed', story: 'Story', billboard: 'Billboard', showcase: 'Showcase', lifestyle: 'Lifestyle', catalog: 'Catalog', festive: 'Festive' }[k] || capitalize(k);
            const name = slide ? `Slide ${k.replace('slide', '')} · ${slide.title}` : `${sizeLabel}${img.aspect ? ' ' + img.aspect : ''}`;
            return `
                <figure class="preset-tile" ${img.aspect ? `data-aspect="${escapeAttr(img.aspect)}"` : ''}>
                    <img class="media-output-img preset-img img-reveal" src="${escapeAttr(img.url)}" alt="${escapeAttr(label + ' - ' + name)}" loading="lazy">
                    <figcaption>
                        <span>${escapeHtml(name)}${img.width ? ` · ${img.width}×${img.height}` : ''}</span>
                        <span class="preset-tile-actions">
                            <button type="button" class="preset-mini" data-preview="${escapeAttr(img.url)}" title="Enlarge" aria-label="Enlarge"><i class="fas fa-expand"></i></button>
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
        const car = meta.carousel;
        const carHtml = carouselTextBlockHtml(msgId);
        const usage = r.usage;
        const costHtml = usage && usage.cost_usd
            ? `<span class="badge-cost-tag me-1" id="${msgId}_cost_badge" title="Images${ad ? ' + ad copy' : ''}">${costBadgeInner(usage)}</span>` : '';
        const elem = $(`#${msgId}`);
        // Same ready-to-publish layout as a post: checklist + Schedule on the left
        elem.find('.assistant-card').html(`
            <div class="assistant-header">
                <div class="assistant-title"><div title="${r.runId ? 'Run #' + r.runId : ''}"><i class="fas ${escapeAttr(icon)} text-primary me-1"></i>${escapeHtml(label)}${meta.occasion ? ` · ${escapeHtml(meta.occasion)}` : ''}</div>${pager}</div>
                <div class="assistant-meta-tags">${costHtml}</div>
            </div>
            <div class="out-layout">
                <aside class="out-side" aria-label="Ready to publish">
                    <div class="out-check" id="${msgId}_checklist" aria-live="polite"></div>
                    <div class="assistant-card-footer action-row out-cta">
                        ${scheduleButtonHtml(msgId)}
                        ${approvalButtonHtml(msgId)}
                    </div>
                </aside>
                <div class="out-main">
            ${meta.text ? `<div class="preset-brief"><i class="fas fa-quote-left me-1"></i>${escapeHtml(meta.text)}</div>` : ''}
            ${meta.hiring && meta.hiring.design_names ? `<div class="preset-brief"><i class="fas fa-palette me-1"></i>Design: ${escapeHtml(meta.hiring.design_names.layout)} · ${escapeHtml(meta.hiring.design_names.scene)} · ${escapeHtml(meta.hiring.design_names.treatment)}. <strong>New images</strong> gives a different design. Check the words on the image before posting.</div>` : ''}
            ${meta.occasion_auto ? `<div class="preset-brief"><i class="fas fa-calendar-day me-1"></i>Made for the next festival, <strong>${escapeHtml(meta.occasion)}</strong>. For another occasion, type e.g. <em>/festive Eid</em>.</div>` : ''}
            <div class="preset-grid${h.platforms.length > 1 ? ' multi' : ''}">${tiles}</div>
            ${adHtml}${carHtml}
            ${outMoreRowHtml(msgId, `
                <button class="dropdown-item btn-preset-regen" data-msg="${msgId}" title="Create these images again, as a new version (uses today's images)"><i class="fas fa-sync-alt me-2"></i>New images</button>
                <button class="dropdown-item btn-refine-base" data-msg="${msgId}" title="Your next message will edit this version">
                    <i class="fas fa-wand-magic-sparkles me-2"></i>${refineBaseMsgId === msgId ? 'Refining this version' : 'Refine this version'}</button>`)}
            <div class="preset-tip"><i class="fas fa-lightbulb me-1"></i>Want a change? Just type it - e.g. "make the background darker".</div>
                </div>
            </div>`);
        elem.data('outEyebrow', label + (meta.occasion ? ` · ${meta.occasion}` : ''));
        watchChecklist(msgId);

        elem.find('.gen-prev, .gen-next').on('click', function () {
            h.currentIndex = Math.max(0, Math.min(h.responses.length - 1, h.currentIndex + ($(this).hasClass('gen-next') ? 1 : -1)));
            if (refineBaseMsgId === msgId) adoptAsRefineBase(msgId);
            renderAssistantResponse(msgId);
        });
        elem.find('[data-preview]').on('click', function () { window.openImagePreview($(this).data('preview')); });
        elem.find('.preset-copy').on('click', function () {
            navigator.clipboard.writeText($(this).data('copy')).then(() => showToast(car ? 'Caption copied' : 'Ad copy copied', 'success'));
        });
        elem.find('.btn-send-approval').on('click', () => openApproval(msgId));
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

    // The row under a post: ONE main action (Schedule), the other real choices
    // as quiet buttons, and edits tucked into "More" - fewer equal options
    // make the next step obvious.
    function actionRowHtml(msgId, moreItems) {
        return `
            <div class="assistant-card-footer action-row">
                ${scheduleButtonHtml(msgId)}
                ${approvalButtonHtml(msgId)}
                <button class="btn btn-sm btn-quiet btn-preview-post" data-msg="${msgId}" title="See it as it will look on each platform">
                    <i class="fas fa-eye me-1"></i>Preview
                </button>
                <div class="dropdown action-more">
                    <button class="btn btn-sm btn-quiet" type="button" data-bs-toggle="dropdown" aria-expanded="false" title="More actions">
                        <i class="fas fa-ellipsis me-1"></i>More
                    </button>
                    <div class="dropdown-menu dropdown-menu-end">${moreItems}</div>
                </div>
            </div>`;
    }

    // Under the post: Preview and "More" (Schedule and Send for approval live
    // with the checklist on the left)
    function outMoreRowHtml(msgId, moreItems) {
        return `
            <div class="out-more-row">
                <button class="btn btn-sm btn-quiet btn-preview-post" data-msg="${msgId}" title="See it as it will look on each platform">
                    <i class="fas fa-eye me-1"></i>Preview
                </button>
                <div class="dropdown action-more">
                    <button class="btn btn-sm btn-quiet" type="button" data-bs-toggle="dropdown" aria-expanded="false" title="More actions">
                        <i class="fas fa-ellipsis me-1"></i>More
                    </button>
                    <div class="dropdown-menu dropdown-menu-end">${moreItems}</div>
                </div>
            </div>`;
    }

    // ── Ready-to-publish checklist (the output card's left column) ─────────
    // Worked out from the post's data and what the card shows right now, so it
    // follows image generation, edits and scheduling as they happen.
    const OUT_NAMES = { linkedin: 'LinkedIn', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube', x: 'X' };
    let bestTimes = null;  // /api/best-times, asked once; until then the item just says "Choose when"
    let bestTimesAsked = false;
    function bestTimeFor(platform) {
        if (!bestTimesAsked) {
            bestTimesAsked = true;
            $.getJSON('/api/best-times?tz=' + (-new Date().getTimezoneOffset())).done(r => {
                bestTimes = r.platforms || {};
                $('.out-check').each(function () { renderChecklist(this.id.replace(/_checklist$/, '')); });
            });
        }
        return bestTimes ? bestTimes[platform] || null : null;
    }

    function imageState(msgId, p, pData, wanted) {
        const media = pData.media || {};
        const $slot = $(`#${msgId}_media_image_${p}`);
        if (media.image && media.image.limit_reached || $slot.find('.image-limit-card').length) return { state: 'warn', note: "Today's image limit is reached" };
        if ($slot.find('.img-creating').length) return { state: 'working', note: 'Being created…' };
        const url = $slot.find('img.media-output-img').attr('src') || (media.image && media.image.url);
        if (url) return { state: 'done', note: `Your logo placed · ${(media.image && media.image.resolution) || '1024×1024'}`, url: url };
        if ($slot.children().length && !$slot.find('img').length) return { state: 'warn', note: "Couldn't create the image", retry: true };
        return { state: wanted ? 'todo' : 'optional', note: p === 'instagram' ? 'Instagram posts need an image' : 'No image yet (optional)' };
    }

    function checklistItems(msgId, p) {
        const h = window.chatHistory[msgId];
        const post = postTextOf(msgId, p);
        if (!h || !post) return [];
        const pData = post.node || {};
        const items = [];
        const len = Array.from(post.caption || '').length;
        const limit = window.PostPreview && window.PostPreview.limitFor ? window.PostPreview.limitFor(p) : null;
        items.push(len
            ? { key: 'caption', state: limit && len > limit ? 'warn' : 'done', title: 'Caption written',
                note: `${len.toLocaleString()}${limit ? ' / ' + limit.toLocaleString() : ''} characters${post.node.edited ? ' · edited' : ''}`, act: post.runId ? 'Edit' : '' }
            : { key: 'caption', state: 'todo', title: 'Write a caption', note: 'No caption yet', act: post.runId ? 'Write' : '' });
        const tags = post.tags.length;
        items.push({ key: 'tags', state: tags ? (p === 'instagram' && tags > 30 ? 'warn' : 'done') : 'optional',
            title: tags ? 'Hashtags picked' : 'No hashtags', note: tags ? `${tags} for ${OUT_NAMES[p] || p}${p === 'instagram' && tags > 30 ? ' (30 at most)' : ''}` : 'Optional',
            act: post.runId ? 'Edit' : '' });
        const wantedImage = (h.selectedOutputs || []).includes('image') || p === 'instagram';
        const img = imageState(msgId, p, pData, wantedImage);
        items.push(Object.assign({ key: 'image', title: img.state === 'done' ? 'Image with your logo' : 'Image' }, img,
            { act: img.state === 'done' ? 'View' : img.retry ? 'Try again' : (img.state === 'todo' || img.state === 'optional') ? 'Create' : '' }));
        const brand = brandCheckItem(pData.compliance);
        if (brand) items.push(brand);
        const time = timeCheckItem(msgId, p);
        if (time) items.push(time);
        return items;
    }

    function brandCheckItem(c) {
        if (!c) return null;
        const attention = c.needs_attention || 0;
        return attention
            ? { key: 'brand', state: 'warn', title: 'Brand check', note: `${attention} item${attention > 1 ? 's need' : ' needs'} your attention`, act: 'Review' }
            : { key: 'brand', state: 'done', title: 'Brand check passed', note: `${c.rules_checked || 0} rules checked${(c.disclaimers_added || []).length ? ' · disclaimer added' : ''}`, act: 'Details' };
    }

    function timeCheckItem(msgId, p) {
        if (!window.SCHEDULING_ENABLED) return null;
        const h = window.chatHistory[msgId];
        const planned = h.scheduled && h.scheduled[h.currentIndex];
        if (planned) {
            const when = new Date(planned.at).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            return { key: 'time', state: 'done', title: 'Scheduled', note: when, act: '' };
        }
        const best = p ? bestTimeFor(p) : null;
        return { key: 'time', state: 'todo', title: 'Pick a time',
            note: best && best.next_slot
                ? 'Next good slot: ' + new Date(best.next_slot).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
                : 'Choose when it goes out', act: 'Schedule' };
    }

    // An image command (/metaad, /festive, /carousel, hiring post...): its images,
    // the ad copy or caption that came with them, then the time
    function presetChecklistItems(msgId) {
        const h = window.chatHistory[msgId];
        const r = h.responses[h.currentIndex] || {};
        const content = r.content || {};
        const meta = content._preset || h.presetRun || {};
        const images = h.platforms.map(k => (content[k] || {}).media?.image).filter(Boolean);
        const made = images.filter(i => i.url && !i.limit_reached).length;
        const limited = images.some(i => i.limit_reached);
        const items = [{
            key: 'images', state: limited ? 'warn' : made ? 'done' : 'todo',
            title: made ? `${made} image${made === 1 ? '' : 's'} made` : 'Images',
            note: limited ? "Today's image limit is reached" : made ? 'Your logo placed on each' : 'No images yet', act: 'New images',
        }];
        const ad = meta.ad_copy;
        if (ad) items.push({ key: 'adcopy', state: 'done', title: 'Ad copy written', note: `Headline: ${ad.headline || ''}`, act: 'Copy' });
        const post = postTextOf(msgId, 'carousel');
        if (post && post.caption) {
            const job = !!post.node.job;
            items.push({ key: 'carousel', state: 'done', title: job ? 'Job post caption' : 'Caption written',
                note: `${Array.from(post.caption).length.toLocaleString()} characters${post.tags.length ? ` · ${post.tags.length} hashtags` : ''}${post.node.edited ? ' · edited' : ''}`,
                act: post.runId ? 'Edit' : '' });
            const brand = brandCheckItem(job ? post.node.compliance : null);
            if (brand) items.push(brand);
        }
        const time = timeCheckItem(msgId, null);
        if (time) items.push(time);
        return items;
    }

    function isPresetMessage(msgId) {
        const h = window.chatHistory[msgId];
        return !!(h && (h.preset || ((h.responses[h.currentIndex] || {}).content || {})._preset));
    }

    function renderChecklist(msgId) {
        const $box = $(`#${msgId}_checklist`);
        if (!$box.length) return;
        const p = $(`#${msgId}`).data('outPlatform');
        const preset = isPresetMessage(msgId);
        const items = preset ? presetChecklistItems(msgId) : checklistItems(msgId, p);
        const eyebrow = preset ? ($(`#${msgId}`).data('outEyebrow') || 'Image command') : `${OUT_NAMES[p] || p || ''} post`;
        const counted = items.filter(i => i.state !== 'optional');
        const done = counted.filter(i => i.state === 'done').length;
        const working = items.some(i => i.state === 'working');
        const warn = items.some(i => i.state === 'warn');
        const title = working ? 'Finishing your post…' : done === counted.length ? 'Ready to publish' : warn ? 'Needs a quick look' : 'Almost ready';
        const marks = { done: '<i class="fas fa-check"></i>', working: '<i class="fas fa-spinner fa-spin"></i>', warn: '!', todo: '', optional: '' };
        const pct = counted.length ? Math.round(done / counted.length * 100) : 0;
        $box.html(`
            <p class="out-eyebrow">${escapeHtml(eyebrow)}</p>
            <h3 class="out-title">${title}</h3>
            <p class="out-sub">${done} of ${counted.length} checks done</p>
            <div class="out-progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Checks done"><span style="width:${pct}%"></span></div>
            <ul class="out-items">
                ${items.map(i => `
                <li class="out-item is-${i.state}">
                    <span class="out-mark" aria-hidden="true">${marks[i.state]}</span>
                    <span class="out-text"><strong>${escapeHtml(i.title)}</strong><small>${escapeHtml(i.note || '')}</small></span>
                    ${i.act ? `<button type="button" class="out-act" data-msg="${msgId}" data-platform="${escapeAttr(p)}" data-key="${i.key}"${i.url ? ` data-url="${escapeAttr(i.url)}"` : ''}>${escapeHtml(i.act)}</button>` : ''}
                </li>`).join('')}
            </ul>`);
    }

    // Re-check whenever the post's content changes (image done, edit saved, ...)
    function watchChecklist(msgId) {
        const elem = $(`#${msgId}`);
        const old = elem.data('outObserver');
        if (old) old.disconnect();
        renderChecklist(msgId);
        const panel = elem.find('.out-main')[0];
        if (!panel || !window.MutationObserver) return;
        let queued = false;
        const observer = new MutationObserver(() => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => { queued = false; renderChecklist(msgId); });
        });
        observer.observe(panel, { childList: true, subtree: true });
        elem.data('outObserver', observer);
    }

    // A checklist item's button does what the card's own controls do
    $(document).on('click', '.out-act', function () {
        const msgId = $(this).data('msg'), p = $(this).data('platform'), key = $(this).data('key');
        const $tab = $(`#${msgId}_tab_${p}`);
        const $panel = $tab.length ? $tab : $(`#${msgId}`);  // an image command's card has no platform tabs
        const reveal = $el => { if ($el.length) $el[0].scrollIntoView({ block: 'center', behavior: 'smooth' }); };
        if (key === 'caption') $panel.find('.btn-edit-post').not('[data-focus]').first().trigger('click');
        else if (key === 'tags') {
            const $line = $panel.find('.hashtag-line').attr('open', true);
            const $edit = $line.find('.btn-edit-post[data-focus]').first();
            if ($edit.length) $edit.trigger('click'); else reveal($line);
        } else if (key === 'image') {
            const url = $(this).data('url');
            if (url && typeof window.openImagePreview === 'function') window.openImagePreview(url);
            else if (typeof window.regenerateImage === 'function') window.regenerateImage(msgId, p);
        } else if (key === 'brand') {
            const $c = $panel.find('.compliance-line').attr('open', true);
            reveal($c);
        } else if (key === 'time') openSchedule(msgId);
        else if (key === 'images') $panel.find('.btn-preset-regen').first().trigger('click');
        else if (key === 'adcopy') $panel.find('.preset-adcopy:not(.post-text-block) .preset-copy').first().trigger('click');
        else if (key === 'carousel') $panel.find(`#${msgId}_text_carousel .btn-edit-post`).first().trigger('click');
    });

    // "Send for approval" (static/js/approval_send.js): after sending, the
    // button becomes a link to the request
    function approvalButtonHtml(msgId) {
        const h = window.chatHistory[msgId] || {};
        const sent = h.approval && h.approval[h.currentIndex];
        if (sent) {
            return `<a class="btn btn-sm btn-outline-success" href="/approve/${sent}" target="_blank" rel="noopener"><i class="fas fa-check me-1"></i>Sent for approval · View</a>`;
        }
        // Approvals come with some plans only (see /api/me/image-quota features)
        if (window.__planFeatures && !window.__planFeatures.approvals) {
            const plan = (window.__upgradePlans || {}).approvals || 'a higher';
            return `<a class="btn btn-sm btn-outline-secondary btn-plan-locked" href="/settings#plan"
                       title="Approvals are on the ${escapeAttr(plan)} plan and above"><i class="fas fa-lock me-1"></i>Send for approval<span class="plan-chip">${escapeHtml(plan)}</span></a>`;
        }
        return `<button class="btn btn-sm btn-quiet btn-send-approval" data-msg="${msgId}"><i class="fas fa-paper-plane me-1"></i>Send for approval</button>`;
    }

    function openApproval(msgId) {
        const h = window.chatHistory[msgId];
        if (!h || typeof window.openSendForApproval !== 'function') return;
        const r = h.responses[h.currentIndex] || {};
        const platforms = postsForMessage(msgId);
        window.openSendForApproval({
            key: `studio-${r.runId || msgId}`,
            story: (h.requestBody && h.requestBody.story) || '',
            conversationId: currentConversationId,
            platforms: platforms,
            onSent: function (req) {
                h.approval = h.approval || {};
                h.approval[h.currentIndex] = req.id;
                renderAssistantResponse(msgId);
            },
        });
    }

    // "Schedule" (static/js/approval_send.js): each platform's caption and media
    // are published by themselves at the chosen time. After scheduling, the
    // button shows when it goes out and opens the calendar.
    function scheduleButtonHtml(msgId) {
        const h = window.chatHistory[msgId] || {};
        const planned = h.scheduled && h.scheduled[h.currentIndex];
        if (planned) {
            const when = new Date(planned.at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            return `<a class="btn btn-sm btn-outline-success btn-scheduled" href="/calendar" title="Open the calendar to move or cancel it"><i class="fas fa-calendar-check me-1"></i>Scheduled · ${escapeHtml(when)}</a>`;
        }
        if (!window.SCHEDULING_ENABLED) return '';  // switched off with STUDIO_SCHEDULING=false: no dead-end button
        return `<button class="btn btn-sm btn-primary-action btn-schedule-post" data-msg="${msgId}" title="Publish it by itself at a time you choose"><i class="fas fa-calendar-plus me-1"></i>Schedule post</button>`;
    }

    // After scheduling: a clear "done" moment - when and where it goes out,
    // where the week stands, and one obvious next step.
    function showScheduledCard(msgId, post) {
        const names = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', youtube: 'YouTube' };
        const where = (post.platforms || []).map(p => names[p] || p);
        const when = new Date(post.scheduled_at_iso).toLocaleString(undefined, { weekday: 'long', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        const p = window.__weekProgress;
        const goalLine = !p ? '' : p.goal_met
            ? `<div class="sched-goal is-met"><i class="fas fa-trophy me-1"></i>Weekly goal reached: ${p.done} posts this week${p.streak_weeks > 1 ? ` &middot; ${p.streak_weeks}-week streak` : ''}</div>`
            : `<div class="sched-goal"><span class="sched-goal-dots">${Array.from({ length: p.goal }, (_, i) => `<i class="${i < p.done ? 'on' : ''}"></i>`).join('')}</span>
                   ${p.done} of ${p.goal} posts this week &middot; <strong>${p.remaining} to go</strong></div>`;
        const $card = $(`
            <div class="sched-success" role="status">
                <div class="sched-success-head"><span class="sched-check"><i class="fas fa-check"></i></span>
                    <div><strong>Scheduled</strong>
                        <div class="sched-when">${escapeHtml(when)}${where.length ? ' on ' + escapeHtml(where.join(', ')) : ''}. It goes out by itself.</div></div></div>
                ${goalLine}
                <div class="sched-actions">
                    <button type="button" class="btn btn-sm btn-primary-action sched-next"><i class="fas fa-plus me-1"></i>Start the next post</button>
                    <a class="btn btn-sm btn-quiet" href="/calendar"><i class="fas fa-calendar-days me-1"></i>See calendar</a>
                </div>
            </div>`);
        $(`#${msgId} .sched-success`).remove();
        $(`#${msgId} .action-row`).after($card);
        $card.find('.sched-next').on('click', function () {
            startNewChat(true);
            $('#storyInput').trigger('focus');
        });
        try { $card[0].scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { /* old browsers */ }
    }

    function refreshScheduleButton(msgId) {
        $(`#${msgId} .assistant-card-footer`).find('.btn-schedule-post, .btn-scheduled, .btn-schedule-soon').first().replaceWith(scheduleButtonHtml(msgId));
        renderChecklist(msgId);
    }

    function openSchedule(msgId) {
        const h = window.chatHistory[msgId];
        if (!h || !window.SCHEDULING_ENABLED || typeof window.openSchedulePost !== 'function') return;
        const r = h.responses[h.currentIndex] || {};
        window.openSchedulePost({
            key: `studio-${r.runId || msgId}`,
            story: (h.requestBody && h.requestBody.story) || '',
            conversationId: currentConversationId,
            runId: r.runId || null,
            platforms: postsForMessage(msgId),
            onScheduled: function (post) {
                h.scheduled = h.scheduled || {};
                h.scheduled[h.currentIndex] = { id: post.id, at: post.scheduled_at_iso };
                refreshScheduleButton(msgId);
                showScheduledCard(msgId, post);
            },
        });
    }
    $(document).on('click', '.btn-schedule-post', function () { openSchedule($(this).data('msg')); });

    // After a conversation is loaded: which of its posts are waiting to go out
    function loadScheduledMarks() {
        $.get('/api/social/scheduled').done(function (r) {
            const waiting = (r.scheduled_posts || []).filter(p => p.status === 'pending' && p.run_id);
            if (!waiting.length) return;
            Object.keys(window.chatHistory).forEach(function (msgId) {
                const h = window.chatHistory[msgId];
                (h.responses || []).forEach(function (response, index) {
                    const post = waiting.find(p => p.run_id === response.runId);
                    if (!post) return;
                    h.scheduled = h.scheduled || {};
                    h.scheduled[index] = { id: post.id, at: post.scheduled_at_iso };
                });
                if (h.scheduled) refreshScheduleButton(msgId);
            });
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
        const inr = (window.__avirMoney || {}).currency === 'INR';
        return `<i class="fas fa-bolt text-warning me-1"></i>${Number(usage.total_tokens || 0).toLocaleString()} tok | ${window.avirMoney(usage.cost_usd, inr ? 2 : 4)}`
            + (media > 0 ? `<span class="cost-media-note"> · incl. ${usage.media_count > 1 ? usage.media_count + ' images' : 'image'} ${window.avirMoney(media)}</span>` : '');
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
                share_with_platforms: shareWith.length ? shareWith : undefined,
                background: true  // made in the background; this page checks /api/media-jobs/<id>
            }),
            success: function (res) {
                if (res.job_id) { followMediaJob(res.job_id, showMediaResult); return; }
                showMediaResult(res);
            },
            error: function (xhr) { showMediaError(xhr); }
        });

        function showMediaResult(res) {
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
        }

        function showMediaError(xhr) {
                const body = xhr.responseJSON || {};
                if (xhr.status === 403 && body.code === 'image_limit_reached') {
                    const card = imageLimitCardHtml(body.quota || window.__imageQuota || {});
                    $('#' + targetSlotId).html(card);
                    shareWith.forEach(p => $(`#${msgId}_media_image_${p}`).html(card));
                    window.applyImageQuota(body.quota);
                    return;
                }
                if (xhr.status === 403 && ['video_limit_reached', 'video_not_enabled', 'video_pack_needed'].includes(body.code)) {
                    $('#' + targetSlotId).html(`
                        <div class="alert alert-warning py-2 px-3 small mt-2">
                            <i class="fas fa-hourglass-half me-1"></i>${escapeHtml(body.error || 'Video is not available right now.')}
                        </div>
                    `);
                    if (body.code === 'video_not_enabled') window.applyVideoAccess(false);
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
    }

    // A background render (services/media_jobs.py): checked every few seconds
    // until it is done. A dropped connection is retried; the result is also
    // saved on the post, so reopening the conversation later shows it.
    function followMediaJob(jobId, done) {
        let misses = 0;
        (function check() {
            $.getJSON(`/api/media-jobs/${encodeURIComponent(jobId)}`)
                .done(function (job) {
                    misses = 0;
                    if (job.status === 'done') return done(job.result || { success: false });
                    if (job.status === 'failed') return done({ success: false, error: job.error || 'The image or video could not be made.' });
                    setTimeout(check, 3000);
                })
                .fail(function (xhr) {
                    if (xhr.status === 404 || ++misses > 20) {
                        return done({ success: false, error: 'Lost track of this one. Reopen the conversation in a minute to see it.' });
                    }
                    setTimeout(check, 5000);
                });
        })();
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

    // ── History (sidebar) ──────────────────────────────────────────────
    // Pinned chats first, then the rest grouped by date. Library and Archived
    // sit at the bottom; Archived swaps the list for the archived chats.
    let currentHistoryTab = 'active';

    function showHistoryTab(tab) {
        currentHistoryTab = tab;
        const archived = tab === 'archived';
        $('#historyBackBtn').toggleClass('d-none', !archived);
        $('#historyArchivedBtn').toggleClass('active', archived).attr('aria-pressed', archived);
        $('#sidebarSearchInput').attr('placeholder', archived ? 'Search archived chats' : 'Search chats');
        renderHistory();
    }
    $('#historyArchivedBtn').on('click', function () { showHistoryTab(currentHistoryTab === 'archived' ? 'active' : 'archived'); });
    $('#historyBackBtn').on('click', function () { showHistoryTab('active'); });

    function renderHistory() {
        const isArchived = currentHistoryTab === 'archived';
        $.ajax({
            url: `/api/conversations?limit=50&archived=${isArchived}`,
            type: 'GET',
            success: function (r) {
                window._allHistoryItems = r.conversations || [];
                const q = ($('#sidebarSearchInput').val() || '').trim();
                renderHistoryList(q ? filterHistory(q) : window._allHistoryItems, isArchived);
            },
            error: function () {
                showToast('Could not load your history right now.', 'error');
            }
        });
    }

    function filterHistory(query) {
        const q = query.toLowerCase();
        return (window._allHistoryItems || []).filter(item =>
            (item.story && item.story.toLowerCase().includes(q)) ||
            (item.tone && item.tone.toLowerCase().includes(q)) ||
            (Array.isArray(item.platforms) && item.platforms.join(' ').toLowerCase().includes(q))
        );
    }

    $('#sidebarSearchInput').on('input', function () {
        const q = $(this).val().trim();
        renderHistoryList(q ? filterHistory(q) : (window._allHistoryItems || []), currentHistoryTab === 'archived');
    });

    // "Create a concise explainer video for Indian investors..." -> "Concise explainer video for Indian investors..."
    function shortTitle(text) {
        const t = String(text || '').trim()
            .replace(/^(please\s+)?(create|write|make|generate|draft|design|give me|prepare)\s+(me\s+)?((a|an|the|some)\s+)?/i, '');
        return t ? t.charAt(0).toUpperCase() + t.slice(1) : (text || 'Untitled chat');
    }

    // Short time for a row ("2h", "3d", "Oct 9") and the group it belongs to
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    function historyDate(item) {
        const d = item.updated_at ? new Date(item.updated_at) : new Date(String(item.timestamp || '').replace(' ', 'T'));
        return isNaN(d) ? null : d;
    }
    function shortWhen(d) {
        if (!d) return '';
        const mins = Math.floor((Date.now() - d) / 60000);
        if (mins < 1) return 'now';
        if (mins < 60) return `${mins}m`;
        if (mins < 60 * 24) return `${Math.floor(mins / 60)}h`;
        if (mins < 60 * 24 * 7) return `${Math.floor(mins / 1440)}d`;
        const now = new Date();
        return `${MONTHS[d.getMonth()]} ${d.getDate()}` + (d.getFullYear() !== now.getFullYear() ? `, ${d.getFullYear()}` : '');
    }
    function groupLabel(d) {
        if (!d) return 'Older';
        const start = new Date(); start.setHours(0, 0, 0, 0);
        const day = 86400000;
        if (d >= start) return 'Today';
        if (d >= start - day) return 'Yesterday';
        if (d >= start - 6 * day) return 'Previous 7 days';
        if (d >= start - 29 * day) return 'Previous 30 days';
        const now = new Date();
        return MONTH_NAMES[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ` ${d.getFullYear()}` : '');
    }

    const PLATFORM_ICONS = {
        linkedin: 'fab fa-linkedin-in', instagram: 'fab fa-instagram', facebook: 'fab fa-facebook-f',
        youtube: 'fab fa-youtube', x: 'fab fa-x-twitter', twitter: 'fab fa-x-twitter',
    };

    function historyRow(item, isArchived) {
        const platforms = Array.isArray(item.platforms) ? item.platforms : [];
        const first = (platforms[0] || '').toLowerCase();
        const icon = PLATFORM_ICONS[first] || 'fas fa-comment';
        const platformNames = platforms.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(', ');
        const d = historyDate(item);
        const title = shortTitle(item.story);
        const menu = isArchived
            ? `<button type="button" role="menuitem" data-act="restore"><i class="fas fa-box-open"></i>Restore</button>`
            : `<button type="button" role="menuitem" data-act="${item.is_pinned ? 'unpin' : 'pin'}"><i class="fas fa-thumbtack"></i>${item.is_pinned ? 'Unpin' : 'Pin to top'}</button>
               <button type="button" role="menuitem" data-act="archive"><i class="fas fa-box-archive"></i>Archive</button>`;
        return `
            <div class="cl-item${Number(item.id) === currentConversationId ? ' active' : ''}" data-id="${item.id}">
                <button type="button" class="cl-row" title="${escapeHtml(item.story || '')}">
                    <span class="cl-plat p-${escapeHtml(first)}" title="${escapeHtml(platformNames)}" aria-hidden="true"><i class="${icon}"></i></span>
                    <span class="cl-title">${escapeHtml(title)}</span>
                    <span class="cl-when">${shortWhen(d)}</span>
                </button>
                <button type="button" class="cl-menu-btn" aria-label="More options for ${escapeHtml(title)}" aria-haspopup="menu" aria-expanded="false"><i class="fas fa-ellipsis"></i></button>
                <div class="cl-menu" role="menu" hidden>${menu}</div>
            </div>`;
    }

    function renderHistoryList(items, isArchived) {
        if (!items.length) {
            const searching = ($('#sidebarSearchInput').val() || '').trim();
            const emptyText = searching ? 'No chats match your search' : (isArchived ? 'No archived chats' : 'No chat history yet');
            const emptySub = searching ? 'Try another word' : (isArchived ? 'Archived chats will appear here' : 'Start a conversation to generate content');
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
        const pinned = isArchived ? [] : items.filter(i => i.is_pinned);
        if (pinned.length) {
            html += `<p class="cl-group"><i class="fas fa-thumbtack" aria-hidden="true"></i>Pinned</p>`;
            pinned.forEach(item => { html += historyRow(item, isArchived); });
        }
        let lastGroup = null;
        items.filter(i => isArchived || !i.is_pinned)
            .sort((a, b) => (historyDate(b) || 0) - (historyDate(a) || 0))
            .forEach(item => {
                const group = groupLabel(historyDate(item));
                if (group !== lastGroup) { html += `<p class="cl-group">${group}</p>`; lastGroup = group; }
                html += historyRow(item, isArchived);
            });
        $('#historyList').html(html);
        updateHeaderConversation();
    }

    // Row clicks, the "..." menu and its actions (one set of handlers for the whole list)
    function closeHistoryMenus() {
        $('.cl-item.menu-open').removeClass('menu-open').find('.cl-menu').prop('hidden', true)
            .end().find('.cl-menu-btn').attr('aria-expanded', 'false');
    }
    $('#historyList').on('click', '.cl-row', function () {
        const $item = $(this).closest('.cl-item');
        $('.cl-item').removeClass('active');
        $item.addClass('active');
        updateHeaderConversation();
        loadConversation($item.data('id'));
    });
    $('#historyList').on('click', '.cl-menu-btn', function (e) {
        e.stopPropagation();
        const $item = $(this).closest('.cl-item');
        const open = !$item.hasClass('menu-open');
        closeHistoryMenus();
        if (!open) return;
        const $menu = $item.find('.cl-menu').removeClass('up').prop('hidden', false);
        $item.addClass('menu-open');
        // Near the bottom of the list it opens upwards, so it is never cut off
        const list = document.getElementById('historyList').getBoundingClientRect();
        if ($menu[0].getBoundingClientRect().bottom > list.bottom) $menu.addClass('up');
        $(this).attr('aria-expanded', 'true');
        $item.find('.cl-menu button').first().trigger('focus');
    });
    $('#historyList').on('click', '.cl-menu button', function (e) {
        e.stopPropagation();
        const id = $(this).closest('.cl-item').data('id');
        const act = $(this).data('act');
        closeHistoryMenus();
        if (act === 'archive') archiveRun(id);
        else if (act === 'restore') unarchiveRun(id);
        else setPinned(id, act === 'pin');
    });
    $(document).on('click', function (e) { if (!$(e.target).closest('.cl-menu').length) closeHistoryMenus(); });
    $(document).on('keydown', function (e) {
        if (e.key !== 'Escape' || !$('.cl-item.menu-open').length) return;
        const $btn = $('.cl-item.menu-open .cl-menu-btn');
        closeHistoryMenus();
        $btn.trigger('focus');
    });

    function setPinned(id, pinned) {
        $.ajax({
            url: `/api/conversations/${id}/${pinned ? 'pin' : 'unpin'}`,
            type: 'POST',
            success: function () { renderHistory(); },
            error: function () { showToast(pinned ? 'Could not pin this chat' : 'Could not unpin this chat', 'error'); }
        });
    }

    function archiveRun(runId) {
        const $item = $(`.cl-item[data-id="${runId}"]`);
        window.withUndo({
            message: 'Conversation archived.',
            url: `/api/conversations/${runId}/archive`,
            hide: () => $item.slideUp(150),
            restore: () => $item.slideDown(150),
            done: () => renderHistory(),
            failed: () => showToast('Failed to archive conversation', 'error'),
        });
    }

    function unarchiveRun(runId) {
        $.ajax({
            url: `/api/conversations/${runId}/unarchive`,
            type: 'POST',
            success: function () {
                showToast('Conversation restored', 'success');
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
            // On page load the unsent draft goes back in the box once the thread is there
            complete: function () { if (opts.restoring) restoreDraft(draftOwner); },
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
                        appendUserMessage(run.story, meta.image_url || null, platforms, run.tone, 'Text (Caption)', 'Standard Enterprise',
                            { followUp: !!meta.refined_from_run_id });
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
                loadScheduledMarks();
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
        // /dashboard?conversation=<id>: a link to one (e.g. the starter posts made at sign-up)
        const linked = new URLSearchParams(window.location.search).get('conversation');
        if (linked && /^\d+$/.test(linked)) {
            history.replaceState(null, '', window.location.pathname);
            loadConversation(linked);
            return;
        }
        let saved = null;
        try { saved = sessionStorage.getItem(CONVERSATION_KEY); } catch (e) { /* storage blocked */ }
        if (saved && !$('#chatThread').children().length && !storyInput.val()) loadConversation(saved, { quiet: true, restoring: true });
        else restoreDraft(draftOwner);
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
            data: JSON.stringify({ requested_amount: amount, reason: reason, currency: $('#creditRequestForm').data('currency') || 'USD' }),
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
            core: '#e85a1c',
            campaign: '#2563eb',
            platform: '#ffa066',
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
        // Undo instead of "Are you sure?" (static/js/undo.js)
        const $row = $(`[onclick="cancelScheduledPostItem(${postId})"]`).closest('tr, .sp-item, .sp-row, li');
        window.withUndo({
            message: 'Scheduled post cancelled.',
            url: `/api/social/scheduled/${postId}/cancel`,
            hide: () => $row.fadeOut(150),
            restore: () => $row.fadeIn(150),
            done: () => loadScheduledPosts(),
            failed: () => showToast('Failed to cancel scheduled post', 'error'),
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





});