// A post drawn the way each platform shows it (LinkedIn, Instagram, Facebook
// cards), and how long a post may be. Shared by the approval review page
// (approval_review.js) and Studio Chat (app_v2.js: "Preview" and "Edit").
// Needs nothing but the page's Font Awesome icons.
(function () {
    // Caption and hashtags are posted together and counted together.
    // The same numbers are enforced by services/post_edit_service.py.
    const LIMITS = { instagram: 2200, linkedin: 3000, facebook: 3000 };
    const DEFAULT_LIMIT = 3000;
    const MAX_HASHTAGS = 30;
    // Roughly where each feed cuts a caption short and shows "more". It
    // varies with the device, so this is a guide, not an exact line.
    const FOLD = { linkedin: 210, instagram: 125, facebook: 480 };
    const NAMES = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', twitter: 'Twitter / X', blog: 'Blog', youtube: 'YouTube' };

    function esc(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function platformName(p) {
        return NAMES[p] || (p ? p.charAt(0).toUpperCase() + p.slice(1) : 'Social');
    }

    function limitFor(target) {
        if (target === 'carousel') return Math.min(LIMITS.linkedin, LIMITS.instagram);
        return LIMITS[target] || DEFAULT_LIMIT;
    }

    // "#a, b #c" or ["a", "#b"] -> ["#a", "#b", "#c"] (no repeats)
    function cleanHashtags(raw) {
        const parts = Array.isArray(raw) ? raw : String(raw || '').replace(/,/g, ' ').split(/\s+/);
        const seen = new Set(), tags = [];
        parts.forEach(part => {
            const tag = String(part).replace(/\s+/g, '').replace(/^#+/, '');
            if (!tag || seen.has(tag.toLowerCase())) return;
            seen.add(tag.toLowerCase());
            tags.push('#' + tag.slice(0, 59));
        });
        return tags;
    }

    // The text as it is posted: caption, a blank line, the hashtags
    function postText(caption, hashtags) {
        return [String(caption || '').trim(), (hashtags || []).join(' ')].filter(Boolean).join('\n\n');
    }

    function styleText(text, color) {
        return esc(text || '')
            .replace(/#(\w+)/g, `<span style="color:${color};font-weight:600;">#$1</span>`)
            .replace(/\n/g, '<br>');
    }

    // With fold: the part a feed shows first, and a "more" link for the rest
    function bodyHtml(text, platform, fold, color) {
        const cut = fold ? FOLD[platform] : 0;
        text = String(text || '');
        if (!cut || text.length <= cut + 40) return styleText(text, color);
        let end = text.lastIndexOf(' ', cut);
        if (end < cut * 0.6) end = cut;
        return `<span class="pp-short">${styleText(text.slice(0, end).trimEnd(), color)}&hellip; <button type="button" class="pp-more" aria-expanded="false">more</button></span>`
            + `<span class="pp-full" hidden>${styleText(text, color)} <button type="button" class="pp-more" aria-expanded="true">less</button></span>`;
    }

    function initials(brand) {
        return String(brand || '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'YB';
    }

    function handle(brand) {
        return String(brand || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 24) || 'yourbrand';
    }

    function imageHtml(url, square) {
        return `<img src="${esc(url)}" alt="" class="d-block w-100" style="${square ? 'aspect-ratio: 1/1; object-fit: cover;' : 'max-height: 420px; object-fit: cover;'} background:#f3f4f6;">`;
    }

    const SHELL = `background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.12); font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;`;
    const action = (icon, label, color) => `<div style="flex:1;text-align:center;padding:8px 4px;color:${color};font-weight:600;font-size:0.78rem;"><i class="${icon} me-1"></i>${label}</div>`;

    // opts: { text, mediaHtml, brand, fold }. The likes and comments are placeholders.
    function cardHtml(platform, opts) {
        const brand = opts.brand || 'Your brand';
        const media = opts.mediaHtml || '';
        if (platform === 'linkedin') {
            return `
            <div class="pp-card" style="${SHELL}">
                <div style="display:flex; align-items:flex-start; gap:10px; padding:12px 14px 8px;">
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#0a66c2,#004182);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">${esc(initials(brand))}</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#000;">${esc(brand)}</div>
                        <div style="font-size:0.72rem; color:#666;">Just now &middot; <i class="fas fa-earth-americas"></i></div>
                    </div>
                    <i class="fas fa-ellipsis" style="color:#666;"></i>
                </div>
                <div style="padding:0 14px 12px; font-size:0.85rem; color:#000; line-height:1.45;">${bodyHtml(opts.text, platform, opts.fold, '#0a66c2')}</div>
                ${media}
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 14px; font-size:0.72rem; color:#666; border-bottom:1px solid #eee;">
                    <span><i class="fas fa-thumbs-up" style="color:#0a66c2;"></i> 128</span>
                    <span>24 comments &middot; 6 reposts</span>
                </div>
                <div style="display:flex; padding:2px 4px;">
                    ${action('far fa-thumbs-up', 'Like', '#666')}${action('far fa-comment', 'Comment', '#666')}${action('fas fa-retweet', 'Repost', '#666')}${action('far fa-paper-plane', 'Send', '#666')}
                </div>
            </div>`;
        }
        if (platform === 'instagram') {
            return `
            <div class="pp-card" style="${SHELL}">
                <div style="display:flex; align-items:center; gap:10px; padding:10px 12px;">
                    <div style="width:34px;height:34px;border-radius:50%;background:linear-gradient(45deg,#f58529,#dd2a7b,#8134af,#515bd4);padding:2px;flex-shrink:0;">
                        <div style="width:100%;height:100%;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.65rem;">${esc(initials(brand))}</div>
                    </div>
                    <div style="font-weight:600; font-size:0.85rem;">${esc(handle(brand))}</div>
                    <i class="fas fa-ellipsis ms-auto" style="color:#000;"></i>
                </div>
                ${media}
                <div style="padding:10px 12px 4px; display:flex; gap:14px; font-size:1.25rem; color:#000;">
                    <i class="far fa-heart"></i><i class="far fa-comment"></i><i class="far fa-paper-plane"></i>
                    <i class="far fa-bookmark ms-auto"></i>
                </div>
                <div style="padding:4px 12px 2px; font-size:0.78rem; font-weight:600;">248 likes</div>
                <div style="padding:0 12px 12px; font-size:0.82rem; line-height:1.4;"><strong>${esc(handle(brand))}</strong> ${bodyHtml(opts.text, platform, opts.fold, '#0a66c2')}</div>
            </div>`;
        }
        if (platform === 'facebook') {
            return `
            <div class="pp-card" style="${SHELL}">
                <div style="display:flex; align-items:flex-start; gap:10px; padding:12px 14px 8px;">
                    <div style="width:44px;height:44px;flex-shrink:0;border-radius:50%;background:linear-gradient(135deg,#1877f2,#0d5cc9);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;">${esc(initials(brand))}</div>
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; font-size:0.9rem; color:#050505;">${esc(brand)}</div>
                        <div style="font-size:0.72rem; color:#65676b;">Just now &middot; <i class="fas fa-earth-americas"></i></div>
                    </div>
                    <i class="fas fa-ellipsis" style="color:#65676b;"></i>
                </div>
                <div style="padding:0 14px 12px; font-size:0.85rem; color:#050505; line-height:1.45;">${bodyHtml(opts.text, platform, opts.fold, '#0a66c2')}</div>
                ${media}
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 14px; font-size:0.72rem; color:#65676b; border-bottom:1px solid #eee;">
                    <span><i class="fas fa-thumbs-up" style="color:#1877f2;"></i> 96 &middot; <i class="fas fa-heart" style="color:#f33e58;"></i></span>
                    <span>18 comments &middot; 4 shares</span>
                </div>
                <div style="display:flex; padding:2px 4px;">
                    ${action('far fa-thumbs-up', 'Like', '#65676b')}${action('far fa-comment', 'Comment', '#65676b')}${action('fas fa-share', 'Share', '#65676b')}
                </div>
            </div>`;
        }
        return `
            <div class="pp-card" style="${SHELL}">
                <div style="display:flex; align-items:center; gap:10px; padding:12px 14px;">
                    <div style="width:40px;height:40px;border-radius:50%;background:#c2410c;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;">${esc(initials(brand))}</div>
                    <div>
                        <div style="font-weight:600; font-size:0.9rem;">${esc(brand)}</div>
                        <div style="font-size:0.72rem; color:#666;">${platformName(platform)} &middot; Just now</div>
                    </div>
                </div>
                ${media}
                <div style="padding:12px 14px; font-size:0.85rem; line-height:1.45;">${bodyHtml(opts.text, platform, opts.fold, '#0a66c2')}</div>
            </div>`;
    }

    // "more" / "less" on a folded caption
    document.addEventListener('click', function (e) {
        const btn = e.target.closest ? e.target.closest('.pp-more') : null;
        if (!btn) return;
        const card = btn.closest('.pp-card');
        if (!card) return;
        const expand = btn.getAttribute('aria-expanded') === 'false';
        card.querySelectorAll('.pp-short').forEach(el => { el.hidden = expand; });
        card.querySelectorAll('.pp-full').forEach(el => { el.hidden = !expand; });
        const next = card.querySelector(expand ? '.pp-full .pp-more' : '.pp-short .pp-more');
        if (next) next.focus();  // the clicked link is hidden now: keep the keyboard where it was
    });

    window.PostPreview = {
        LIMITS: LIMITS, MAX_HASHTAGS: MAX_HASHTAGS, FOLD: FOLD,
        limitFor: limitFor, postText: postText, cleanHashtags: cleanHashtags, platformName: platformName,
        cardHtml: cardHtml, imageHtml: imageHtml, initials: initials, handle: handle,
    };
})();
