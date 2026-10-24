# One-click social connect: research and plan

How to make connecting Facebook, Instagram, LinkedIn and YouTube a single click
in **Settings → Social Accounts**, instead of copying IDs and access tokens.
October 2026.

---

## 1. Why it feels complicated today

| What the user sees now | Why |
|---|---|
| Fields for a Page ID / Author URN and an access token, plus long "Connection Guides" | That is the manual fallback. It is what everyone sees today. |
| No "Connect with Facebook / LinkedIn" button | **The one-click flow is already built** (`services/social_oauth_service.py`) but stays hidden until the app's own Meta and LinkedIn developer apps exist and their keys are in `.env` (`META_APP_ID`/`SECRET`, `LINKEDIN_CLIENT_ID`/`SECRET`). None are set. |
| YouTube has a "Connect" (Google sign-in) | Built (`/api/auth/youtube`), needs Google OAuth credentials. |

So the main fix is not new code: it is **creating and getting approval for the
platform apps**, so the existing buttons switch on. The rest is making the
page look like one button per network.

---

## 2. The options

### A. Our own platform apps (already coded)

The user clicks **Connect**, signs in on Facebook / LinkedIn / Google, approves,
and is back connected. One Facebook sign-in connects the Page **and** its
Instagram account.

| Network | What it takes on our side | How long |
|---|---|---|
| **LinkedIn - personal profile** | Create a LinkedIn app and add "Share on LinkedIn" (`w_member_social`). It is an open permission: no review. | Same day |
| **LinkedIn - company page** | Apply for the **Community Management API**: business email, legal name, address, website, privacy policy; LinkedIn decides. | Weeks; may be refused |
| **Facebook Page + Instagram** | Meta **Business** app, **business verification**, then **App Review** for publishing permissions (written answers + a screen recording of the flow). Before approval it works only for accounts with a role on the app (good for testing). | Typically 1-4 weeks, longer if review asks for changes |
| **Instagram without a Facebook Page** | Add Meta's newer **"Instagram API with Instagram Login"**: Instagram business/creator accounts connect directly. Same App Review. Not built yet. | Same review as above |
| **YouTube** | Google OAuth app; `youtube.upload` is a sensitive scope, so Google **verifies** the app (privacy policy, homepage, demo video). | Weeks |

- **Cost:** free.
- **Pros:** our own name on the consent screens, no per-user fees, data stays with us.
- **Cons:** the approvals take weeks, and LinkedIn company pages may never be approved.

### B. A unified social API (Ayrshare, Late)

These companies already own approved apps for 10+ networks. We call their API;
the user clicks Connect and goes through **their** hosted (white-label) connect
page, then posting goes through their API.

| | Ayrshare (Business) | Late (getlate.dev) |
|---|---|---|
| Connect | Hosted connect page opened with a JWT URL; users never see Ayrshare's dashboard | `GET /v1/connect/{platform}` returns an auth URL; their page or a "headless" one we design |
| Networks | Facebook, Instagram, LinkedIn (incl. pages), X, TikTok, YouTube, Pinterest, Threads, Google Business Profile, Reddit and more | About 10-13, including the big ones |
| Price (2026, check before buying) | Launch about $299/month for up to 10 user profiles; Business about $599/month for 30 profiles, then a per-profile fee | Paid plans from about $19/month (by post volume); higher tiers for more |

- **Pros:**
  - live in days, not weeks;
  - covers networks we haven't built (X, TikTok, Pinterest, Threads, Google Business Profile);
  - they handle the approvals and token refresh.
- **Cons:**
  - a monthly bill that grows with users;
  - our users' posts and tokens pass through a third party;
  - their name may appear on some consent screens;
  - if they go down or change prices, publishing stops.

### C. Hybrid (recommended)

Use our own apps for the big three, where we already have the code, and a
unified API only for networks we haven't built. Start the slow approvals now,
and keep the setup simple for users in the meantime.

---

## 3. Recommended plan

**Decision (Oct 2026): hybrid.**

### Week 1: what users see becomes simple (code, about 2-3 days) - done

Built: one card per network (`static/js/settings.js` `renderSocialCards`),
token entry and guides under "Advanced", a Connect chip in the Schedule
window that rechecks on return, and expiry reminders by email as well as the
bell. Not done: account profile pictures on the cards.

1. **One card per network** in Settings → Social Accounts: logo, one big
   **Connect** button, and once connected the account's name and picture with
   **Disconnect**. No IDs or tokens on screen.
2. **Manual token entry moves behind "Advanced (for developers)"**, collapsed
   by default, with the Connection Guides inside it.
3. **Connect from where it is needed**: the Schedule window and the Publish
   button show "Connect LinkedIn" inline when a platform isn't connected.
4. **Reconnect reminders**: LinkedIn tokens last about 60 days with no refresh
   on self-serve apps, and Meta tokens can expire too. Email and bell 7 days
   before expiry (the notice system already exists).

### Week 1: switch on what needs no review (setup, an hour each)

5. **LinkedIn personal posting**: create the LinkedIn app, add "Share on
   LinkedIn", put the keys in `.env`. The LinkedIn button goes live at once.
6. **Meta in development mode**: create the Meta Business app and put the keys
   in `.env`. The Facebook button works for your own team's accounts, so the
   whole flow can be tested for real.
7. **Google (YouTube)**: create the OAuth client and test with your own channel.

### Weeks 1-4: apply for the approvals (paperwork, in parallel)

8. **Meta**: business verification for StradIT, then App Review for publishing
   to Pages and Instagram. You need a privacy policy URL, a demo account and a
   screen recording of: connect → write a post → publish.
9. **LinkedIn Community Management API**, only if company-page posting matters.
10. **Google OAuth verification** for YouTube upload.

### Week 2: Instagram without a Facebook Page (code, about 2 days) - done

Built: the `instagram` provider in `services/social_oauth_service.py`, tokens
renewed hourly before they expire, publishing and analytics through
`graph.instagram.com`. Setup: `docs/social-connect-setup.md`. Needs
`INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`.

11. Add Meta's "Instagram API with Instagram Login" as a second Instagram
    button, so creators and small businesses without a Facebook Page can connect.

### Later: more networks through a unified API (optional, 2-3 days)

12. If users ask for X, TikTok, Pinterest, Threads or Google Business Profile,
    add one adapter for Late (cheapest to start) behind the same Connect cards,
    rather than building and getting approval for each network ourselves.

---

## 4. Decisions needed

1. **Option**: hybrid (recommended), own apps only (free, slower), or unified
   API only (fastest, ongoing cost).
2. **LinkedIn company pages**: needed at launch, or is personal posting enough
   until LinkedIn approves?
3. **Who owns the Meta and LinkedIn apps**: they should belong to StradIT's
   business accounts (business verification is in the company's name).

---

## Sources

- [Ayrshare pricing](https://www.ayrshare.com/pricing) and [business plan / white-label connect](https://www.ayrshare.com/docs/multiple-users/business-launch-overview); [Ayrshare pricing breakdown (Blotato, 2026)](https://www.blotato.com/blog/ayrshare-pricing)
- [Late: connecting accounts](https://docs.getlate.dev/guides/connecting-accounts), [Late social media API](https://getlate.dev/social-media-api), [Late pricing changes](https://getlate.dev/changelog/simplified-pricing-plans-for-late-api-users)
- [Meta: App Review for the Instagram API](https://developers.facebook.com/docs/instagram-platform/app-review), [Instagram API with Instagram Login](https://developers.facebook.com/blog/post/2024/07/30/instagram-api-with-instagram-login/), [Create an Instagram app](https://developers.facebook.com/docs/instagram-platform/create-an-instagram-app)
- [Instagram publishing scope explained (AdaptlyPost)](https://adaptlypost.com/blog/instagram-business-content-publish-scope)
- [LinkedIn Community Management API](https://developer.linkedin.com/product-catalog/marketing/community-management-api), [LinkedIn: increasing access](https://learn.microsoft.com/da-dk/linkedin/marketing/increasing-access), [Posting to LinkedIn without partner approval (OpenTweet)](https://opentweet.io/answers/can-i-post-to-linkedin-without-partner-approval)
- This codebase: `services/social_oauth_service.py`, `docs/social-connect-setup.md`
