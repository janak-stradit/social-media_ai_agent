# Contributing

## Local setup

```bash
pip install -r requirements-dev.txt
```

## Before opening a PR

```bash
ruff check .            # lint
ruff format .           # auto-format
pytest                  # unit tests
pytest --cov --cov-report=term-missing   # tests + coverage report
```

All four run in CI (`.github/workflows/ci.yml`) on every push and pull request against `main`.
A PR won't be mergeable until they pass, once branch protection is turned on (see below).

## Tooling choices

- **Ruff** does both linting and formatting (replaces flake8 + isort + black with one fast tool).
  Config lives in `pyproject.toml` under `[tool.ruff]`. Line length is 120; `E501`
  (line-too-long) is intentionally not enforced — the formatter governs practical width, and
  this codebase has long prompt/URL/SQL strings not worth force-wrapping.
- **pytest** + **pytest-cov** for tests and coverage. Config lives in `pyproject.toml` under
  `[tool.pytest.ini_options]` / `[tool.coverage.*]`. Tests live in `tests/`.
- `tests/conftest.py` points the app at a throwaway sqlite database (via `DATABASE_URL`) so the
  suite never touches a real Postgres instance and needs no external services in CI.

## Branch protection / required PR checks (one-time GitHub setup)

This can't be done from a workflow file — it's a repo setting an admin configures once, either
in the GitHub UI or via `gh api`. On GitHub.com:

1. Go to **Settings → Branches** on the repo, and add a branch protection rule for `main`
   (or use **Rulesets** — GitHub's newer equivalent — under **Settings → Rules → Rulesets**).
2. Enable **Require a pull request before merging**.
   - Optionally: **Require approvals** (e.g. 1) and **Dismiss stale approvals on new commits**.
3. Enable **Require status checks to pass before merging**, then search for and select
   **`Lint, format & test`** (the job name from `ci.yml`) once it has run at least once on a PR
   or push — GitHub only lists checks that have reported at least one result.
4. Optionally enable **Require branches to be up to date before merging**.
5. Optionally enable **Require conversation resolution before merging**.
6. Optionally enable **Do not allow bypassing the above settings** (applies the rule to admins
   too, not just other contributors).

Equivalent via the `gh` CLI (repo admin token required), for example:

```bash
gh api repos/:owner/:repo/branches/main/protection -X PUT -f required_status_checks[strict]=true \
  -f required_status_checks[contexts][]="Lint, format & test" \
  -f required_pull_request_reviews[required_approving_review_count]=1 \
  -f enforce_admins=true
```

## Security scanning

None of the tools above catch security-specific issues (hardcoded secrets, injection risks,
etc.). Consider enabling **GitHub Dependabot alerts** and **CodeQL** under
**Settings → Code security** — both are free, first-party, and need no extra config file to
start (CodeQL will offer to add its own workflow).
