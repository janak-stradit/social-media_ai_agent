// Admin -> AI providers (templates/admin.html #adminPaneAi; services/ai_router.py).
// Connections (provider accounts, with write-only keys), the models they offer
// and their prices, and what each capability runs on now. Choosing the model
// for each capability and feature comes in phase 2 (AI_PROVIDERS_PLAN.md).
(function () {
    const state = { connections: [], models: [], current: {}, kinds: {}, capabilities: {}, keysReady: false,
        editingConnection: null, editingModel: null, fetchFor: null, fetched: [] };
    const CAPS = ['text', 'vision', 'image', 'video'];
    const CAP_NAMES = { text: 'Text', vision: 'Image analysis', image: 'Images', video: 'Video' };

    function esc(str) {
        return String(str == null ? '' : str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) {
        const icons = { success: 'fa-check-circle text-success', error: 'fa-exclamation-circle text-danger', info: 'fa-info-circle text-info' };
        $('#toastBody').html(`<i class="fas ${icons[type] || icons.info} me-2"></i>${esc(msg)}`);
        const el = document.getElementById('toast');
        if (el && window.bootstrap) new bootstrap.Toast(el, { delay: 3500 }).show();
    }
    function errorOf(xhr, fallback) { return (xhr.responseJSON || {}).error || fallback; }
    function when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return isNaN(d) ? '' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }
    function api(method, url, body) {
        return $.ajax({ url: url, type: method, contentType: 'application/json', data: body ? JSON.stringify(body) : undefined });
    }

    // ── Load and draw ───────────────────────────────────────────────────
    window.loadAiProviders = function () {
        return $.getJSON('/api/admin/ai').done(function (r) {
            Object.assign(state, { connections: r.connections || [], models: r.models || [], current: r.current || {},
                kinds: r.kinds || {}, capabilities: r.capabilities || CAP_NAMES, keysReady: !!r.keys_ready });
            $('#aiKeysNotice').toggleClass('d-none', state.keysReady).html(state.keysReady ? '' :
                '<i class="fas fa-key me-2"></i>To add API keys here, set <code>AI_KEYS_ENCRYPTION_KEY</code> in the server settings and restart. ' +
                'Connections that use keys from the server settings work without it.');
            $('#aiEnvNotice').toggleClass('d-none', !r.routing_from_env).html(r.routing_from_env
                ? '<i class="fas fa-triangle-exclamation me-2"></i><code>AI_ROUTING_FROM_ENV</code> is on: everything runs on the server settings and choices made here are ignored.' : '');
            renderNow();
            renderConnections();
            renderModels();
        }).fail(xhr => toast(errorOf(xhr, 'Could not load the AI providers.'), 'error'));
    };

    function renderNow() {
        $('#aiNow').html(CAPS.map(cap => {
            const n = state.current[cap] || {};
            return `<div class="ai-now-card">
                <div class="ai-now-cap">${esc(CAP_NAMES[cap])}</div>
                <strong>${esc(n.provider || '-')}</strong>
                ${n.model ? `<div class="ai-now-model">${esc(n.model)}</div>` : ''}
                ${n.fallbacks && n.fallbacks.length ? `<small>Then: ${esc(n.fallbacks.join(', '))}</small>` : ''}
                ${n.note ? `<small>${esc(n.note)}</small>` : ''}
                <span class="ai-source">${n.source === 'admin' ? 'Chosen here' : 'From server settings'}</span>
            </div>`;
        }).join(''));
    }

    function keyText(c) {
        if (c.key_source === 'admin') return `<span class="ai-key">&bull;&bull;&bull;&bull;${esc(c.key_last4 || '')}</span> <small class="text-muted">typed here</small>`;
        if (c.key_source === 'env') {
            return c.has_key ? `<small class="text-muted">From server settings: <code>${esc(c.key_env)}</code></small>`
                : `<small class="text-danger">Missing: <code>${esc(c.key_env)}</code> isn't set</small>`;
        }
        return '<small class="text-danger">No key</small>';
    }
    function testText(c) {
        if (!c.last_test_at) return '<small class="text-muted">Not tested</small>';
        return c.last_test_ok
            ? `<span class="ai-test-ok"><i class="fas fa-circle-check me-1"></i>OK</span> <small class="text-muted">${esc(when(c.last_test_at))}</small>`
            : `<span class="ai-test-bad" title="${esc(c.last_test_error || '')}"><i class="fas fa-circle-xmark me-1"></i>${esc(c.last_test_error || 'Failed')}</span>`;
    }
    function renderConnections() {
        const rows = state.connections.map(c => `
            <tr class="${c.enabled ? '' : 'ai-row-off'}">
                <td><strong>${esc(c.name)}</strong>${c.enabled ? '' : ' <small class="text-muted">(off)</small>'}</td>
                <td><small>${esc((state.kinds[c.kind] || c.kind).split(' (')[0])}</small>${c.base_url ? `<div class="small text-muted text-break">${esc(c.base_url)}</div>` : ''}</td>
                <td>${keyText(c)}</td>
                <td>${testText(c)}</td>
                <td class="text-end text-nowrap">
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-test="${c.id}">Test</button>
                    ${c.kind !== 'kie' ? `<button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-fetch="${c.id}">Models</button>` : ''}
                    ${c.kind !== 'bedrock' ? `<button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-edit="${c.id}">Edit</button>` : ''}
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-toggle="${c.id}">${c.enabled ? 'Turn off' : 'Turn on'}</button>
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-remove="${c.id}" title="Remove"><i class="far fa-trash-can"></i></button>
                </td>
            </tr>`);
        $('#aiConnectionsTbody').html(rows.join('') ||
            '<tr><td colspan="5" class="text-center py-4 text-muted">No connections yet. Add one to start.</td></tr>');
    }

    function priceText(m) {
        const bits = [];
        const caps = m.capabilities || [];
        if (caps.includes('text') || caps.includes('vision')) {
            bits.push(m.price_in_per_1k != null && m.price_out_per_1k != null
                ? `$${m.price_in_per_1k} in / $${m.price_out_per_1k} out per 1K tokens` : null);
        }
        if (caps.includes('image')) bits.push(m.price_per_image != null ? `$${m.price_per_image} per image` : null);
        if (caps.includes('video')) bits.push(m.price_per_video_second != null ? `$${m.price_per_video_second} per video second` : null);
        if (bits.some(b => b == null)) {
            const set = bits.filter(Boolean);
            return `<span class="ai-price is-missing"><i class="fas fa-triangle-exclamation me-1"></i>Price missing</span>${set.length ? `<div class="ai-price">${esc(set.join(' · '))}</div>` : ''}`;
        }
        return `<span class="ai-price">${esc(bits.join(' · '))}</span>`;
    }
    function renderModels() {
        const byConn = Object.fromEntries(state.connections.map(c => [c.id, c]));
        const rows = state.models.map(m => `
            <tr class="${m.enabled ? '' : 'ai-row-off'}">
                <td><div class="ai-model-id">${esc(m.model_id)}</div>${m.label ? `<small class="text-muted">${esc(m.label)}</small>` : ''}</td>
                <td><small>${esc((byConn[m.connection_id] || {}).name || '')}</small></td>
                <td>${(m.capabilities || []).map(c => `<span class="ai-cap">${esc(CAP_NAMES[c] || c)}</span>`).join('')}</td>
                <td>${priceText(m)}</td>
                <td><input type="checkbox" class="form-check-input" data-ai-model-on="${m.id}" ${m.enabled ? 'checked' : ''} aria-label="Model on"></td>
                <td class="text-end text-nowrap">
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-model-edit="${m.id}">Edit</button>
                    <button type="button" class="btn-xs btn-xs-outline-neutral" data-ai-model-remove="${m.id}" title="Remove"><i class="far fa-trash-can"></i></button>
                </td>
            </tr>`);
        $('#aiModelsTbody').html(rows.join('') ||
            '<tr><td colspan="6" class="text-center py-4 text-muted">No models yet. Use Models on a connection to list what it offers.</td></tr>');
    }

    // ── Panels ──────────────────────────────────────────────────────────
    function openPanel(id) { $('#aiConnBackdrop').addClass('open'); $('#' + id).addClass('open'); }
    window.closeAiPanels = function () { $('#aiConnBackdrop, #aiConnPanel, #aiModelPanel, #aiFetchPanel').removeClass('open'); };
    function capBoxes(target, chosen) {
        $(target).html(CAPS.map(c => `<label><input type="checkbox" class="form-check-input" value="${c}" ${chosen.includes(c) ? 'checked' : ''}>${esc(CAP_NAMES[c])}</label>`).join(''));
    }
    function checkedCaps(target) { return $(target).find('input:checked').map(function () { return this.value; }).get(); }

    // A connection
    window.openAiConnection = function (id) {
        const c = state.connections.find(x => x.id === id) || null;
        state.editingConnection = c;
        $('#aiConnTitle').html(`<i class="fas fa-plug text-primary me-2"></i>${c ? 'Edit connection' : 'Add connection'}`);
        $('#aiConnKind').html(Object.entries(state.kinds).filter(([k]) => k !== 'bedrock')
            .map(([k, label]) => `<option value="${k}">${esc(label)}</option>`).join('')).val(c ? c.kind : 'openai_compatible');
        $('#aiConnKindGroup').toggleClass('d-none', !!c);
        $('#aiConnName').val(c ? c.name : '');
        $('#aiConnUrl').val(c ? c.base_url || '' : '');
        $('#aiConnKey').val('').attr('placeholder', c && c.has_key ? 'Leave empty to keep the current key' : '');
        $('#aiConnKeyHint').text(c && c.key_source === 'env'
            ? `Uses ${c.key_env} from the server settings. Type a key to use that one instead (stored encrypted).`
            : 'Stored encrypted. It can\'t be shown again - only its last 4 characters.');
        $('#aiConnRemoveKeyRow').toggleClass('d-none', !(c && c.key_source === 'admin'));
        $('#aiConnRemoveKey').prop('checked', false);
        $('#aiConnKey').prop('disabled', !state.keysReady);
        if (!state.keysReady) $('#aiConnKeyHint').text('Set AI_KEYS_ENCRYPTION_KEY in the server settings to type keys here.');
        $('#aiConnError').addClass('d-none');
        syncKindFields();
        openPanel('aiConnPanel');
        setTimeout(() => $('#aiConnName').trigger('focus'), 200);
    };
    function syncKindFields() { $('#aiConnUrlGroup').toggleClass('d-none', $('#aiConnKind').val() !== 'openai_compatible'); }
    window.saveAiConnection = function () {
        const c = state.editingConnection;
        const body = { name: $('#aiConnName').val().trim() };
        if (!c) body.kind = $('#aiConnKind').val();
        if ((c ? c.kind : body.kind) === 'openai_compatible') body.base_url = $('#aiConnUrl').val().trim();
        if ($('#aiConnKey').val()) body.api_key = $('#aiConnKey').val();
        if ($('#aiConnRemoveKey').is(':checked')) body.remove_key = true;
        const $btn = $('#aiConnSave').prop('disabled', true);
        api(c ? 'PUT' : 'POST', c ? `/api/admin/ai/connections/${c.id}` : '/api/admin/ai/connections', body)
            .done(r => {
                $('#aiConnKey').val('');  // never keep a key in the page
                window.closeAiPanels();
                toast(c ? 'Connection saved. Testing it...' : 'Connection added. Testing it...', 'success');
                testConnection(r.connection.id);
            })
            .fail(xhr => $('#aiConnError').removeClass('d-none').text(errorOf(xhr, 'Could not save the connection.')))
            .always(() => $btn.prop('disabled', false));
    };

    function testConnection(id) {
        const $btn = $(`[data-ai-test="${id}"]`).prop('disabled', true).text('Testing...');
        return api('POST', `/api/admin/ai/connections/${id}/test`)
            .done(r => { toast(r.message, r.ok ? 'success' : 'error'); })
            .fail(xhr => toast(errorOf(xhr, 'The test could not run.'), 'error'))
            .always(() => { $btn.prop('disabled', false).text('Test'); window.loadAiProviders(); });
    }

    // Models offered by a connection
    function openFetch(id) {
        const c = state.connections.find(x => x.id === id);
        if (!c) return;
        state.fetchFor = c;
        $('#aiFetchTitle').html(`<i class="fas fa-list text-primary me-2"></i>Models offered by ${esc(c.name)}`);
        $('#aiFetchIntro').text('Asking the provider...');
        $('#aiFetchList').html('<div class="p-3 small text-muted">Loading...</div>');
        $('#aiFetchFilter').val('');
        capBoxes('#aiFetchCaps', ['text']);
        openPanel('aiFetchPanel');
        api('POST', `/api/admin/ai/connections/${id}/test`).done(r => {
            state.fetched = r.models || [];
            const known = new Set(state.models.filter(m => m.connection_id === id).map(m => m.model_id));
            $('#aiFetchIntro').text(r.ok && state.fetched.length
                ? `${state.fetched.length} models. Tick the ones AVIR AI should be able to use.` : r.message);
            $('#aiFetchList').html(state.fetched.map(mid => known.has(mid)
                ? `<label class="is-known"><i class="fas fa-check text-success"></i>${esc(mid)} <small>(already added)</small></label>`
                : `<label><input type="checkbox" class="form-check-input" value="${esc(mid)}">${esc(mid)}</label>`).join('') ||
                '<div class="p-3 small text-muted">No models to show. Add models by hand instead.</div>');
            window.loadAiProviders();
        }).fail(xhr => { $('#aiFetchIntro').text(errorOf(xhr, 'The provider could not be asked.')); $('#aiFetchList').empty(); });
    }
    $(document).on('input', '#aiFetchFilter', function () {
        const q = this.value.trim().toLowerCase();
        $('#aiFetchList label').each(function () { $(this).toggleClass('d-none', !!q && !$(this).text().toLowerCase().includes(q)); });
    });
    window.addFetchedAiModels = function () {
        const ids = $('#aiFetchList input:checked').map(function () { return this.value; }).get();
        const caps = checkedCaps('#aiFetchCaps');
        if (!ids.length) { toast('Tick at least one model.', 'info'); return; }
        if (!caps.length) { toast('Tick what the models can do.', 'info'); return; }
        const $btn = $('#aiFetchAdd').prop('disabled', true);
        api('POST', '/api/admin/ai/models', { connection_id: state.fetchFor.id, models: ids.map(mid => ({ model_id: mid, capabilities: caps })) })
            .done(r => {
                window.closeAiPanels();
                toast(`Added ${r.models.length} model${r.models.length === 1 ? '' : 's'}. Enter their prices in the table.`, 'success');
                window.loadAiProviders();
            })
            .fail(xhr => toast(errorOf(xhr, 'Could not add the models.'), 'error'))
            .always(() => $btn.prop('disabled', false));
    };

    // A model
    function syncPriceFields() {
        const caps = checkedCaps('#aiModelCaps');
        $('.ai-prices [data-price-for]').each(function () {
            const wants = $(this).data('price-for').split(' ');
            $(this).toggleClass('d-none', !wants.some(c => caps.includes(c)));
        });
    }
    window.openAiModel = function (id) {
        const m = state.models.find(x => x.id === id) || null;
        if (!m && !state.connections.length) { toast('Add a connection first.', 'info'); return; }
        state.editingModel = m;
        $('#aiModelTitle').html(`<i class="fas fa-cube text-primary me-2"></i>${m ? 'Edit model' : 'Add a model'}`);
        $('#aiModelConn').html(state.connections.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join(''))
            .val(m ? m.connection_id : state.connections[0].id);
        $('#aiModelConnGroup').toggleClass('d-none', !!m);
        $('#aiModelId').val(m ? m.model_id : '');
        $('#aiModelLabel').val(m ? m.label || '' : '');
        capBoxes('#aiModelCaps', m ? m.capabilities : ['text']);
        $('#aiPriceIn').val(m && m.price_in_per_1k != null ? m.price_in_per_1k : '');
        $('#aiPriceOut').val(m && m.price_out_per_1k != null ? m.price_out_per_1k : '');
        $('#aiPriceImage').val(m && m.price_per_image != null ? m.price_per_image : '');
        $('#aiPriceVideo').val(m && m.price_per_video_second != null ? m.price_per_video_second : '');
        $('#aiModelError').addClass('d-none');
        syncPriceFields();
        openPanel('aiModelPanel');
    };
    window.saveAiModel = function () {
        const m = state.editingModel;
        const body = {
            model_id: $('#aiModelId').val().trim(), label: $('#aiModelLabel').val().trim(), capabilities: checkedCaps('#aiModelCaps'),
            price_in_per_1k: $('#aiPriceIn').val(), price_out_per_1k: $('#aiPriceOut').val(),
            price_per_image: $('#aiPriceImage').val(), price_per_video_second: $('#aiPriceVideo').val(),
        };
        if (!m) body.connection_id = Number($('#aiModelConn').val());
        const $btn = $('#aiModelSave').prop('disabled', true);
        api(m ? 'PUT' : 'POST', m ? `/api/admin/ai/models/${m.id}` : '/api/admin/ai/models', body)
            .done(() => { window.closeAiPanels(); toast('Model saved.', 'success'); window.loadAiProviders(); })
            .fail(xhr => $('#aiModelError').removeClass('d-none').text(errorOf(xhr, 'Could not save the model.')))
            .always(() => $btn.prop('disabled', false));
    };

    // ── Wiring ──────────────────────────────────────────────────────────
    $(function () {
        $(document).on('change', '#aiConnKind', syncKindFields);
        $(document).on('change', '#aiModelCaps input', syncPriceFields);
        $(document).on('click', '[data-ai-url]', function () { $('#aiConnUrl').val($(this).data('ai-url')).trigger('focus'); });
        $(document).on('click', '[data-ai-test]', function () { testConnection(Number($(this).data('ai-test'))); });
        $(document).on('click', '[data-ai-fetch]', function () { openFetch(Number($(this).data('ai-fetch'))); });
        $(document).on('click', '[data-ai-edit]', function () { window.openAiConnection(Number($(this).data('ai-edit'))); });
        $(document).on('click', '[data-ai-toggle]', function () {
            const c = state.connections.find(x => x.id === Number($(this).data('ai-toggle')));
            if (!c) return;
            api('PUT', `/api/admin/ai/connections/${c.id}`, { enabled: !c.enabled })
                .done(() => { toast(c.enabled ? `${c.name} is off.` : `${c.name} is on.`, 'success'); window.loadAiProviders(); })
                .fail(xhr => toast(errorOf(xhr, 'Could not change it.'), 'error'));
        });
        $(document).on('click', '[data-ai-remove]', function () {
            const c = state.connections.find(x => x.id === Number($(this).data('ai-remove')));
            if (!c || !window.confirm(`Remove ${c.name} and its models? This can't be undone.`)) return;
            api('DELETE', `/api/admin/ai/connections/${c.id}`)
                .done(() => { toast(`${c.name} removed.`, 'success'); window.loadAiProviders(); })
                .fail(xhr => toast(errorOf(xhr, 'Could not remove it.'), 'error'));
        });
        $(document).on('click', '[data-ai-model-edit]', function () { window.openAiModel(Number($(this).data('ai-model-edit'))); });
        $(document).on('change', '[data-ai-model-on]', function () {
            const id = Number($(this).data('ai-model-on'));
            api('PUT', `/api/admin/ai/models/${id}`, { enabled: this.checked })
                .done(() => window.loadAiProviders())
                .fail(xhr => { toast(errorOf(xhr, 'Could not change it.'), 'error'); window.loadAiProviders(); });
        });
        $(document).on('click', '[data-ai-model-remove]', function () {
            const m = state.models.find(x => x.id === Number($(this).data('ai-model-remove')));
            if (!m || !window.confirm(`Remove ${m.model_id}?`)) return;
            api('DELETE', `/api/admin/ai/models/${m.id}`)
                .done(() => { toast('Model removed.', 'success'); window.loadAiProviders(); })
                .fail(xhr => toast(errorOf(xhr, 'Could not remove it.'), 'error'));
        });
        $(document).on('keydown', function (e) { if (e.key === 'Escape') window.closeAiPanels(); });
    });
})();
