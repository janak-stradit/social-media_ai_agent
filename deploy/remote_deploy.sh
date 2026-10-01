#!/usr/bin/env bash
# Runs ON THE EC2 INSTANCE (as the deploy user, e.g. ubuntu) from GitHub Actions.
#   usage: remote_deploy.sh <image> <registry> <git-sha> <branch>
#   stdin: line 1 = ECR login password (valid 12 h), rest = the .env content
#          (ENV_FILE secret). Neither touches the command line or the logs.
# Pulls <image> from ECR, (re)starts the web + scheduler containers and checks
# the app answers. If the new version fails the check, the previous image is
# started again and the deploy is reported as failed.
set -euo pipefail

BASE=/opt/socialmedia
DEPLOY="$BASE/deploy"
DATA="$BASE/data"
APP_UID=1000
IMAGE="${1:?image required}"
REGISTRY="${2:?registry required}"
SHA="${3:-unknown}"
BRANCH="${4:-unknown}"
COMPOSE=(docker compose -f "$DEPLOY/docker-compose.prod.yml")
# Rough space one image pull needs (compressed layers + unpacked copy)
MIN_FREE_GB=8

IFS= read -r ECR_PASSWORD || true
[ -n "${ECR_PASSWORD:-}" ] || { echo "No ECR login password received on stdin." >&2; exit 1; }

echo "==> Reading .env from GitHub secret"
umask 077
ENV_TMP="$(mktemp)"
trap 'rm -f "$ENV_TMP"' EXIT
cat > "$ENV_TMP"
if [ ! -s "$ENV_TMP" ]; then
    echo "ENV_FILE secret is empty - refusing to deploy without configuration." >&2
    exit 1
fi
sed -i 's/\r$//' "$ENV_TMP"
umask 022

echo "==> Server setup (only when setup_server.sh / nginx config changed)"
SETUP_HASH="$(cat "$DEPLOY/setup_server.sh" "$DEPLOY"/nginx/*.conf | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$BASE/.setup-hash" 2>/dev/null || true)" != "$SETUP_HASH" ]; then
    sudo bash "$DEPLOY/setup_server.sh"
    echo "$SETUP_HASH" | sudo tee "$BASE/.setup-hash" >/dev/null
fi

# Readable only by the container's app user
sudo install -m 600 -o "$APP_UID" -g "$APP_UID" "$ENV_TMP" "$BASE/.env"

echo "==> Freeing disk space before the pull"
# Keep only the image the running containers use (the rollback target); every
# other image, stopped container and build cache is removed. Older versions
# stay in ECR and can be redeployed with the workflow's image_tag input.
sudo docker container prune -f >/dev/null || true
sudo docker image prune -af >/dev/null || true
sudo docker builder prune -af >/dev/null 2>&1 || true
FREE_GB="$(df --output=avail -BG / | tail -1 | tr -dc '0-9')"
echo "Free disk space: ${FREE_GB} GB"
sudo docker system df || true
if [ "${FREE_GB:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "Only ${FREE_GB} GB free - pulling the new image needs about ${MIN_FREE_GB} GB." >&2
    echo "Grow the EBS volume (see DEPLOYMENT.md -> Troubleshooting -> no space left on device)." >&2
    exit 1
fi

echo "==> Pulling $IMAGE"
printf '%s' "$ECR_PASSWORD" | sudo docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
unset ECR_PASSWORD
sudo docker pull --quiet "$IMAGE"
sudo docker logout "$REGISTRY" >/dev/null

echo "==> Seeding default brand assets (existing files are never overwritten)"
sudo docker run --rm --user "$APP_UID:$APP_UID" -v "$DATA/brand:/seed" --entrypoint sh "$IMAGE" \
    -c 'cp -rn /app/static/img/brand/. /seed/ 2>/dev/null || true'

PREVIOUS_IMAGE="$(cat "$BASE/CURRENT_IMAGE" 2>/dev/null || true)"

start() {
    # --force-recreate: also picks up a changed .env when the image is unchanged
    sudo env APP_IMAGE="$1" "${COMPOSE[@]}" up -d --force-recreate --remove-orphans
}

healthy() {
    # First start loads torch + the embedding model and runs init_db against RDS
    local code=""
    for _ in $(seq 1 36); do
        code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true)"
        if [ "$code" = "200" ]; then
            sleep 5
            if sudo env APP_IMAGE="$1" "${COMPOSE[@]}" ps --status running --services | grep -qx scheduler; then
                return 0
            fi
            echo "Scheduler container is not running." >&2
            return 1
        fi
        sleep 5
    done
    echo "App did not answer HTTP 200 on /login (last code: ${code:-none})." >&2
    return 1
}

echo "==> Starting containers"
start "$IMAGE"

echo "==> Health check"
if healthy "$IMAGE"; then
    echo "$IMAGE" | sudo tee "$BASE/CURRENT_IMAGE" >/dev/null
    echo "$IMAGE $SHA $BRANCH $(date -u +%Y-%m-%dT%H:%M:%SZ)" | sudo tee -a "$BASE/DEPLOY_HISTORY" >/dev/null
    # Only the new image stays on the server; the previous one is removed now
    # that the new version is healthy (it stays in ECR for rollbacks).
    sudo docker image prune -af >/dev/null || true
    echo "Disk after cleanup: $(df -h --output=avail / | tail -1 | tr -d ' ') free"
    echo "Deployed $IMAGE ($BRANCH)"
    exit 0
fi

echo "==> Deploy FAILED - recent logs:" >&2
sudo env APP_IMAGE="$IMAGE" "${COMPOSE[@]}" logs --tail 80 web scheduler >&2 || true

if [ -n "$PREVIOUS_IMAGE" ] && [ "$PREVIOUS_IMAGE" != "$IMAGE" ]; then
    echo "==> Rolling back to $PREVIOUS_IMAGE" >&2
    if sudo docker image inspect "$PREVIOUS_IMAGE" >/dev/null 2>&1; then
        start "$PREVIOUS_IMAGE"
        healthy "$PREVIOUS_IMAGE" && echo "Rollback OK - $PREVIOUS_IMAGE is running." >&2
    else
        echo "Previous image is no longer on this server; redeploy it with the image_tag input." >&2
    fi
fi
exit 1
