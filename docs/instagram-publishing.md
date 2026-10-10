# Instagram publishing - setup

AVIR AI publishes to Instagram through Meta's Graph API ("Instagram API with
Facebook Login"). Code: `services/instagram_service.py`.

## What each customer needs

1. An **Instagram Business or Creator account** (a personal account can't be
   published to through the API).
2. That account **linked to a Facebook Page** (Meta Business Suite →
   Settings → Instagram accounts).
3. In AVIR AI → Settings → Social accounts: connect the Facebook Page, then
   click **"Use the account linked to my Facebook Page"** - or open
   Instagram → Configure and paste the Page ID and a Page access token.

## One-time setup of the Meta app (StradIT)

1. In <https://developers.facebook.com/apps> use (or create) a **Business**
   type app and add the **Instagram** and **Facebook Login for Business**
   products.
2. Request these permissions:
   - `instagram_basic` - read the account's id and username
   - `instagram_content_publish` - publish posts
   - `pages_show_list` and `pages_read_engagement` - find the Instagram
     account linked to a Page
   - `business_management` - only if the Page is owned by a Business Manager
3. Until the app passes **App Review**, only people with a role on the app
   (admins, developers, testers) can publish. Submit App Review for
   `instagram_content_publish` early - Meta asks for a screencast showing the
   publish flow (Studio Chat → Send for approval → approve → Publish to
   Instagram) and it can take a few weeks.
4. Tokens: use a **long-lived Page access token** (a user token exchanged for a
   long-lived one, then `GET /me/accounts`). Page tokens from a long-lived user
   token don't expire on a schedule, but stop working if the user changes their
   password or removes the app - the app then shows "reconnect Instagram".

## Server settings

- `APP_BASE_URL` must be the **public HTTPS** address (e.g.
  `https://avir.stradit.com`): Meta downloads each image from
  `<APP_BASE_URL>/static/uploads/...`. Localhost can't be published from.
  `PUBLIC_MEDIA_BASE_URL` overrides it if images are served elsewhere.
- `META_GRAPH_VERSION` (default `v23.0`): keep it on a current version - Meta
  retires each one about two years after release.

## What gets published

- **One image** or a **carousel of 2-10 slides** (from an approved
  multi-platform request: Approval Requests → the request → Publish to
  Instagram; only the person who asked for approval sees the button).
- Images are sent as JPEG and cropped into Instagram's allowed shapes
  (4:5 portrait to 1.91:1 landscape) when needed.
- Caption up to 2,200 characters and 30 hashtags (the approved hashtags are
  added under the caption).
- Instagram limits how many posts an account can publish through the API in
  24 hours; **Verify API** in Settings shows how many are used.
