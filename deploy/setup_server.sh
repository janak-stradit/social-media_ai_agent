#!/usr/bin/env bash
# One-time (and idempotent) EC2 preparation - Ubuntu 24.04 / 22.04.
# Run as root:  sudo bash deploy/setup_server.sh <app-user>
# <app-user> must have passwordless sudo (the default EC2 "ubuntu" user does).
# remote_deploy.sh calls it automatically on the first deploy and again
# whenever this file, the systemd units or the nginx config change.
set -euo pipefail

APP_USER="${1:-ubuntu}"
BASE=/opt/socialmedia
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { echo "User $APP_USER does not exist." >&2; exit 1; }

echo "==> System packages"
apt-get update -y
apt-get install -y software-properties-common curl git rsync nginx \
    build-essential libpq-dev ffmpeg ca-certificates
# The app is pinned to Python 3.11 (same as the Dockerfile and CI).
if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
fi
apt-get install -y python3.11 python3.11-venv python3.11-dev

echo "==> Swap (torch + sentence-transformers need headroom on small instances)"
if ! swapon --show | grep -q '/swapfile'; then
    if [ ! -f /swapfile ]; then
        fallocate -l 4G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
    fi
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Directories"
mkdir -p "$BASE/app/static/uploads" "$BASE/ms-playwright" "$BASE/hf-cache"
chown -R "$APP_USER:$APP_USER" "$BASE"
# nginx (www-data) serves /static directly, so it must be able to traverse these
chmod 755 "$BASE" "$BASE/app"

echo "==> systemd services"
for unit in socialmedia-web socialmedia-scheduler; do
    sed "s/__APP_USER__/$APP_USER/g" "$HERE/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
systemctl enable socialmedia-web socialmedia-scheduler

echo "==> nginx"
SITE=/etc/nginx/sites-available/socialmedia.conf
# After "certbot --nginx" the site file holds the HTTPS blocks; don't wipe them.
if [ -f "$SITE" ] && grep -q "managed by Certbot" "$SITE"; then
    echo "nginx site is managed by Certbot - leaving $SITE unchanged"
else
    install -m 644 "$HERE/nginx/socialmedia.conf" "$SITE"
fi
ln -sf "$SITE" /etc/nginx/sites-enabled/socialmedia.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl reload nginx || systemctl restart nginx

echo "==> Server setup complete"
