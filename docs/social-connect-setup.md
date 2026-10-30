# One-click "Connect with Facebook / LinkedIn": setup

With this set up, a user connects an account in **Settings → Social accounts** by
clicking **Connect with Facebook** or **Connect with LinkedIn**, signing in on
that site and approving. They come back connected, with nothing to copy.

Until it is set up, the buttons are hidden and accounts are connected the old
way (Page ID / Author URN and an access token typed in by hand). That way keeps
working afterwards too, next to the button, as **Enter details by hand**.

Code: `services/social_oauth_service.py`, routes under `/api/social/oauth/` in
`api/routes.py`, tests in `tests/test_social_oauth.py`.

> The flow follows Meta's and LinkedIn's documentation and is tested against
> simulated versions of both. It has not been run against the live services,
> because that needs the two apps below to exist first. Expect to adjust a
> setting or two the first time you try it for real.

## What you need to do

1. Create an app with Meta and one with LinkedIn (below).
2. Register this application's **return addresses** in them, exactly:

   ```
   https://<your-domain>/api/social/oauth/facebook/callback
   https://<your-domain>/api/social/oauth/linkedin/callback
   ```

   `<your-domain>` is `APP_BASE_URL`. An admin can also read the exact
   addresses from `GET /api/social/oauth/providers` while signed in.
   Both platforms expect `https`. For trying it on your own machine,
   `http://localhost` addresses are normally accepted while the app is in
   development mode - check each platform's app settings.
3. Put the apps' IDs and secrets in `.env` and restart:

   ```
   META_APP_ID=...
   META_APP_SECRET=...
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   ```

   A button appears as soon as its two values are set. Setting only one
   platform is fine.

Keep the secrets out of the repository and out of the browser. They are only
ever sent from this server to Meta or LinkedIn.

## Meta (Facebook Pages and Instagram)

One sign-in connects a Facebook Page **and** the Instagram account linked to it.

1. At <https://developers.facebook.com/apps>, create an app of type **Business**.
2. Add the **Facebook Login for Business** product (or Facebook Login).
3. Under its settings, add the Facebook return address above to
   **Valid OAuth Redirect URIs**.
4. Copy the **App ID** and **App secret** (App settings → Basic) into
   `META_APP_ID` and `META_APP_SECRET`.
5. Request these permissions in **App Review**. Meta also asks for business
   verification before granting them:

   | Permission | Used for |
   |---|---|
   | `pages_show_list` | listing the Pages the user manages |
   | `pages_manage_posts` | publishing to the Page |
   | `pages_read_engagement` | reading the Page and its linked Instagram account |
   | `instagram_basic` | finding the Instagram account |
   | `instagram_content_publish` | publishing to Instagram |

Until App Review is passed, only people who have a role on the Meta app
(admins, developers, testers) can connect. That is enough to try it yourself.

Options:

- `META_OAUTH_SCOPES` changes the permissions asked for (default: the five above).
- `META_LOGIN_CONFIG_ID`: with Facebook Login for Business you create a
  *configuration* that holds the permissions. Put its ID here and it is sent
  instead of the permission list.
- `META_GRAPH_VERSION` (currently `v23.0`) is the Graph API version used
  everywhere. Meta's documentation showed `v25.0` in October 2026; Meta keeps a
  version for about two years.

Page access obtained this way normally has no expiry date, so nothing needs
renewing unless the user changes their Facebook password or removes the app.
If Meta does end it, publishing fails with a message asking to connect again.

## LinkedIn

1. At <https://www.linkedin.com/developers/apps>, create an app (LinkedIn asks
   you to link it to a company page).
2. Under **Products**, add:
   - **Sign In with LinkedIn using OpenID Connect** (the scopes `openid profile`),
   - **Share on LinkedIn** (the scope `w_member_social`).
3. Under **Auth**, add the LinkedIn return address above to
   **Authorized redirect URLs**, and copy the **Client ID** and **Client Secret**
   into `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`.

That is enough for a user to connect and post as **their own profile**.

**Company pages** need LinkedIn's **Community Management API** product, which
LinkedIn reviews before granting. Once you have it, set:

```
LINKEDIN_OAUTH_SCOPES=openid profile w_member_social w_organization_social r_organization_admin
```

After that, a user who administers company pages is asked whether to post as
themselves or as one of those pages.

A LinkedIn sign-in lasts **60 days** (LinkedIn does not issue longer ones, and
automatic renewal is only available to selected partners). The app shows the
date in Settings, puts a notification under the bell a week before, and a
second one if it runs out. Connecting again takes one click, and LinkedIn skips
its approval screen when the earlier sign-in is still valid.

`LINKEDIN_API_VERSION` (currently `202609`) is the version sent to LinkedIn's
API. LinkedIn retires a version about a year after its release.

## What the user sees

- **One account found:** connected at once, with a confirmation at the top of
  the Social accounts tab.
- **Several Pages, or a profile and company pages:** a short list to choose from.
  The choice is kept for 15 minutes.
- **They cancel on Facebook or LinkedIn:** a message that nothing was connected.
- **Something went wrong:** the platform's reason, in the same place.

## How it is kept safe

- The sign-in is tied to the browser that started it by a random, single-use
  value (`state`), valid for 10 minutes and bound to the signed-in user. An
  answer that does not match is refused before anything is contacted.
- The one-time code is exchanged for access from the server, using the app
  secret. Access tokens are never sent to the browser, including while the
  user is choosing between accounts.
- The outcome is handed to the Settings page through the session, not through
  the address, so a crafted link cannot make the app display someone else's text.
- The one-time code in the return address is masked in the access log.

## Troubleshooting

| What you see | Likely cause |
|---|---|
| The button is not there | The app ID or secret is missing in `.env`, or the app was not restarted |
| "redirect_uri doesn't match" / "URL blocked" on the platform's page | The return address registered in the app is not exactly the one above (check `https`, the domain and no trailing slash) |
| "No Facebook Page was shared" | The user is not an admin of a Page, or did not tick a Page when Facebook asked |
| "LinkedIn did not share your profile" | The LinkedIn app is missing the OpenID Connect product |
| "Invalid scope" on LinkedIn's page | A scope in `LINKEDIN_OAUTH_SCOPES` has not been granted to the app |
| "could not be verified" | The sign-in took longer than 10 minutes, or was finished in another browser. Click Connect again |

## Instagram without a Facebook Page (Instagram Login)

Creators and small businesses often have an Instagram business or creator
account but no Facebook Page. **Connect with Instagram** signs them in on
Instagram itself ("Instagram API with Instagram Login").

1. In the same Meta app, add the **Instagram** product and open
   **API setup with Instagram login**.
2. Under **Set up Instagram business login**, add this return address:

   ```
   https://<your-domain>/api/social/oauth/instagram/callback
   ```

3. Copy the **Instagram app ID** and **Instagram app secret** shown there.
   They are different from the Meta App ID and secret:

   ```
   INSTAGRAM_APP_ID=...
   INSTAGRAM_APP_SECRET=...
   ```

4. Ask for `instagram_business_basic` and `instagram_business_content_publish`
   in App Review (Advanced Access). Until then it works for Instagram accounts
   added as testers in the app's roles.

How it behaves:

- When it's set up, the Instagram card's main button is **Connect with
  Instagram**. A user whose Facebook Page is already connected can still use
  **Use the one linked to my Page**.
- These tokens start with `IG` and are used with `graph.instagram.com`
  (`instagram_service.graph_url`, `metrics_service._graph`). Page tokens keep
  using `graph.facebook.com`.
- They last 60 days. The hourly scheduler renews each one when 20 days or
  less are left (`refresh_instagram_tokens`), so users don't have to sign in
  again. If renewal fails, the usual reminders (bell and email) still go out
  before it expires.

## Comments as they happen (Meta webhooks)

The Inbox checks for new comments every 30 minutes in a post's first day,
then every 3 hours (`services/comment_service.py refresh_due`). With
webhooks, Meta tells AVIR AI about a new comment within seconds, so the bell
and the Inbox show it straight away - the first hour is when replies help a
post most. The scheduled check keeps running as a safety net.

1. Pick any long random string and set it as `META_WEBHOOK_VERIFY_TOKEN` in
   `.env`. While it is empty the webhook is off (`/api/webhooks/meta` answers 404).
2. Meta app -> **Webhooks**:
   - **Page**: callback URL `https://<your domain>/api/webhooks/meta`, verify
     token as above, subscribe to the **feed** field.
   - **Instagram**: same URL and token, subscribe to **comments**.
   Meta calls the URL once to check the token; the app answers with the
   challenge.
3. Each connected Page must be subscribed to the app
   (`POST /{page-id}/subscribed_apps?subscribed_fields=feed` with the Page
   token). Instagram accounts connected with Instagram Login are subscribed
   from Meta app -> Instagram -> Webhooks.
4. Live data needs the comment permissions approved in App Review
   (`pages_read_engagement`, `pages_manage_metadata`, `instagram_manage_comments`
   or `instagram_business_manage_comments`). Until then only the app's testers'
   accounts send webhooks.

Every POST is checked against `X-Hub-Signature-256`, signed with
`META_APP_SECRET` (Pages, and Instagram through a Page) or
`INSTAGRAM_APP_SECRET` (Instagram Login); anything else is refused. Only new
top-level comments on posts AVIR AI published are stored; the account's own
comments, replies and edits are ignored.
