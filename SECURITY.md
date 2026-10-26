# AVIR AI: security

What protects the platform from outside attackers today, what to set on the
server, and what to build before payments go live. Updated October 2026.

## 1. In place

| Area | Protection | Where |
|---|---|---|
| Passwords | Hashed (Werkzeug); reset tokens hashed and single-use; 8 characters minimum for new and reset passwords | `auth/routes.py` |
| Sign-in | At most 8 attempts per account and 20 per visitor IP in 15 / 10 minutes; a successful sign-in clears the count | `auth/rate_limit.py` |
| Sign-up and email endpoints | At most 10 sign-ups, password resets or verification resends per IP an hour; slider puzzle before sign-up; email verification | `auth/rate_limit.py`, `auth/captcha.py` |
| Sessions | Cookie is HttpOnly, SameSite=Lax, HTTPS-only in production; cleared and reissued at sign-in | `config.py` |
| Secret key | Production refuses to start with the default `SECRET_KEY` (a known key lets anyone forge a login) | `app.py` |
| Cross-site access | No other website can call the API with a user's cookies (CORS closed unless `CORS_ORIGINS` lists trusted sites) | `app.py` |
| Cross-site request forgery | Changes (POST/PUT/PATCH/DELETE) sent by a browser from another website are refused | `app.py` |
| Browser headers | Clickjacking protection, no file-type guessing, referrer policy, HSTS over HTTPS | `app.py` |
| Admin | Admin-only endpoints check the account; the first admin is never created with a known password; startup logs a security error if an admin still uses the old default `admin123` | `auth/utils.py`, `db.py` |
| Two-step sign-in | Codes from an authenticator app (RFC 6238); secret stored encrypted; each code works once; wrong codes limited; turning it off needs a code. Required for admins (`ADMIN_2FA_REQUIRED`), available to everyone in Settings → Security | `auth/two_factor.py`, `auth/routes.py` |
| Audit log | Plan, pack, credit, credit request, access, account and two-step sign-in changes, with who, when, before/after and IP. Admin → Audit log | `db.AuditLog`, `api/routes.py` |
| Signed-out visitors | A visitor without a session is not signed in. (Until October 2026, `/api/auth/me` signed such visitors in as user #1, the first admin.) | `auth/routes.py` |
| Uploads | Image types only (png, jpg, gif, webp), safe file names, 50 MB limit | `api/routes.py`, `config.py` |
| Website scanner | Refuses private and internal addresses (no reaching the server's own network) | `services/website_scraper_service.py` |
| Email links | Unsubscribe and idea links are signed or random, and checked against the user | `services/idea_digest_service.py` |
| Database | Queries through SQLAlchemy (no hand-built SQL from user input); RDS not public | `db.py`, `DEPLOYMENT.md` |
| Ending sessions | Each session carries the account's session version; a password reset, two-step sign-in change, admin deactivation or **Settings → Security → Sign out everywhere else** raises it, and every other browser is signed out on its next request | `auth/utils.py`, `app.py` |
| API access | Every data route needs a signed-in account (the competitor-post routes, which scrape and call the AI, used to be open) | `api/routes.py` |
| API bearer tokens (JWT) | Clients without the browser cookie sign in at `POST /api/auth/token` (same sign-in limits; two-step accounts send `code`) and send `Authorization: Bearer <access_token>` on `/api/*`. HS256, signed with `JWT_SECRET_KEY` (else `SECRET_KEY`); access tokens last 1 hour, refresh tokens 30 days (`POST /api/auth/token/refresh`). Tokens carry the session version, so ending sessions ends them too; an invalid token is refused with 401, and tokens don't open pages | `auth/jwt_tokens.py`, `app.py`, `auth/routes.py` |
| Cost and spam limits per account | At most 150 AI calls, 40 image/video generations and 30 emails sent on the user's behalf per hour | `auth/rate_limit.py` (`limit_by_user`) |
| Server errors | A 500 never shows its raw text (database messages, paths) to the browser; it is logged and the reply carries a reference id | `app.py` |
| Passwords (new) | Common passwords (also with digits added) and passwords containing the user's email name or name are refused at sign-up and reset | `auth/routes.py` (`password_problem`) |
| Uploads (content) | Images are opened and checked, so a script renamed `.png` is refused | `api/routes.py` (`is_real_image`) |
| More headers | `Cross-Origin-Opener-Policy`, `X-Permitted-Cross-Domain-Policies`, and `Cache-Control: no-store` on API answers | `app.py` |

## 2. Set on the server

- **`SECRET_KEY`**: a long random value, for example from
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it
  signs everyone out once.
- **HTTPS**: run certbot as in `DEPLOYMENT.md` step 8; the nginx config only
  listens on port 80 until then, and the secure session cookie needs HTTPS.
- **Default admin**: if `admin@vortexsocial.ai` exists, change its password
  (or delete it once another admin exists). The log line
  `SECURITY: admin ... still uses the old default password` means it is still
  `admin123`.
- **`ADMIN_EMAIL` / `ADMIN_PASSWORD`** (optional): used only when a database
  has no admin yet; without them a random password is printed once in the log.
- **Redis** running (as in `deploy/docker-compose.prod.yml`), so the attempt
  limits are shared by every web worker.

## 3. Payments (Stripe, for every country: INR and USD) - built

All five payment rules are in `services/billing_service.py`, with tests in
`tests/test_billing.py`:

1. **Hosted checkout only**: Stripe's Checkout page takes the payment; card
   details never reach our server.
2. **Signatures checked**: every webhook (`Stripe-Signature`, which must also
   be under 5 minutes old). After Stripe Checkout the server asks Stripe
   directly whether the session was paid. A bad signature changes nothing and
   is logged.
3. **Server prices only**: the order amount comes from the plans table and the
   pack list plus GST; a webhook reporting a different amount is refused and
   logged as a security event.
4. **Applied once**: each order is claimed for fulfilment once
   (`payments.fulfilled_at`), and webhook event ids are stored, so the browser
   and a repeated webhook can't add a plan or pack twice.
5. **Audit log**: every payment (`payment.received`), refused signature and
   amount mismatch.

Refunds go back through Stripe (admin only, Admin -> Payments), issue a
credit note and are recorded in the audit log; refunds made in Stripe's
dashboard sync through the signed webhooks, once. Every payment gets a GST
invoice (`services/invoice_service.py`), visible only to its customer and admins.

Automatic renewal (Stripe subscriptions) follows the same rules: the
subscription's price is set by the server, the first charge is confirmed with
Stripe, every renewal arrives as a signed webhook
and is applied once (with its own invoice), and a renewal for a different
amount is refused.

## 4. Later

- A full Content-Security-Policy that lists allowed script sources. The pages
  use inline scripts today, so a strict policy needs them moved into files
  first.
- Regular RDS snapshots tested by restoring them.

Done since the first version of this list: sign-in lockout emails (the account
owner is told when signing in is paused after repeated wrong passwords),
`pip-audit` in GitHub Actions plus Dependabot (`.github/dependabot.yml`), and the
local debug server only listening on this machine (`DEV_HOST` to change it).

Tests for these protections: `tests/test_security.py`, `tests/test_security_layers.py`.
