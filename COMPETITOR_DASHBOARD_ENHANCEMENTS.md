# Competitor Dashboard — Code Review & Enhancement Roadmap

Reviewed: `templates/competitor_dashboard.html`, `static/js/dashboard.js`, `api/routes.py`
(competitor/opportunity endpoints), `db.py` (`CompetitorPost`, `OpportunitySuggestion`),
`services/scraper_service.py`, `agents/story_agent.py`, `agents/opportunity_agent.py`.

## How it works today

1. **Scan** — `Start Scan` calls `GET /api/platform-posts?platform=&competitor=`, which uses
   `ScraperService.get_platform_posts()` to hit an external "Scraper Explorer" REST API
   (`SCRAPER_API_URL`, default a hardcoded IP) for 4 hardcoded competitors (BlackRock, BNY Mellon,
   Northern Trust, The Vanguard Group). Results are upserted into `competitor_posts`
   (`db.save_competitor_posts`, deduped on competitor+platform+post_url).
2. **Browse** — the feed is actually rendered from the DB (`/api/competitor-posts-db`), not the
   live scan response, so the page also works offline from previously saved posts.
3. **Select → Synthesize** — checking posts fills a context buffer and calls
   `/api/generate-channel-storyline` (`StoryAgent.generate_channel_storyline`), an LLM call that
   turns competitor posts into a 45/55 "counter-narrative" storyline (no CTAs, no markdown).
4. **Opportunities modal** — a separate LLM call (`OpportunityAgent`) mines the same selected posts
   for "unserved content themes" and "domain expansion" ideas, persisted to
   `opportunity_suggestions` and deduped by (category, title).
5. **Generate → Approve → Publish** — caption/media generation goes through the real
   `/api/generate` and `/api/generate-media` endpoints and `/api/approve-asset` persists the
   approved asset. **Publish is not real** — `publishPipelineContent()` is a client-side
   `setTimeout` that never calls the backend, even though a working `SocialPublisherService` and
   `scheduler_thread.py` already exist in the codebase for the main Studio Chat flow.
6. **Pipeline history** — the entire left-hand "History" panel (`window.pipelineHistory`) lives in
   `localStorage` only (`straditPipelineHistory`). Nothing about strategy/asset/approval state is
   persisted server-side or tied to a user.

## Gaps that stood out

- Competitor list is hardcoded in 3 places (`scraper_service.py`, the `<select>` in the template,
  the mock fallback data) — no way to add/remove a tracked competitor without a code change.
- `ScraperService` silently falls back to canned mock posts on any exception (timeout, 404, bad
  JSON) — a real outage looks identical to "no new posts," so nothing signals degraded data.
- Publish button is fake; approved assets never reach `SocialPublisherService`/the scheduler.
- Pipeline/run history is per-browser (`localStorage`), so it's lost on cache clear and invisible
  to teammates or on a different device — no multi-user visibility into what's been approved.
- No engagement data (likes/shares/comments) is scraped or stored, so nothing in the UI can rank
  posts by traction — only recency.
- No scheduled/background scanning; a scan only happens when someone clicks "Start Scan".

## Suggested enhancements

### High value, fits existing architecture
1. **Wire up real publishing** — replace the `setTimeout` in `publishPipelineContent()` with a
   call to a new `/api/publish-pipeline-asset` endpoint that reuses
   `SocialPublisherService.publish_post_to_connected_accounts`, mirroring what
   `scheduler_thread.py` already does for scheduled posts.
2. **Move pipeline history server-side** — add a `competitor_pipeline_run` table (id, user_id,
   competitors, context, strategy_json, asset_json, status, timestamps) so history survives across
   devices/browsers and is auditable per user, replacing `straditPipelineHistory` localStorage.
3. **Configurable competitor list** — a small admin table (`tracked_competitor`: name, scraper
   key, active) plus a settings-page CRUD UI, instead of the hardcoded arrays in
   `scraper_service.py` and the `<select>` options.
4. **Scan health/status indicator** — have `ScraperService` return whether data came from the live
   API or the mock fallback (e.g. a `_source: "live"|"mock"` flag) and surface a banner/badge in
   the dashboard so a scraper outage isn't mistaken for "no new posts."
5. **Background/scheduled scans** — extend `scheduler_thread.py` with a periodic job that calls
   `get_platform_posts` for all tracked competitors/platforms on an interval, so new posts show up
   without a manual click; pair with a "new since your last visit" notification badge.

### Analytics & insight (net-new value)
6. **Competitor activity trends** — a lightweight chart (posts/week per competitor, per platform)
   using the timestamps already stored in `competitor_posts`; helps spot posting-cadence shifts.
7. **Engagement capture** — extend `CompetitorPost` with likes/comments/shares columns (if the
   scraper API exposes them) so posts can be sorted/filtered by traction instead of only recency.
8. **Topic/keyword tagging** — run a lightweight LLM or keyword-extraction pass on ingest to tag
   each post with topics, enabling filtering ("show only ESG posts") and topic-trend charts.
9. **Opportunity lifecycle** — `opportunity_suggestions` currently only grows; add a status
   (new/reviewed/actioned/dismissed) so the modal becomes a working backlog instead of an
   append-only list.
10. **Cross-competitor comparison view** — a side-by-side summary (post volume, platform mix, top
    themes) across all 4 competitors for a chosen date range.

### UX polish
11. **Search & filters in the feed** — free-text search plus a date range filter on top of the
    existing platform/competitor selects (`renderPlatformPosts` already has all the data needed
    client-side, or push filtering server-side as the dataset grows).
12. **Pagination / virtualized list** — `get_competitor_posts` caps at `limit=300` server-side but
    the client renders everything at once; add pagination or infinite scroll as volume grows.
13. **Bulk actions on the feed** — "select all from competitor X", or multi-post batch synthesis
    beyond the current one-at-a-time checkbox selection.
14. **Export** — let a user export a generated storyline/opportunity list as PDF/Markdown for
    sharing outside the tool (sales enablement decks, etc.).
15. **De-dup near-duplicate posts** — competitors often cross-post the same content to
    LinkedIn/Twitter/blog; a similarity check could collapse these in the feed view.

### Reliability / correctness
16. **Retry + timeout tuning for the scraper API** — currently a single `timeout=8` request with no
    retry; add backoff so transient network blips don't immediately fall back to mock data.
17. **Verify SSL properly** — `verify=False` is set on every scraper request (self-signed cert
    workaround); at minimum pin the expected cert/CA instead of disabling verification entirely.
18. **Tests** — no test coverage found for the competitor-posts save/dedupe logic
    (`save_competitor_posts`) or the opportunity dedupe logic — both have non-trivial uniqueness
    rules worth covering.

## Suggested priority order

1. Real publish wiring (#1) — closes the biggest "looks done but isn't" gap.
2. Server-side pipeline history (#2) — unblocks multi-user use of the dashboard at all.
3. Scan health indicator (#4) — cheap, prevents silently misleading data.
4. Configurable competitor list (#3) — removes recurring code changes for a business config.
5. Everything else, roughly in the order listed, depending on which matters more: analytics
   (if this is becoming a strategy tool) vs. UX polish (if usage volume is growing first).
