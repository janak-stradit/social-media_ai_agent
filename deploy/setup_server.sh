#!/usr/bin/env bash
# One-time (and idempotent) EC2 preparation for the Docker deployment - Ubuntu 24.04.
# Run as root:  sudo bash /opt/socialmedia/deploy/setup_server.sh
# remote_deploy.sh calls it automatically on the first deploy and again
# whenever this file or the nginx config change.
set -euo pipefail

BASE=/opt/socialmedia
DATA="$BASE/data"
APP_UID=1000   # the "app" user inside the image (see Dockerfile)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

echo "==> System packages (Docker, Compose, nginx)"
apt-get update -y
apt-get install -y ca-certificates curl rsync nginx docker.io docker-compose-v2
systemctl enable --now docker

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

echo "==> Data directories (mounted into the containers)"
mkdir -p "$DATA/static/uploads" "$DATA/brand" "$DATA/chroma_db"
chown -R "$APP_UID:$APP_UID" "$DATA"
# nginx (www-data) serves /static/uploads straight from here
chmod 755 "$BASE" "$DATA" "$DATA/static" "$DATA/static/uploads"

# Earlier non-Docker deployments ran the app as systemd services on the same port
for unit in socialmedia-web socialmedia-scheduler; do
    if [ -f "/etc/systemd/system/$unit.service" ]; then
        systemctl disable --now "$unit" || true
        rm -f "/etc/systemd/system/$unit.service"
        systemctl daemon-reload
    fi
done

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
