# Feature Development — Last 7 Days (Sep 8–15, 2026)

High-level summary of what shipped this week on `feature/competitor-dashboard`. For a detailed,
per-bug JIRA-ready breakdown of the Sep 13–14 work specifically, see `README.md`.

---

## Competitor Intelligence & Storylines
- **Post clustering & suggested storylines** — competitor posts are now semantically clustered
  (local embeddings) and surfaced as ready-to-use content ideas directly on the Analysis Dashboard.
- **Festive/seasonal storyline suggestions** — a dedicated festival calendar service now suggests
  warm, on-brand holiday/greeting content alongside the regular competitor-driven storylines, with
  its own non-salesy generation path.
- **Anti-repetition tuning** — near-duplicate wire-story posts are now collapsed before clustering,
  labeling samples are diversified, and a persistent "seen" ledger stops the same storyline from
  resurfacing every time storylines are regenerated.

## Brand Character System
- **Character Setup & configuration** — replaced the fixed single-character dropdown with a
  dynamic, multi-select brand asset system (characters + logo) that can be extended from a new
  **Brand Configuration** page, instead of being hardcoded.
- **Reference-image-accurate generation** — character/logo reference photos are now correctly
  threaded through to image and video generation end-to-end, with prompt-writing rules that defer
  a character's actual appearance to their reference photo rather than letting the AI guess.
- **Brand Configuration page** — new full-screen page to manage Content Guidelines (colors,
  typography, voice & tone, imagery style, etc.) and Logo/Character assets (add, replace, delete),
  all editable without touching code.

## Content Generation Quality
- **Content prompt & Studio Chat finetuning** — added tone/finetune controls to the chat-driven
  generation flow.
- **Image prompt accuracy** — fixed image prompts to use the actual rich art-direction text from
  the strategy step (scene, composition, branding rules) instead of a short fallback caption.
- **StradIT guardrails** — added brand/content guardrails so generated content stays on-brand and
  doesn't misattribute company name or persona.
- **Counter-strategy UX fixes** — fixed the copy button, added a cancel button for in-flight
  generations.

## Approval Workflow
- **External content-approval flow** — replaced in-app approve/reject with a proper workflow: a
  request is emailed to a reviewer with a decision link, decisions are recorded with comments, and
  multiple generated images (not just one) are attached and sent.
- **Approvals dashboard (`/approve`)** — a dedicated list of every approval request, past and
  current, with status filtering.
- **Standalone review page (`/approve/<id>`)** — reviewers land on a focused decision page instead
  of a modal, with multi-platform (LinkedIn/Facebook/Instagram) previews of the actual post.

## Platform & Delivery
- **Multi-platform previews** — "Preview on LinkedIn/Facebook/Instagram" simulates how a post will
  actually look on each platform before publishing.
- **ZIP download** — download all generated variations for a pipeline stage in one archive.
- **Security hardening** — replaced non-cryptographic random seed generation with `secrets` for
  security-sensitive code paths.

---

*Both the top-nav entries for Approvals and Brand Configuration were later hidden by request —
both pages remain fully reachable by direct link (e.g. from approval emails).*
