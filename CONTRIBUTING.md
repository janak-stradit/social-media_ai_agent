# Contributing

## Local setup

```bash
pip install -r requirements-dev.txt   # Python: black, flake8, pylint, mypy, bandit, pytest
npm install                           # Frontend: eslint, htmlhint
```

## Before opening a PR

```bash
black .                  # auto-format Python
flake8 .                 # style lint
pylint agents/ auth/ services/ api/ app.py config.py db.py scheduler_thread.py scripts/
mypy .                   # type check (advisory only right now, see below)
bandit -c pyproject.toml -r .   # security scan
pytest --cov --cov-report=term-missing   # tests + coverage report

npm run lint:js          # ESLint on static/js
npm run lint:html        # HTMLHint on templates/*.html
```

All of the above run in CI (`.github/workflows/code-quality.yml`) on every push and pull request
against `main`, as four independent jobs so each shows up as its own check on a PR:
- **Python Lint & Security** — Black, Flake8, Pylint, Bandit
- **Frontend Lint** — ESLint, HTMLHint
- **Static type check (mypy, report-only)** — Mypy, non-blocking (see below)
- **Test** — pytest + coverage

A PR won't be mergeable until the first, second, and fourth pass, once branch protection is
turned on (see below) — the mypy job is intentionally left out of the required-checks list since
it's report-only.

## Tooling choices

**Python** — five separate tools rather than one combined linter/formatter, each covering a
different concern:
- **Black** formats. Config: `[tool.black]` in `pyproject.toml`, line length 120.
- **Flake8** does style/correctness linting (pyflakes + pycodestyle). Config: `.flake8`.
  `E501` (line-too-long) is off deliberately — Black governs practical width, and this codebase
  has long prompt/URL/SQL strings not worth force-wrapping. `E203`/`W503` are off because they
  conflict with Black's own formatting choices (the standard Black-compat ignores).
- **Pylint** does deeper static analysis (unreachable code, wrong method names, etc.). Config:
  `[tool.pylint.*]` in `pyproject.toml`. A first-adoption baseline: convention/complexity checks
  that are subjective or reflect a deliberate, pervasive pattern in this codebase (broad
  `except Exception`, lazy `import` inside functions, `raise Exception(...)` instead of a custom
  hierarchy, `too-many-*` complexity metrics) are disabled repo-wide with reasons in the disable
  list's comments. Everything else pylint found was either fixed or flagged inline with
  `# pylint: disable=<code>` plus a `KNOWN BUG` comment at the exact call site — see below.
- **Mypy** type-checks, in its own **`Static type check (mypy, report-only)`** CI job. Config:
  `[tool.mypy]` in `pyproject.toml`. **Currently advisory only** (`continue-on-error: true` and
  left out of the required-status-checks list) — this codebase's SQLAlchemy models use plain
  `Column(...)` declarations with no `Mapped[...]` typing, which mypy can't reconcile with how
  instance attributes actually behave at runtime, so `db.py` alone accounts for the large majority
  of the ~52 outstanding errors. Fixing that properly means migrating the ORM models to
  `Mapped[...]`/`mapped_column(...)` — a real, separate migration, not a lint fix. Promote this
  job to a required check once that migration happens (or once you've reviewed the remaining
  errors and are comfortable with them).
- **Bandit** scans for security issues. Config: `[tool.bandit]` in `pyproject.toml`. `B110`
  (try/except/pass) is skipped repo-wide as a deliberate best-effort-fallback pattern used
  throughout; a handful of other findings are suppressed individually inline with `# nosec <code>`
  — search the codebase for `nosec` to see each one and why. One, `verify=False` in
  `services/scraper_service.py`, is a real known gap (see `COMPETITOR_DASHBOARD_ENHANCEMENTS.md`),
  not a false positive — it's suppressed so CI stays green, not because it's fine.

  **Bandit nosec syntax gotcha:** `# nosec B105` works; `# nosec B105,B106` (comma, no space)
  silently keeps only the *last* code due to a regex bug in bandit's comment parser — use a space
  (`# nosec B105 B106`) or put codes on their own line if you need more than one. Also never put
  the literal text `pylint:` in a prose comment near pylint-disabled code — pylint's directive
  scanner does a naive substring search for it anywhere in a comment, not just at the start.

**Frontend** — this app has no build step (plain HTML + jQuery + Bootstrap from CDN), so tooling
stays equally build-free:
- **ESLint** (flat config, `eslint.config.js`) lints `static/js/**/*.js`. Since these scripts
  aren't ES modules, each page-script attaches its public API to `window.*` and other scripts /
  inline `onclick=` handlers call it as a bare global — `eslint.config.js` declares that whole
  surface as known globals so ESLint can resolve it. If you add a new `window.foo = ...` in any
  `static/js` file, add `foo` to that globals list too.
- **HTMLHint** (`.htmlhintrc`) lints `templates/*.html`. It tolerates the Jinja2 template syntax
  fine; a few rules (`doctype-first`, `attr-value-double-quotes`, `spec-char-escape`,
  `alt-require`, `title-require`) are off as too strict for a first pass.

**Tests** — **pytest** + **pytest-cov**. Config: `[tool.pytest.ini_options]` /
`[tool.coverage.*]` in `pyproject.toml`. Tests live in `tests/`.
`tests/conftest.py` points the app at a throwaway sqlite database (via `DATABASE_URL`) so the
suite never touches a real Postgres instance and needs no external services in CI.

## Pre-existing bugs found while adopting this tooling (not fixed here)

Static analysis surfaced several bugs that predate this tooling setup. Each is marked in the code
with a `KNOWN BUG (pre-existing)` comment and a suppression so CI doesn't block on something out
of this task's scope — grep for `KNOWN BUG` to find them all. Summary:

- `scheduler_thread.py` — `SocialPublisherService.refresh_youtube_token()` doesn't exist, so the
  45-minute YouTube token refresh cycle always fails silently; connected YouTube accounts stop
  being able to publish once their access token expires. Needs a real OAuth2 refresh-token-grant
  implementation.
- `api/routes.py` (`/api/models/info`) — `MemoryService.get_stats()` doesn't exist, so the RAG
  memory-node count always silently shows 0.
- `api/routes.py` (`/api/schedule`) — `StrategyAgent.schedule_posts()` doesn't exist, so this
  endpoint always returns a 500.
- `services/memory_service.py` — the ChromaDB-empty fallback imports `db.get_all_history`, which
  doesn't exist (it's `db.get_history`); the fallback always raises and is silently swallowed.
- `scripts/sync_scraper_to_rag.py` — out of sync with `MemoryService`'s current API (`__init__`
  takes no `user_id`, there's no `add_context()`); this standalone maintenance script is fully
  broken as written.
- `services/media_service.py` (`_generate_image_bedrock`) — a second `except Exception as e:` on
  the same `try` is unreachable dead code (an earlier, broader `except` already catches
  everything), so if all three image providers fail, the graceful fallback response never fires.
- `services/media_service.py` (`_generate_google_gemini_video`) — `client.files.download(...,
  destination=...)` doesn't match the installed `google-genai` SDK's signature (confirmed by both
  pylint and mypy independently); unverified without a real Google GenAI credential to exercise
  this path.

## Branch protection / required PR checks (one-time GitHub setup)

This can't be done from a workflow file — it's a repo setting an admin configures once, either
in the GitHub UI or via `gh api`. On GitHub.com:

1. Go to **Settings → Branches** on the repo, and add a branch protection rule for `main`
   (or use **Rulesets** — GitHub's newer equivalent — under **Settings → Rules → Rulesets**).
2. Enable **Require a pull request before merging**.
   - Optionally: **Require approvals** (e.g. 1) and **Dismiss stale approvals on new commits**.
3. Enable **Require status checks to pass before merging**, then search for and select
   **`Python Lint & Security`**, **`Frontend Lint`**, and **`Test`** (three of the four job names
   from `code-quality.yml` — leave `Static type check (mypy, report-only)` out, it's advisory)
   once each has run at least once on a PR or push — GitHub only lists checks that have reported
   at least one result.
4. Optionally enable **Require branches to be up to date before merging**.
5. Optionally enable **Require conversation resolution before merging**.
6. Optionally enable **Do not allow bypassing the above settings** (applies the rule to admins
   too, not just other contributors).

Equivalent via the `gh` CLI (repo admin token required), for example:

```bash
gh api repos/:owner/:repo/branches/main/protection -X PUT -f required_status_checks[strict]=true \
  -f required_status_checks[contexts][]="Python Lint & Security" \
  -f required_status_checks[contexts][]="Frontend Lint" \
  -f required_status_checks[contexts][]="Test" \
  -f required_pull_request_reviews[required_approving_review_count]=1 \
  -f enforce_admins=true
```

## Further security scanning

Bandit only covers Python source-level issues. Consider also enabling **GitHub Dependabot
alerts** and **CodeQL** under **Settings → Code security** — both are free, first-party, and need
no extra config file to start (CodeQL will offer to add its own workflow).
