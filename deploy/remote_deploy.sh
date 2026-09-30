#!/usr/bin/env bash
# Runs ON THE EC2 INSTANCE (as the deploy user) after GitHub Actions has
# rsynced the code into /opt/socialmedia/app. The .env content arrives on
# stdin (from the ENV_FILE GitHub secret) so it is never written to the
# runner's disk, the command line or the logs.
#   usage: remote_deploy.sh <git-sha> <branch> < env-file
set -euo pipefail

BASE=/opt/socialmedia
APP="$BASE/app"
VENV="$BASE/venv"
SHA="${1:-unknown}"
BRANCH="${2:-unknown}"
cd "$APP"

echo "==> Writing .env from GitHub secret"
umask 077
ENV_TMP="$(mktemp "$APP/.env.XXXXXX")"
cat > "$ENV_TMP"
if [ ! -s "$ENV_TMP" ]; then
    rm -f "$ENV_TMP"
    echo "ENV_FILE secret is empty - refusing to deploy without configuration." >&2
    exit 1
fi
# Normalise Windows line endings pasted into the secret
sed -i 's/\r$//' "$ENV_TMP"
mv "$ENV_TMP" "$APP/.env"
chmod 600 "$APP/.env"
umask 022

echo "==> Server setup (only when deploy/ server files changed)"
SETUP_HASH="$(cat deploy/setup_server.sh deploy/systemd/*.service deploy/nginx/*.conf | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$BASE/.setup-hash" 2>/dev/null || true)" != "$SETUP_HASH" ]; then
    sudo bash deploy/setup_server.sh "$(id -un)"
    echo "$SETUP_HASH" > "$BASE/.setup-hash"
fi

echo "==> Python dependencies"
[ -x "$VENV/bin/python" ] || python3.11 -m venv "$VENV"
REQ_HASH="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$BASE/.requirements-hash" 2>/dev/null || true)" != "$REQ_HASH" ]; then
    "$VENV/bin/pip" install --upgrade pip wheel
    # CPU-only torch: the default PyPI wheel bundles ~2.5 GB of CUDA libraries
    # that an EC2 CPU instance can't use.
    TORCH_PIN="$(grep -E '^torch==' requirements.txt || true)"
    if [ -n "$TORCH_PIN" ]; then
        "$VENV/bin/pip" install "$TORCH_PIN" --index-url https://download.pytorch.org/whl/cpu
    fi
    "$VENV/bin/pip" install -r requirements.txt
    # Chromium + its system libraries for the onboarding scraper
    sudo "$VENV/bin/playwright" install-deps chromium
    PLAYWRIGHT_BROWSERS_PATH="$BASE/ms-playwright" "$VENV/bin/playwright" install chromium
    echo "$REQ_HASH" > "$BASE/.requirements-hash"
else
    echo "requirements.txt unchanged - skipping pip install"
fi

mkdir -p "$APP/static/uploads"
echo "$SHA $BRANCH $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$BASE/DEPLOYED_VERSION"

echo "==> Restarting services"
sudo systemctl restart socialmedia-web
sudo systemctl restart socialmedia-scheduler

echo "==> Health check"
# First start loads torch + the embedding model and runs init_db against RDS.
for i in $(seq 1 36); do
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/login || true)"
    if [ "$code" = "200" ]; then
        echo "App is up (HTTP 200 on /login) - deployed $SHA ($BRANCH)"
        systemctl is-active --quiet socialmedia-scheduler || {
            echo "Scheduler service is not running:" >&2
            sudo systemctl status socialmedia-scheduler --no-pager -l | tail -n 30 >&2
            exit 1
        }
        exit 0
    fi
    sleep 5
done

echo "Health check failed (last HTTP code: ${code:-none}). Recent web logs:" >&2
journalctl -u socialmedia-web -n 80 --no-pager >&2 || sudo systemctl status socialmedia-web --no-pager -l >&2
exit 1
