#!/usr/bin/env bash
# Qaboom deploy setup — run as root or with sudo
# Usage: sudo bash /srv/qaboom/deploy/setup.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing nginx config..."
cp "$DEPLOY_DIR/nginx-qaboom.io.conf" /etc/nginx/sites-available/qaboom.io
ln -sf /etc/nginx/sites-available/qaboom.io /etc/nginx/sites-enabled/qaboom.io
nginx -t
systemctl reload nginx
echo "    nginx config installed and reloaded."

echo ""
echo "==> Installing systemd service..."
cp "$DEPLOY_DIR/qaboom.service" /etc/systemd/system/qaboom.service
systemctl daemon-reload
systemctl enable qaboom
systemctl restart qaboom
sleep 2
systemctl status qaboom --no-pager
echo "    qaboom service enabled and started."

echo ""
echo "==> Fixing certbot (snap)..."
apt-get remove -y certbot python3-certbot-nginx 2>/dev/null || true
snap install --classic certbot 2>/dev/null || snap refresh certbot
ln -sf /snap/bin/certbot /usr/local/bin/certbot 2>/dev/null || true
echo "    certbot (snap) ready."

echo ""
echo "============================================================"
echo "  NEXT: point qaboom.io DNS → 185.28.23.100 (A record)"
echo "  THEN run:  certbot --nginx -d qaboom.io -d www.qaboom.io"
echo "============================================================"
