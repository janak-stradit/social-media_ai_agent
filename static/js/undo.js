// Undo instead of "Are you sure?": the page acts at once, shows "... Undo"
// for a few seconds, and only then sends the request. Leaving the page in
// the meantime still sends it (fetch keepalive), so nothing is lost.
//
//   withUndo({ message, url, method, hide(), restore(), done(response), failed() })
(function () {
    const WAIT_MS = 8000;
    const pending = new Map();  // id -> { opts, timer }
    let nextId = 1;

    function bar() {
        let $bar = $('#undoBar');
        if (!$bar.length) {
            $bar = $('<div id="undoBar" class="undo-bar" role="status" aria-live="polite"></div>').appendTo('body');
        }
        return $bar;
    }

    function send(id) {
        const item = pending.get(id);
        if (!item) return;
        pending.delete(id);
        clearTimeout(item.timer);
        bar().find(`[data-undo-id="${id}"]`).remove();
        const o = item.opts;
        $.ajax({ url: o.url, type: o.method || 'POST' })
            .done(r => { if (o.done) o.done(r); })
            .fail(() => {
                if (o.restore) o.restore();
                if (o.failed) o.failed();
            });
    }

    window.withUndo = function (opts) {
        const id = nextId++;
        if (opts.hide) opts.hide();
        const $note = $(`<div class="undo-note" data-undo-id="${id}">
                <span></span><button type="button" class="undo-btn">Undo</button>
                <div class="undo-timer"></div></div>`);
        $note.find('span').text(opts.message);
        $note.find('.undo-timer').css('animation-duration', `${WAIT_MS}ms`);
        $note.find('.undo-btn').on('click', function () {
            const item = pending.get(id);
            if (!item) return;
            clearTimeout(item.timer);
            pending.delete(id);
            $note.remove();
            if (opts.restore) opts.restore();
        });
        bar().append($note);
        pending.set(id, { opts: opts, timer: setTimeout(() => send(id), WAIT_MS) });
    };

    // Leaving the page: what is waiting is sent now
    window.addEventListener('pagehide', function () {
        pending.forEach(function (item) {
            clearTimeout(item.timer);
            try {
                fetch(item.opts.url, { method: item.opts.method || 'POST', keepalive: true, credentials: 'same-origin' });
            } catch (e) { /* the browser refused; it stays as it was */ }
        });
        pending.clear();
    });
})();
