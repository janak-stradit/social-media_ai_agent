$(document).ready(function () {
    let uploadedImagePath = null;

    // ── Character counter ──────────────────────────────────────────────
    $('#storyInput').on('input', function () {
        $('#charCount').text($(this).val().length + ' characters');
    });

    // ── Drag & drop ────────────────────────────────────────────────────
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

    function handleImageUpload(file) {
        const formData = new FormData();
        formData.append('image', file);
        const reader = new FileReader();
        reader.onload = e => { $('#previewImg').attr('src', e.target.result); $('#imagePreview').removeClass('d-none'); };
        reader.readAsDataURL(file);
        setAnalysisBadge('badge-warn', 'Uploading...');
        $.ajax({
            url: '/api/upload', type: 'POST', data: formData, processData: false, contentType: false,
            success: function (r) {
                uploadedImagePath = r.filepath;
                setAnalysisBadge('badge-ok', '<i class="fas fa-check me-1"></i>Analyzed');
                showToast('Image uploaded and analyzed!', 'success');
            },
            error: function () { setAnalysisBadge('badge-fail', 'Upload failed'); showToast('Upload failed', 'error'); }
        });
    }

    function setAnalysisBadge(cls, html) {
        $('#imageAnalysisStatus').attr('class', 'analysis-badge ' + cls).html(html);
    }

    // ── Analyze Story ──────────────────────────────────────────────────
    window.analyzeStory = function () {
        const story = $('#storyInput').val().trim();
        if (!story) { showToast('Please enter a story first!', 'warning'); return; }
        const btn = $('#analyzeBtn');
        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i>Analyzing...');
        $.ajax({
            url: '/api/analyze-story', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ story }),
            success: function (r) {
                const themes = r.analysis?.themes;
                const t = Array.isArray(themes) ? themes.join(', ') : (themes || 'detected');
                showToast('Story analyzed! Themes: ' + t, 'success');
            },
            error: function (xhr) { showToast('Analysis failed: ' + (xhr.responseJSON?.error || 'Error'), 'error'); },
            complete: function () { btn.prop('disabled', false).html('<i class="fas fa-brain me-1"></i>Analyze'); }
        });
    };

    // ── Generate Content ───────────────────────────────────────────────
    window.generateContent = function () {
        const story = $('#storyInput').val().trim();
        if (!story) { showToast('Please enter a story first!', 'warning'); return; }
        const platforms = [];
        $('input[type="checkbox"]:checked').each(function () { platforms.push($(this).val()); });
        if (!platforms.length) { showToast('Select at least one platform!', 'warning'); return; }
        const tone = $('#toneSelect').val();
        const mediaType = $('input[name="mediaType"]:checked').val() || 'none';

        $('#loadingState').removeClass('d-none');
        $('#resultsSection').addClass('d-none');
        $('#generateBtn').prop('disabled', true).addClass('generating');

        let progress = 0;
        const steps = ['Analyzing story themes...','Processing visual context...','Generating captions...','Curating hashtags...','Building strategy...'];
        const iv = setInterval(() => {
            progress = Math.min(90, progress + Math.random() * 15);
            $('#progressBar').css('width', progress + '%');
            $('#loadingText').text(steps[Math.floor(Math.random() * steps.length)]);
        }, 800);

        $.ajax({
            url: '/api/generate', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ story, image_path: uploadedImagePath, platforms, tone, include_strategy: true }),
            success: function (r) {
                clearInterval(iv);
                $('#progressBar').css('width', '100%');
                setTimeout(() => {
                    $('#loadingState').addClass('d-none');
                    $('#resultsSection').removeClass('d-none');
                    renderResults(r.content, platforms);
                    renderHistory();
                    $('#generateBtn').prop('disabled', false).removeClass('generating');
                    document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth', block: 'start' });

                    // Trigger media generation if selected
                    if (mediaType !== 'none') {
                        platforms.forEach(p => {
                            const caption = r.content[p]?.caption?.primary_caption || story;
                            generateMedia(p, caption, mediaType, tone);
                        });
                    }
                }, 400);
            },
            error: function (xhr) {
                clearInterval(iv);
                $('#loadingState').addClass('d-none');
                $('#generateBtn').prop('disabled', false).removeClass('generating');
                showToast('Generation failed: ' + (xhr.responseJSON?.error || 'Unknown error'), 'error');
            }
        });
    };

    // ── Helpers ────────────────────────────────────────────────────────
    function safeStr(val) {
        if (val == null) return '—';
        if (typeof val === 'string') return val;
        if (typeof val === 'number' || typeof val === 'boolean') return String(val);
        if (Array.isArray(val)) {
            // Array of strings/numbers → join them
            return val.map(v => safeStr(v)).join(', ') || '—';
        }
        if (typeof val === 'object') {
            // Try common single-value keys first
            const simple = val.text || val.value || val.time || val.label || val.name
                        || val.description || val.recommendation || val.type || val.format
                        || val.day || val.title;
            if (typeof simple === 'string') return simple;

            // Try combining day + time (common in strategy)
            if (val.day && val.time) return `${val.day} at ${val.time}`;
            if (val.days && val.times) {
                const days = Array.isArray(val.days) ? val.days.join(', ') : val.days;
                const times = Array.isArray(val.times) ? val.times.join(', ') : val.times;
                return `${days} — ${times}`;
            }

            // Collect all string/number leaf values
            const parts = Object.values(val)
                .filter(v => typeof v === 'string' || typeof v === 'number')
                .map(String);
            if (parts.length) return parts.join(' · ');

            // Last resort: flatten one level
            return Object.entries(val)
                .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : safeStr(v)}`)
                .join(' · ') || '—';
        }
        return String(val);
    }
    function safeReach(val) {
        if (val == null) return 70;
        if (typeof val === 'object') val = val.percentage || val.value || val.score || 70;
        return Math.min(100, Math.max(0, parseInt(val) || 70));
    }
    function safeReachLabel(val) {
        if (val == null) return '';
        if (typeof val === 'object') return val.label || val.description || val.text || '';
        return '';
    }
    function escapeHtml(text) {
        if (!text) return '';
        const d = document.createElement('div'); d.textContent = text; return d.innerHTML;
    }

    // ── Render Results ─────────────────────────────────────────────────
    function renderResults(content, platforms) {
        const all = ['facebook', 'instagram', 'linkedin'];

        // Show/hide tabs
        $('#platformTabs .nav-item').each(function () {
            const btn = $(this).find('button');
            const p = btn.attr('data-bs-target')?.replace('#','').replace('Result','');
            $(this).toggle(!!(p && platforms.includes(p)));
        });

        let html = '', first = true;
        all.forEach(platform => {
            const data = content[platform]; if (!data) return;
            const active = first ? 'show active' : ''; first = false;
            const s = data.strategy || {}, ht = data.hashtags || {}, cap = data.caption || {};
            const optTime = safeStr(s.optimal_time);
            const fmt = safeStr(s.format);
            const reach = safeReach(s.metrics_forecast?.reach ?? s.metrics_forecast);
            const reachLbl = safeReachLabel(s.metrics_forecast?.reach ?? s.metrics_forecast);
            const tags = Array.isArray(ht.hashtags) ? ht.hashtags : [];
            const engScore = ht.engagement_prediction ?? '—';
            const primaryCap = cap.primary_caption || '';
            const charCnt = cap.character_count || primaryCap.length;
            const variants = Array.isArray(cap.variants) ? cap.variants : ['',''];

            html += `
            <div class="tab-pane fade ${active}" id="${platform}Result">
              <div class="result-grid">
                <div class="result-card">
                  <div class="result-card-header">
                    <div class="result-card-title">
                      <div class="card-icon card-icon-blue" style="width:28px;height:28px;font-size:12px;"><i class="fab fa-${platform}"></i></div>
                      Caption
                    </div>
                    <div class="d-flex align-items-center gap-2">
                      <span style="font-size:11px;color:var(--text-muted);">${charCnt} chars</span>
                      <button class="btn-icon" onclick="copyToClipboard('caption-${platform}')" title="Copy caption"><i class="fas fa-copy"></i></button>
                    </div>
                  </div>
                  <div class="result-card-body">
                    <div class="caption-box" id="caption-${platform}">${escapeHtml(primaryCap)}</div>
                    <div class="mt-3">
                      <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">
                        <i class="fas fa-flask me-1"></i>A/B Test Variants
                      </div>
                      <div class="variant-nav">
                        <button class="variant-btn active" data-bs-toggle="tab" data-bs-target="#var-${platform}-0">Variant A</button>
                        <button class="variant-btn" data-bs-toggle="tab" data-bs-target="#var-${platform}-1">Variant B</button>
                      </div>
                      <div class="tab-content">
                        <div class="tab-pane fade show active" id="var-${platform}-0">
                          <div class="caption-box" style="border-left-color:var(--purple);">${escapeHtml(variants[0]||'')}</div>
                        </div>
                        <div class="tab-pane fade" id="var-${platform}-1">
                          <div class="caption-box" style="border-left-color:var(--teal);">${escapeHtml(variants[1]||'')}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                <div class="d-flex flex-column gap-3">
                  <div class="result-card">
                    <div class="result-card-header">
                      <div class="result-card-title"><i class="fas fa-hashtag me-2" style="color:var(--purple);"></i>Hashtags</div>
                      <span class="eng-score"><i class="fas fa-chart-line me-1"></i>${engScore}/10</span>
                    </div>
                    <div class="result-card-body">
                      <div class="hashtag-wrap">
                        ${tags.length ? tags.map(t=>`<span class="hashtag-pill">#${t}</span>`).join('') : '<span style="color:var(--text-muted);font-size:12px;">No hashtags generated</span>'}
                      </div>
                    </div>
                  </div>
                  <div class="result-card">
                    <div class="result-card-header">
                      <div class="result-card-title"><i class="fas fa-chess me-2" style="color:var(--blue);"></i>Posting Strategy</div>
                    </div>
                    <div class="result-card-body d-flex flex-column gap-2">
                      <div class="strategy-item">
                        <div class="strategy-label"><i class="far fa-clock me-1"></i>Best Time to Post</div>
                        <div class="strategy-value blue">${optTime}</div>
                      </div>
                      <div class="strategy-item">
                        <div class="strategy-label"><i class="fas fa-film me-1"></i>Content Format</div>
                        <div class="strategy-value">${fmt}</div>
                      </div>
                      <div class="strategy-item">
                        <div class="strategy-label"><i class="fas fa-users me-1"></i>Expected Reach</div>
                        <div class="reach-bar-wrap">
                          <div class="reach-bar"><div class="reach-bar-fill" style="width:${reach}%"></div></div>
                          <span class="reach-pct">${reach}%</span>
                        </div>
                        ${reachLbl ? `<small style="color:var(--text-muted);font-size:11px;">${reachLbl}</small>` : ''}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>`;
        });
        $('#resultsContent').html(html);
    }

    // ── History (PostgreSQL-backed) ────────────────────────────────────
    function renderHistory() {
        const container = $('#historyList');
        container.html('<div class="history-empty"><i class="fas fa-spinner fa-spin fa-lg mb-2"></i><p>Loading...</p></div>');
        $.ajax({
            url: '/api/history?limit=20', type: 'GET',
            success: function (r) {
                const hist = r.history || [];
                $('#historyCount').text(hist.length);
                if (!hist.length) {
                    container.html('<div class="history-empty"><i class="fas fa-clock-rotate-left fa-2x mb-3"></i><p>No runs yet</p><small>Generate content to build history</small></div>');
                    return;
                }
                const icons = { facebook:'fab fa-facebook', instagram:'fab fa-instagram', linkedin:'fab fa-linkedin' };
                let html = '';
                hist.forEach(e => {
                    const platformBadges = e.platforms.map(p => `<i class="${icons[p]||'fas fa-globe'} me-1" style="font-size:12px;"></i>`).join('');
                    html += `
                    <div class="history-entry">
                      <div class="history-meta">
                        ${platformBadges}
                        <span class="badge-version" style="padding:2px 8px;font-size:10px;">${escapeHtml(e.tone)}</span>
                        <span class="history-time">${e.timestamp}</span>
                      </div>
                      <p class="story-preview">${escapeHtml(e.story)}</p>
                      <button class="history-view-btn" onclick="viewHistoryEntry(${e.id})">
                        <i class="fas fa-eye me-1"></i>View Details
                      </button>
                    </div>`;
                });
                container.html(html);
            },
            error: function () {
                container.html('<div class="history-empty"><i class="fas fa-exclamation-circle mb-2"></i><p style="color:#dc2626;">Could not load history</p></div>');
                $('#dbStatus').html('<i class="fas fa-circle fa-xs me-1" style="color:#dc2626;"></i><span style="color:#dc2626;">Offline</span>');
            }
        });
    }

    window.viewHistoryEntry = function (runId) {
        $('#modalTimestamp').text('Loading...');
        $('#modalStory, #modalTone, #modalPlatforms').text('');
        $('#modalDetails').html('<div style="text-align:center;padding:32px;"><i class="fas fa-spinner fa-spin fa-2x" style="color:var(--blue);"></i></div>');
        new bootstrap.Modal(document.getElementById('historyModal')).show();

        $.ajax({
            url: `/api/history/${runId}`, type: 'GET',
            success: function (r) {
                const e = r.run;
                $('#modalTimestamp').text(e.timestamp);
                $('#modalStory').text(e.story);
                $('#modalTone').text(e.tone);
                $('#modalPlatforms').text(e.platforms.join(', '));

                const colors = { facebook:'var(--blue)', instagram:'#e4405f', linkedin:'#0a66c2' };
                let html = '';
                e.platforms.forEach(platform => {
                    const data = e.content[platform]; if (!data) return;
                    const cap = data.caption?.primary_caption || '—';
                    const str = data.strategy || {};
                    const tags = Array.isArray(data.hashtags?.hashtags) ? data.hashtags.hashtags : [];
                    const reach = safeReach(str.metrics_forecast?.reach ?? str.metrics_forecast);
                    html += `
                    <div class="mb-4">
                      <h6 style="color:${colors[platform]||'var(--blue)'};font-weight:700;margin-bottom:12px;text-transform:capitalize;">
                        <i class="fab fa-${platform} me-2"></i>${platform}
                      </h6>
                      <div class="history-detail-block mb-2">
                        <div class="modal-meta-label">Caption</div>
                        <p style="font-size:13px;color:var(--text-main);white-space:pre-wrap;margin:0;">${escapeHtml(cap)}</p>
                      </div>
                      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">
                        <div class="history-detail-block">
                          <div class="modal-meta-label"><i class="far fa-clock me-1"></i>Best Time</div>
                          <div class="modal-meta-value" style="color:var(--blue);">${safeStr(str.optimal_time)}</div>
                        </div>
                        <div class="history-detail-block">
                          <div class="modal-meta-label"><i class="fas fa-film me-1"></i>Format</div>
                          <div class="modal-meta-value">${safeStr(str.format)}</div>
                        </div>
                        <div class="history-detail-block">
                          <div class="modal-meta-label"><i class="fas fa-users me-1"></i>Reach</div>
                          <div style="display:flex;align-items:center;gap:8px;margin-top:4px;">
                            <div class="reach-bar" style="flex:1;"><div class="reach-bar-fill" style="width:${reach}%"></div></div>
                            <span class="reach-pct">${reach}%</span>
                          </div>
                        </div>
                      </div>
                      ${tags.length ? `<div class="hashtag-wrap">${tags.map(t=>`<span class="hashtag-pill">#${t}</span>`).join('')}</div>` : ''}
                    </div>`;
                });
                $('#modalDetails').html(html);
            },
            error: function () {
                $('#modalDetails').html('<p style="color:var(--danger);font-size:13px;"><i class="fas fa-exclamation-circle me-2"></i>Could not load run details.</p>');
            }
        });
    };
    // ── Media Generation ───────────────────────────────────────────────
    function generateMedia(platform, caption, mediaType, tone) {
        // Inject placeholder into that platform's result tab
        const container = $(`#${platform}Result .result-grid`);
        if (!container.length) return;

        const placeholderId = `media-${platform}`;
        container.append(`
            <div id="${placeholderId}" class="result-card" style="grid-column:1/-1;">
              <div class="result-card-header">
                <div class="result-card-title">
                  <i class="fas fa-${mediaType === 'video' ? 'video' : 'image'} me-2" style="color:var(--purple);"></i>
                  ${mediaType === 'video' ? 'Video Storyboard' : 'AI-Generated Image'}
                </div>
                <span style="font-size:12px;color:var(--text-muted);"><i class="fas fa-spinner fa-spin me-1"></i>Generating...</span>
              </div>
              <div class="result-card-body" style="text-align:center;padding:32px;">
                <div class="loading-ring-wrap" style="display:inline-block;width:48px;height:48px;">
                  <div class="loading-ring" style="width:48px;height:48px;border-width:2px;"></div>
                </div>
                <p style="margin-top:12px;color:var(--text-muted);font-size:13px;">
                  ${mediaType === 'video' ? 'Creating detailed video storyboard...' : 'Generating image with DALL-E 3...'}
                </p>
              </div>
            </div>`);

        $.ajax({
            url: '/api/generate-media',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ platform, caption: caption.substring(0, 500), media_type: mediaType, tone }),
            success: function (result) {
                renderMediaResult(placeholderId, result, mediaType, platform);
            },
            error: function (xhr) {
                $(`#${placeholderId} .result-card-body`).html(
                    `<div style="padding:20px;text-align:center;">
                      <i class="fas fa-exclamation-triangle fa-2x mb-2" style="color:var(--warning);"></i>
                      <p style="color:var(--text-sub);font-size:13px;">${xhr.responseJSON?.error || 'Media generation failed'}</p>
                    </div>`
                );
                $(`#${placeholderId} .result-card-header span:last`).html('<span class="badge-fail" style="padding:2px 8px;border-radius:4px;">Failed</span>');
            }
        });
    }

    function renderMediaResult(containerId, result, mediaType, platform) {
        const card = $(`#${containerId}`);
        // Update header status
        card.find('.result-card-header span:last').html(
            result.success
                ? '<span style="color:var(--success);font-size:12px;"><i class="fas fa-check-circle me-1"></i>Done</span>'
                : '<span style="color:var(--danger);font-size:12px;"><i class="fas fa-times-circle me-1"></i>Error</span>'
        );

        if (!result.success) {
            card.find('.result-card-body').html(
                `<p style="color:var(--danger);padding:16px;font-size:13px;"><i class="fas fa-exclamation-triangle me-2"></i>${result.error || 'Unknown error'}</p>`
            );
            return;
        }

        if (mediaType === 'image') {
            card.find('.result-card-body').html(`
                <div style="text-align:center;">
                  <img src="${result.url}" alt="AI Generated for ${platform}" style="max-width:100%;border-radius:var(--radius-sm);border:1px solid var(--border);margin-bottom:12px;">
                  <p style="font-size:12px;color:var(--text-muted);margin-bottom:4px;"><strong>Size:</strong> ${result.size || 'Standard'}</p>
                  <p style="font-size:11px;color:var(--text-muted);font-style:italic;max-width:600px;margin:0 auto;">
                    <strong>Prompt:</strong> ${escapeHtml((result.prompt || '').substring(0, 200))}...
                  </p>
                </div>`);
        } else {
            // Video storyboard
            const sb = result.storyboard || {};
            const scenes = Array.isArray(sb.scenes) ? sb.scenes : [];
            let scenesHtml = scenes.map(s => `
                <div class="strategy-item" style="margin-bottom:8px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <span style="font-size:12px;font-weight:700;color:var(--blue);">Scene ${s.scene_number || '?'}</span>
                    <span style="font-size:11px;color:var(--text-muted);">${s.duration || '?'}s</span>
                  </div>
                  <div style="font-size:12px;margin-bottom:4px;"><strong style="color:var(--text-sub);">Visual:</strong> ${escapeHtml(s.visual_description || '')}</div>
                  ${s.audio_narration ? `<div style="font-size:12px;margin-bottom:4px;"><strong style="color:var(--text-sub);">Narration:</strong> ${escapeHtml(s.audio_narration)}</div>` : ''}
                  ${s.on_screen_text ? `<div style="font-size:12px;margin-bottom:4px;"><strong style="color:var(--text-sub);">On-Screen:</strong> ${escapeHtml(s.on_screen_text)}</div>` : ''}
                  ${s.transition ? `<div style="font-size:11px;color:var(--text-muted);">→ ${escapeHtml(s.transition)}</div>` : ''}
                </div>`).join('');

            card.find('.result-card-body').html(`
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
                  <div class="strategy-item">
                    <div class="strategy-label"><i class="fas fa-heading me-1"></i>Title</div>
                    <div class="strategy-value">${escapeHtml(sb.title || 'Untitled')}</div>
                  </div>
                  <div class="strategy-item">
                    <div class="strategy-label"><i class="fas fa-clock me-1"></i>Duration</div>
                    <div class="strategy-value blue">${sb.duration || '?'} seconds</div>
                  </div>
                  <div class="strategy-item">
                    <div class="strategy-label"><i class="fas fa-music me-1"></i>Music</div>
                    <div class="strategy-value">${escapeHtml(sb.music_mood || 'Not specified')}</div>
                  </div>
                  <div class="strategy-item">
                    <div class="strategy-label"><i class="fas fa-bolt me-1"></i>Hook</div>
                    <div class="strategy-value">${escapeHtml(sb.hook || 'N/A')}</div>
                  </div>
                </div>
                <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">
                  <i class="fas fa-film me-1"></i>Scene-by-Scene Breakdown (${scenes.length} scenes)
                </div>
                ${scenesHtml}
                ${sb.cta ? `<div class="strategy-item" style="border-left:3px solid var(--blue);margin-top:12px;">
                  <div class="strategy-label"><i class="fas fa-bullhorn me-1"></i>Call to Action</div>
                  <div class="strategy-value">${escapeHtml(sb.cta)}</div>
                </div>` : ''}
                ${sb.production_notes ? `<div style="margin-top:12px;padding:12px;background:#fafbfc;border:1px solid var(--border);border-radius:var(--radius-sm);">
                  <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:6px;"><i class="fas fa-lightbulb me-1"></i>PRODUCTION NOTES</div>
                  <p style="font-size:12px;color:var(--text-sub);margin:0;">${escapeHtml(typeof sb.production_notes === 'string' ? sb.production_notes : JSON.stringify(sb.production_notes))}</p>
                </div>` : ''}
            `);
        }
    }

    // ── Clipboard ──────────────────────────────────────────────────────
    window.copyToClipboard = function (id) {
        navigator.clipboard.writeText($('#' + id).text()).then(() => showToast('Copied to clipboard!', 'success'));
    };

    // ── Toast ──────────────────────────────────────────────────────────
    function showToast(msg, type) {
        const icons = { success:'check-circle', error:'exclamation-triangle', warning:'exclamation-circle' };
        const colors = { success:'#16a34a', error:'#dc2626', warning:'#d97706' };
        $('#toastBody').html(`<span style="color:${colors[type]||'inherit'};"><i class="fas fa-${icons[type]||'info-circle'} me-2"></i>${msg}</span>`);
        bootstrap.Toast.getOrCreateInstance(document.getElementById('toast')).show();
    }

    // ── Variant tab switching ─────────────────────────────────────────
    $(document).on('click', '.variant-btn', function () {
        $(this).closest('.mt-3').find('.variant-btn').removeClass('active');
        $(this).addClass('active');
    });

    // ── Init ───────────────────────────────────────────────────────────
    renderHistory();
});