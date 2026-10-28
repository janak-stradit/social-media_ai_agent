# Autopilot: plan

Autopilot creates posts for the calendar's open slots and, once approved,
schedules them to publish. October 2026.

Decisions taken: **off by default**, **Review first** (nothing is published
without approval), **text only by default** (images are opt-in with a monthly
cap, to keep credit use low).

> Merged with a second build (28 Oct 2026): the engine below is kept as is;
> planning and the page come from the other build. The user sets posts a
> week, days off, a tips / offers / news mix, festivals, topics to use and
> avoid, and optional images on the **/autopilot** page, which shows the
> two-week plan ("planned" rows) and what waits for approval. Settings ->
> Autopilot is now a status card linking there. A database made by the
> other build is upgraded at start-up (db._upgrade_first_autopilot).

---

## 1. What it reuses

| Need | Already in the app |
|---|---|
| When to post | Calendar suggested slots from the weekly goal (`services/calendar_service.py`), best times per platform (`services/best_time_service.py`) |
| What to post | Ideas for you (`services/trend_service.py`: trending, post types that work, upcoming dates), the brand profile, the post language |
| Writing the post | The Generate pipeline - now `services/content_pipeline.generate_post` (phase 1) |
| Checking it | Approval requests and reminders (`services/approval_bundle_service.py`, `approval_reminder_service.py`), the bell, email |
| Publishing it | Scheduled posts and the background scheduler (`scheduler_thread.py`, `services/schedule_service.py`) |
| Limits | Plan allowances, credits, compliance rules |

## 2. How it works

1. **Settings -> Autopilot**: a toggle, off by default. Turning it on needs a
   brand profile and a connected account, and asks for confirmation.
   Options: platforms, posts per week (the weekly goal), how many days ahead
   it prepares (default 2).
2. **Every hour**, for each user with Autopilot on, the scheduler:
   - takes the calendar's open slots in the next days, at the platform's
     best time;
   - picks an idea per slot - an occasion first, then trending, then post
     types that work - never repeating a recent topic;
   - writes the post with `generate_post` (brand voice, language,
     compliance), text only;
   - creates an **approval request** for the user and notifies them ("3 posts
     ready for Thu-Sat").
3. **Approved** posts are scheduled at their slot and publish as usual.
   Posts not approved before their slot are skipped, never published.
4. On the calendar, Autopilot posts carry an "Autopilot" badge; the strip
   shows "Autopilot on - next post Thu 10:00".

## 3. Safety

- Nothing is ever published without approval (Review first).
- A post with a high-severity compliance flag says so in the approval request.
- Stays within the plan's posts and credit (`PostBlocked`); stops with a
  notice when they run out.
- Pauses itself after 2 failures in a row or a disconnected account.
- Turning it off removes Autopilot's posts that haven't been approved.
- At most one post per slot; every action is in the audit log.

## 4. Phases

| Phase | What ships | Size |
|---|---|---|
| 1. Pipeline as a function (done) | `services/content_pipeline.generate_post`; `/api/generate` calls it and behaves exactly as before; credit and plan limits raise `PostBlocked` | 2-3 days |
| 2. Autopilot, Review first | Settings toggle (off by default), the hourly planner in the scheduler, approval requests with a notice, approved posts scheduled | 3 days |
| 3. Polish | Calendar badges and the strip line, auto-pause, turning off cleans up, audit log, Help article | 2 days |

## 5. Phase 1 notes

- The pipeline is the same code, moved: `api/routes.py` keeps the agents and
  helpers, and `generate_post` looks them up there at call time, so the
  endpoint and Autopilot always share them (and tests that replace them on
  `api.routes` cover both).
- Found while moving it: the credit check sits in a `try` that logs and
  continues. A reached limit is now raised after it, so it always stops the
  post (tested), while a failing check still doesn't.
- A test leak to watch: a short fake caption makes the quality check ask for
  a rewrite, which calls the real AI unless `caption_agent.refine_caption`
  is faked too (done in `tests/test_content_pipeline.py`).
