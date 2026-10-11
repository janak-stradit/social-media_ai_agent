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
