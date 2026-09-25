/*
 * Slider-puzzle verification modal (server side: auth/captcha.py).
 *
 *   SliderCaptcha.open().then(token => ...)   // resolves with a one-use captcha_token
 *                        .catch(() => ...)    // user closed the modal
 *
 * Positions are sent in the puzzle's native pixel space (challenge.width), so
 * the stage can be any rendered size.
 */
(function () {
    'use strict';

    const HANDLE_STEP = 0.02;   // keyboard step, fraction of the track
    let els = null;
    let state = null;

    function build() {
        const overlay = document.createElement('div');
        overlay.className = 'captcha-overlay';
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="captcha-modal" role="dialog" aria-modal="true" aria-labelledby="captchaTitle" aria-describedby="captchaSub">
                <div class="captcha-header">
                    <h2 id="captchaTitle">Complete security verification</h2>
                    <button type="button" class="captcha-close" aria-label="Close"><i class="fas fa-xmark" aria-hidden="true"></i></button>
                </div>
                <p class="captcha-sub" id="captchaSub">Drag the slider to complete the puzzle before creating your account</p>
                <div class="captcha-bar">
                    <span>Drag the slider to fit the puzzle piece</span>
                    <button type="button" class="captcha-refresh" aria-label="Load a new puzzle"><i class="fas fa-rotate-right" aria-hidden="true"></i></button>
                </div>
                <div class="captcha-stage">
                    <img class="captcha-bg" alt="" draggable="false">
                    <img class="captcha-piece" alt="" draggable="false">
                    <div class="captcha-loading"><i class="fas fa-spinner fa-spin" aria-hidden="true"></i></div>
                    <div class="captcha-success"><i class="fas fa-check" aria-hidden="true"></i></div>
                </div>
                <div class="captcha-track">
                    <div class="captcha-fill"></div>
                    <div class="captcha-handle" role="slider" tabindex="0"
                         aria-label="Puzzle slider. Use arrow keys to move the piece, Enter to submit."
                         aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
                        <i class="fas fa-arrow-right" aria-hidden="true"></i>
                    </div>
                </div>
                <p class="captcha-msg" aria-live="polite"></p>
            </div>`;
        document.body.appendChild(overlay);

        els = {
            overlay,
            modal: overlay.querySelector('.captcha-modal'),
            close: overlay.querySelector('.captcha-close'),
            refresh: overlay.querySelector('.captcha-refresh'),
            stage: overlay.querySelector('.captcha-stage'),
            bg: overlay.querySelector('.captcha-bg'),
            piece: overlay.querySelector('.captcha-piece'),
            track: overlay.querySelector('.captcha-track'),
            fill: overlay.querySelector('.captcha-fill'),
            handle: overlay.querySelector('.captcha-handle'),
            msg: overlay.querySelector('.captcha-msg'),
        };

        els.close.addEventListener('click', () => finish(null));
        els.refresh.addEventListener('click', () => { if (!state.busy) loadChallenge(); });
        overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) finish(null); });
        overlay.addEventListener('keydown', onOverlayKey);
        els.handle.addEventListener('pointerdown', onPointerDown);
        els.handle.addEventListener('keydown', onHandleKey);
    }

    // --- positioning -------------------------------------------------------

    function maxHandleLeft() {
        return els.track.clientWidth - els.handle.offsetWidth;
    }

    function setFraction(frac) {
        frac = Math.min(1, Math.max(0, frac));
        state.frac = frac;
        const left = frac * maxHandleLeft();
        els.handle.style.transform = `translateX(${left}px)`;
        els.fill.style.width = `${left + els.handle.offsetWidth / 2}px`;
        els.handle.setAttribute('aria-valuenow', String(Math.round(frac * 100)));
        const ch = state.challenge;
        if (ch) {
            els.piece.style.left = `${(pieceX() / ch.width) * 100}%`;
        }
    }

    function pieceX() {
        const ch = state.challenge;
        return state.frac * (ch.width - ch.piece_size);
    }

    function record(yOffset) {
        state.track.push([Math.round(pieceX() * 10) / 10, Math.round(yOffset), Math.round(performance.now())]);
    }

    function resetSlider(animate) {
        els.handle.classList.toggle('is-animating', !!animate);
        els.fill.classList.toggle('is-animating', !!animate);
        els.piece.classList.toggle('is-animating', !!animate);
        setFraction(0);
        state.track = [];
        state.source = 'pointer';
        if (animate) {
            setTimeout(() => {
                els.handle.classList.remove('is-animating');
                els.fill.classList.remove('is-animating');
                els.piece.classList.remove('is-animating');
            }, 300);
        }
    }

    // --- pointer + keyboard -------------------------------------------------

    function onPointerDown(e) {
        if (state.busy || !state.challenge) return;
        e.preventDefault();
        els.handle.setPointerCapture(e.pointerId);
        els.handle.focus();
        const startX = e.clientX;
        const startY = e.clientY;
        const startLeft = state.frac * maxHandleLeft();
        state.track = [];
        state.source = 'pointer';
        els.modal.classList.add('is-dragging');
        setMessage('');
        record(0);

        function move(ev) {
            const max = maxHandleLeft();
            setFraction(max > 0 ? (startLeft + ev.clientX - startX) / max : 0);
            record(ev.clientY - startY);
        }
        function up(ev) {
            els.handle.removeEventListener('pointermove', move);
            els.handle.removeEventListener('pointerup', up);
            els.handle.removeEventListener('pointercancel', up);
            els.modal.classList.remove('is-dragging');
            record(ev.clientY - startY);
            if (state.frac > 0.01) submit();
        }
        els.handle.addEventListener('pointermove', move);
        els.handle.addEventListener('pointerup', up);
        els.handle.addEventListener('pointercancel', up);
    }

    function onHandleKey(e) {
        if (state.busy || !state.challenge) return;
        const step = e.shiftKey ? HANDLE_STEP * 5 : HANDLE_STEP;
        let handled = true;
        if (e.key === 'ArrowRight' || e.key === 'ArrowUp') setFraction(state.frac + step);
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') setFraction(state.frac - step);
        else if (e.key === 'Home') setFraction(0);
        else if (e.key === 'End') setFraction(1);
        else if (e.key === 'Enter' || e.key === ' ') { if (state.frac > 0.01) submit(); }
        else handled = false;
        if (handled) {
            e.preventDefault();
            if (e.key !== 'Enter' && e.key !== ' ') {
                if (state.source !== 'keyboard') { state.track = []; state.source = 'keyboard'; }
                record(0);
            }
        }
    }

    function onOverlayKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); finish(null); return; }
        if (e.key !== 'Tab') return;
        // Keep focus inside the dialog.
        const focusables = [els.close, els.refresh, els.handle];
        const idx = focusables.indexOf(document.activeElement);
        const next = e.shiftKey
            ? focusables[(idx - 1 + focusables.length) % focusables.length]
            : focusables[(idx + 1) % focusables.length];
        e.preventDefault();
        next.focus();
    }

    // --- server calls -------------------------------------------------------

    function setMessage(text, kind) {
        els.msg.textContent = text || '';
        els.msg.className = 'captcha-msg' + (kind ? ' is-' + kind : '');
    }

    function setLoading(on) {
        els.stage.classList.toggle('is-loading', on);
    }

    function loadChallenge() {
        state.challenge = null;
        els.modal.classList.remove('is-success', 'is-error');
        setLoading(true);
        resetSlider(false);
        return fetch('/api/auth/captcha/challenge', { credentials: 'same-origin', cache: 'no-store' })
            .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
            .then(({ ok, body }) => {
                if (!ok || !body.success) throw new Error(body.error || 'Could not load the puzzle.');
                state.challenge = body;
                els.bg.src = body.bg;
                els.piece.src = body.piece;
                els.piece.style.top = `${(body.piece_y / body.height) * 100}%`;
                els.piece.style.width = `${(body.piece_size / body.width) * 100}%`;
                setFraction(0);
            })
            .catch((err) => setMessage(err.message || 'Could not load the puzzle.', 'error'))
            .finally(() => setLoading(false));
    }

    function submit() {
        if (state.busy || !state.challenge) return;
        state.busy = true;
        fetch('/api/auth/captcha/verify', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                challenge_id: state.challenge.challenge_id,
                x: Math.round(pieceX() * 10) / 10,
                track: state.track,
                source: state.source,
            }),
        })
            .then((r) => r.json().catch(() => ({})))
            .then((body) => {
                if (body.success && body.captcha_token) {
                    els.modal.classList.add('is-success');
                    setMessage('Verified!', 'success');
                    setTimeout(() => finish(body.captcha_token), 650);
                    return;
                }
                els.modal.classList.add('is-error');
                setMessage(body.error || 'Not quite - try again.', 'error');
                setTimeout(() => {
                    els.modal.classList.remove('is-error');
                    state.busy = false;
                    if (body.expired) loadChallenge();
                    else resetSlider(true);
                }, 550);
            })
            .catch(() => {
                state.busy = false;
                setMessage('Network error - please try again.', 'error');
                resetSlider(true);
            });
    }

    // --- open / close -------------------------------------------------------

    function finish(token) {
        if (!state || els.overlay.hidden) return;
        const { resolve, reject, returnFocus } = state;
        els.overlay.hidden = true;
        document.body.classList.remove('captcha-open');
        state.busy = false;
        if (returnFocus && returnFocus.focus) returnFocus.focus();
        if (token) resolve(token);
        else reject(new Error('cancelled'));
    }

    function open() {
        if (!els) build();
        if (state && !els.overlay.hidden) return state.promise;
        let resolve, reject;
        const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
        state = {
            promise, resolve, reject,
            returnFocus: document.activeElement,
            challenge: null, frac: 0, track: [], source: 'pointer', busy: false,
        };
        setMessage('');
        els.overlay.hidden = false;
        document.body.classList.add('captcha-open');
        loadChallenge().then(() => els.handle.focus());
        return promise;
    }

    window.SliderCaptcha = { open };
})();
