# New Features — Oct 9–11, 2026

Everything built in the last three days, grouped by day. Each entry has a type, a title and a
short description, in the same style as `README.md`, so it can be pasted into JIRA.

> Days follow the commit dates on branch `feature/news-aggregator`:
> `24f1c1d` (Oct 9), `c8b6dc8` and `9a5b859` (Oct 10), `a978841` (Oct 11).
> Test suite at the end of Oct 11: 415 passed, 3 failed. The 3 failures are in
> `tests/test_app_factory.py` and were already failing before this work.

**Read this first:** [what is switched off by default](#switched-off-by-default) and
[what has not been proven against the live platforms](#not-yet-proven-live).

---

## Oct 9, 2026 — ideas, calendar and emails

### [Feature] "Ideas for you" in Studio Chat
The start screen suggests posts from three sources: what is trending in the user's industry this
week, post types that work, and upcoming festivals in the user's markets. Clicking an idea fills
the message box and picks its platform and output. Trends are looked up once per industry and
kept for 24 hours.
Files: `services/trend_service.py`, `static/js/app_v2.js`. Tests: `tests/test_idea_feed.py`.

### [Feature] Content Calendar with a weekly posting goal
New page `/calendar`: this week's goal (1 to 7 posts) with a progress ring and a streak of weeks
the goal was met, and a month view showing posts created, posts scheduled, festivals and
suggested days. A suggested day carries an idea that opens Studio Chat ready to go. Days follow
the user's own time zone.
Files: `services/calendar_service.py`, `templates/calendar.html`, `static/js/calendar.js`.
Tests: `tests/test_content_calendar.py`.

### [Feature] Weekly ideas email
Once a week each user gets three ideas from their feed. Each button opens Studio Chat with that
brief loaded, also after logging in. One-click unsubscribe, a switch in Settings → Notifications,
and "send me one now".
Files: `services/idea_digest_service.py`. Tests: `tests/test_weekly_ideas_email.py`.

### [Feature] Notifications under the bell
Timely nudges: a festival a few days away, this week's top story in the user's industry, and a
reminder after a quiet spell. They always appear under the header bell and can also be emailed.
A user gets at most two idea emails a week, the weekly one included.
Files: `services/nudge_service.py`, `static/js/user_menu.js`. Tests: `tests/test_nudges.py`.

### [Feature] Monthly recap email
Early each month: how many posts the user created, per platform, compared with the month
before, plus two ideas and up to two tips read off the numbers. Sent only to users who posted.
Files: `services/recap_service.py`. Tests: `tests/test_monthly_recap.py`.

### [Feature] Emails when a credit request is decided
The user is told when an admin approves or rejects their credit extension, with the new limit.
A mail failure never undoes the decision.
Tests: `tests/test_credit_request_email.py`.

### [Feature] Video generation, switched on per user
Video is shown as "Soon" until an admin switches it on for a user (Admin page). Videos are
charged per second, have a daily limit, keep the model's own audio, and an optional voiceover is
cut to fit the clip.
Files: `services/media_service.py`, `templates/admin.html`. Tests: `tests/test_video_generation.py`.

---

## Oct 10, 2026 — news, admin emails, carousels, Instagram

### [Feature] Own news aggregator for trending topics
Replaces the Gemini search call (which hit rate limits) as the main source of "trending" ideas.
Reads Google News and trade feeds, removes duplicates, groups stories about the same event, ranks
them by how many different publishers carry them, and writes topics for the user's industry and
market. Cached per industry and market for 24 hours.
Files: `services/news_aggregator.py`, `services/trend_service.py`. Tests: `tests/test_news_aggregator.py`.

### [Feature] Admin → Emails: history of every email sent
A new Emails tab lists every send attempt with its type, recipient, status and error, with
filters and totals.
Files: `services/email_service.py` (`_deliver`, `email_context`), `db.py` (`EmailLog`),
`static/js/admin.js`. Tests: `tests/test_email_center.py`.

### [Feature] Admin → Emails: on/off switches for scheduled emails
Weekly ideas, festival and trend nudges, and the monthly recap can be switched on and off from
the Admin page. The `.env` values are the defaults.

### [Feature] Admin → Emails: send ideas or a festival post to a segment
An admin picks users by industry and account type, previews how many will receive it, and sends
either fresh ideas or a festival idea now. Respects opt-outs and the weekly cap.
Files: `services/email_campaign_service.py`.

### [Improvement] Ideas and emails fit the user better
Idea prompts now take the account type into account, and UAE users get their own occasions
(Flag Day, Commemoration Day, National Day, Islamic festivals).
Files: `services/festival_service.py`, `services/trend_service.py`.

### [Feature] Send a whole post for approval from Studio Chat
One request and one email cover every platform of a post, each with its own caption, hashtags
and media. The reviewer approves each platform or asks for changes with a comment, so a request
can end as Approved, Partly approved or Changes requested. The requester gets a decision email.
Files: `services/approval_bundle_service.py`, `static/js/approval_send.js`,
`static/js/approval_review.js`. Tests: `tests/test_approval_bundle.py`.

### [Feature] `/carousel` image command
Type `/carousel` and a topic to get a set of slides with a shared caption. LinkedIn carousels can
be sent as a multi-image post or as a document (a PDF built from the slides).
Files: `services/carousel_service.py`, `services/image_presets.py`.

### [Feature] Instagram publishing
An approved Instagram post (one image or a carousel of 2 to 10 slides) can be published from the
request. Instagram can be connected in one click from an already connected Facebook Page.
Files: `services/instagram_service.py`, `docs/instagram-publishing.md`.
Tests: `tests/test_instagram_publishing.py`.

---

## Oct 11, 2026 — easier to use, scheduling, publishing, one-click connect

### [Feature] In-app guide: checklist, tours, tips and a Help Center
- **Get started checklist** in the header: seven steps that tick themselves off from what the
  user has really done.
- **Tours** on first visit to Studio Chat, Brand Profile, Approval Requests, a single request and
  Settings. "Take the tour again" is in the user menu.
- **"?" tips** next to things that need a word of explanation.
- **Help Center** at `/help`: ten short guides with search, written as Markdown in `help/`.
- Empty screens now say what to do next.

Files: `services/guide_service.py`, `services/help_center.py`, `static/js/guide.js`,
`templates/help.html`, `help/*.md`. Tests: `tests/test_guide.py`.

### [Feature] Edit a caption by hand
**Edit** on a caption or its hashtags opens it for typing, with a live count against the
platform's limit (Instagram 2,200; LinkedIn and Facebook 3,000; 30 hashtags). Nothing is
regenerated and it costs nothing. **Restore original** brings back the AI's text. Also works on
a carousel's caption.
Files: `services/post_edit_service.py`, `static/js/app_v2.js`. Tests: `tests/test_post_edit.py`.

### [Feature] Preview post
Shows the post the way LinkedIn, Instagram or Facebook displays it, including where a long
caption is cut behind "more" and the slides of a carousel. The same cards are used on the
approval review page.
Files: `static/js/post_preview.js`.

### [Improvement] Smaller conveniences in Studio Chat
- Paste a copied image or screenshot with `Ctrl` + `V` to attach it.
- **Try again** on a failed change, image command or research.
- An unsent brief is kept as a draft and survives a refresh.
- **Save brief** and **Use again** under each sent message; saved briefs are behind the star
  next to the paperclip.
- Clearer button names: **Enlarge** (the picture), **Preview post**, **Rewrite caption**,
  **New images**.

### [Feature] Approval reminders
A reviewer who has not decided gets a reminder after 24 hours and a last one after 3 days.
Requests older than 14 days are left alone.
Files: `services/approval_reminder_service.py`. Tests: `tests/test_approval_reminders.py`.

### [Feature] Schedule a post from Studio Chat
**Schedule** opens a dialog with a date and time (in the user's time zone) and a section per
platform, each with its own caption and media and the account it posts as. Times are stored in
UTC. A post is marked "publishing" before anything is sent, so it can never go out twice.
Files: `services/schedule_service.py`, `static/js/approval_send.js`.
Tests: `tests/test_publishing.py`.

### [Feature] Calendar: move, cancel and retry scheduled posts
Click a scheduled post to see each platform's result, pick a new time, cancel it or send again
the platforms that failed. Drag a post to another day to move it. A notification under the bell
says when a post went out or why it did not.
Files: `static/js/calendar.js`, `services/schedule_service.py`.

### [Feature] LinkedIn and Facebook publishing, with carousels
Text, one image and carousels can be published from an approved request or on schedule.
Facebook: several photos in one post. LinkedIn: a multi-image post, or a document (PDF) carousel.
Files: `services/publish_service.py`, `services/linkedin_service.py`, `services/facebook_service.py`.

### [Feature] Image Library
New page `/library`: every image the user has made, newest first, with search. An image can be
enlarged, downloaded or used in a new post. Ticking several makes a carousel to schedule or send
for approval. The image picker in the send dialog can also pick from "All my images".
Files: `templates/library.html`, `static/js/library.js`. Tests: `tests/test_library_briefs.py`.

### [Feature] One-click "Connect with Facebook / LinkedIn"
In Settings, the user clicks Connect, signs in on the platform and comes back connected, with no
IDs or tokens to copy. Facebook connects the Page and its linked Instagram account together.
With several Pages (or a profile and company pages) the user picks one. LinkedIn sign-ins last 60
days: the card shows the date and the user is notified a week before. Entering details by hand
remains available.
Files: `services/social_oauth_service.py`, `docs/social-connect-setup.md`.
Tests: `tests/test_social_oauth.py`.

### [Bug] YouTube connect could attach a channel to another user's account
The return address carried a user ID that the app trusted without checking who was signed in.
It now uses a random, single-use value tied to the browser and the signed-in user. An error
value from the address was also written straight into the page; nothing from the address is
shown any more.
Files: `api/routes.py`.

### [Bug] Dialog overlay did not cover the whole window
With the app shown at 90% zoom, the dark background behind dialogs stopped short on the right
and bottom. Fixed for every dialog.

### [Bug] Post card wider than a phone screen
A post with an image scrolled sideways on a phone because the image's header did not wrap.

---

## Switched off by default

These are built but do nothing until switched on.

| What | How to switch it on |
|---|---|
| **Schedule** button in Studio Chat and the Image Library (shown as "Soon") | `STUDIO_SCHEDULING=true` in `.env` |
| One-click Connect buttons | `META_APP_ID` + `META_APP_SECRET`, `LINKEDIN_CLIENT_ID` + `LINKEDIN_CLIENT_SECRET` (see `docs/social-connect-setup.md`) |
| Approval reminder emails | Admin → Emails, or `APPROVAL_REMINDER_EMAILS=true` |
| Weekly ideas email | Admin → Emails, or `WEEKLY_IDEAS_EMAIL=true` |
| Nudge emails (the bell works without this) | Admin → Emails, or `NUDGE_EMAILS=true` |
| Monthly recap email | Admin → Emails, or `MONTHLY_RECAP_EMAIL=true` |
| Video generation | Per user, on the Admin page |

All emails need SMTP and `APP_BASE_URL` to be set. Scheduled posts and scheduled emails only go
out while the scheduler is running (`scripts/run_scheduler.py` in production).

## Not yet proven live

- **Publishing to Instagram, Facebook and LinkedIn** and **one-click connect** are tested against
  simulated versions of the platforms, not the real ones. The request formats were checked
  against LinkedIn's and Meta's documentation in October 2026. Live use needs:
  - Meta App Review for the page and Instagram publishing permissions,
  - LinkedIn approval for company pages (personal profiles need none),
  - a public `https` address in `APP_BASE_URL`.
- **API versions** are settings and need bumping over time: `META_GRAPH_VERSION` (now `v23.0`;
  Meta's documentation shows `v25.0`) and `LINKEDIN_API_VERSION` (now `202609`).
- **Scheduled times are now stored in UTC.** A manual post that was already waiting before
  Oct 11 was saved the old way and may go out at a shifted time. Check Settings → Scheduled posts.

## New pages and where to find things

| Page | Address | Added |
|---|---|---|
| Content Calendar | `/calendar` | Oct 9 |
| Help Center | `/help` | Oct 11 |
| Image Library | `/library` | Oct 11 |

| Guide | File |
|---|---|
| Instagram publishing setup | `docs/instagram-publishing.md` |
| One-click connect setup | `docs/social-connect-setup.md` |
| User-facing help articles | `help/*.md` (shown at `/help`) |
