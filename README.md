# Completed Tasks — Sep 13–14, 2026

Ready-to-paste list for JIRA. Each entry has a suggested type, a title, and a description.

> Note: day boundaries are approximate (reconstructed from session context, not commit
> timestamps — none of this work has been committed to git yet). Group 1 covers the bulk of
> the approval-workflow / brand-configuration work; Group 2 is today's Suggested Storylines fix.

---

## Yesterday — Sep 13, 2026

### [Feature] Character Setup: support multiple characters/logos at once
Replaced the single-select Character dropdown with checkboxes so a user can combine more than
one brand asset (e.g. Aiden + the StradIT logo) as reference images in a single generation.

### [Bug] Remove hardcoded logo watermark stamped onto generated images
`_apply_image_watermark()` was pasting a white-background logo box onto every generated image
regardless of the AI prompt. Removed the post-processing step entirely; branding is now handled
by the generation prompt itself.

### [Bug] "Download All Variations (ZIP)" produced an empty archive
The button was mapping `item.history` (plain URL strings) through `.content`, which doesn't
exist on a string, silently producing an array of `null`s. Fixed the mapping and scoped the
button to image/video items only.

### [Bug] Wizard header stepper (1–4) never updated to the current stage
Two separate `window.slideWorkflow` definitions existed in the same file; the second silently
overwrote the first and dropped the stepper-highlighting logic. Merged into one definition.

### [Bug] Pipeline modal's "Content Generated" stage stuck locked/empty
Stage-reached calculations string-matched `pipeline.status` against stage IDs, but transitional
statuses (e.g. `asset_generating`) never matched anything, so the stepper silently fell back to
stage 0 even when content had been generated. Replaced with a check based on actual pipeline data
(`strategy`, `assetContent`) instead of the status string.

### [Bug] Asset Review carousel only displayed the last generated image
Carousel indicator dots and prev/next arrows used negative CSS offsets to position themselves,
but the parent container clips overflow — all navigation controls were invisible even though
every generated image was present in the DOM. Repositioned controls to stay within bounds.

### [Feature] Replace plain spinner with an image-shaped loading skeleton
Added a blurred, shimmering placeholder box (sized like the eventual image) shown while
image/video generation is in progress, instead of a spinner floating in empty space.

### [Bug] Approval email only attached one image instead of all generated variations
`sendApprovalEmail()` only sent the single item the user clicked Approve on. Updated to collect
every image in the pipeline's generated set and attach all of them (inline + as real attachments).

### [Feature] Build an external content-approval workflow (DB + email + decision UI)
Replaced in-app Approve/Reject with a "Send for Approval" flow: a new `approval_requests` DB
table persists the pipeline's content server-side, an email is sent with a "Review & Decide"
link, and the reviewer accepts/rejects with comments on a dedicated page (login-gated).

### [Feature] Move the approval decision UI from a modal into its own page (`/approve/<id>`)
Reviewers following an email link now land on a focused, standalone page instead of a modal
stacked on top of the full Analysis Dashboard.

### [Feature] Build an Approvals list dashboard (`/approve`)
A page listing every approval request (past and current) with status filters (All/Pending/
Accepted/Rejected), thumbnails, and links into each individual review page.

### [Feature] Add a platform post-preview simulator (LinkedIn/Facebook/Instagram)
A "Preview on [Platform]" button renders a mock-up of how the generated post will actually look
once published, for all three platforms regardless of which one the content was generated for.

### [Bug] Approval-request email linked to the wrong base URL
The link-building code fell back to the incoming request's own host, which produced a broken
`http://localhost/...` link (no port) when triggered from a non-browser context. Added an
explicit `APP_BASE_URL` config value.

### [Bug] Approval-request email used the wrong header color scheme
Reverted the "Review Requested" email's header from an ad-hoc amber scheme back to the
original indigo/purple brand colors used elsewhere.

### [Feature] Add a "Number of Images to Generate" control to the Generator step
Replaced the hardcoded "always generate 3 variations" behavior with a user-selectable count
(1–5).

### [Feature] Restrict counter-strategy image prompts to a single image
Previously every generation branch instructed the AI to produce a multi-slide carousel
description; changed to a single, cohesive image description per the current UI's single-image
workflow.

### [Bug] Generated content silently lost after the last successful image variation
The code path that persists `pipeline.assetContent` back onto the pipeline record only ran when
a *new* variation started generating — after the *last* variation succeeded, that save step was
never reached, so fully-successful generations still looked empty on reload.

### [Feature] Redesign Suggested/Festive Storylines as tabs instead of stacked sections
Consolidated two full-width sections into a single tabbed "Quick Start" bar with a collapse
toggle, so the actual post feed isn't pushed down the page.

### [Feature] Add an urgency badge to the Festive Storylines tab
A count badge (pulsing amber if a festival is within 7 days) ensures upcoming festive content
opportunities can't be missed just because that tab isn't the one open by default.

### [Feature] Build an editable Brand Configuration page (Guidelines + Products & Service)
User-editable settings (stored in a new `app_settings` DB table) read live by content
generation, replacing values that were previously hardcoded in source.

### [Feature] Restructure Content Guidelines into a proper structured form
Replaced a single free-text box with 7 sections (Colors, Typography, Voice & Tone, Content
Rules, Imagery Style, Character/Persona Rules, Messaging), each wired into the actual
generation prompt.

### [Feature] Add Logo & Character asset management (add/replace/remove)
Brand character/logo reference images are now a dynamic, extensible list (new `brand_assets`
DB table) instead of a fixed pair — manageable from Brand Configuration and reflected live in
the dashboard's Character Setup checkboxes.

### [Bug] `/api/brand-assets` returned a 500 error
Root cause was two-fold: (1) the `brand_assets` table didn't exist yet in the shared database
until a schema migration ran, and (2) a separate stale-server-process issue where a running
Flask instance had loaded a partial, in-between version of the route file.

### [Bug] Festive greeting content incorrectly blocked as "No Strong Match"
The festive-content bypass in the strategy-generation step worked correctly, but the downstream
caption-writing prompt treated any non-"Strong/Moderate" connection strength (including the
festive schema's "N/A") as a block condition. Added an explicit exception for festive content.

### [Bug] Generated content displayed the wrong company name
The `brand_voice` preset label ("Standard Enterprise" — a tone descriptor) was being
misinterpreted by the LLM as the actual company name and rendered directly into images/captions.
Clarified the prompt wording and confirmed the company is always StradIT.

### [Bug] Generated images/videos ignored the strategy's actual art direction
The rich `image_prompt`/`video_prompt` fields produced during strategy generation were never
actually sent to image/video generation — only the much shorter social caption text was used as
a substitute prompt. Wired the real prompts through end-to-end.

### [Bug] `UnboundLocalError` breaking video generation
A local `import os` statement deep inside `generate_video()` shadowed the module-level `os` for
the entire function, breaking every earlier `os.getenv()` call in that same function.

### [Bug] Gemini video generation failed on accounts without Enterprise tier
The `generate_audio` parameter is Enterprise-only; a Developer-tier API key rejected the whole
request. Added an automatic retry without that parameter instead of failing outright.

### [Feature] Full-screen layout for Approve and Brand Configuration pages
Replaced narrow, centered single-column pages with the same full app header + full-height
layout used elsewhere in the app.

---

## Today — Sep 14, 2026

### [Bug/Feature] Suggested Storylines kept resurfacing the same content
Three compounding causes: (1) near-duplicate republished wire stories inflated a single
cluster's apparent size/breadth, (2) the LLM's labeling sample often showed 4 near-identical
copies of the same headline instead of a diverse sample, and (3) nothing tracked which
storylines had already been suggested across "regenerate" runs. Added near-duplicate collapsing
before clustering, diversified the labeling sample, and added a persistent (never-cleared)
seen-storyline table that filters out exact repeats. Verified: a second run against the same
post set correctly produced 0 new / 10 filtered as repeats.
