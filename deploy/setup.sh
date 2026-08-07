#!/usr/bin/env bash
# Qaboom deploy setup — run as root or with sudo
# Usage: sudo bash /srv/qaboom/deploy/setup.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing nginx config (HTTP only — cert doesn't exist yet)..."
cat > /etc/nginx/sites-available/qaboom.io << 'NGINXEOF'
# ─── Rate limiting zones ──────────────────────────────────────────────────────
limit_req_zone $binary_remote_addr zone=qb_general:10m rate=20r/s;
limit_req_zone $binary_remote_addr zone=qb_auth:10m rate=5r/s;

# ─── Redirect www → non-www ───────────────────────────────────────────────────
server {
    listen 80;
    listen [::]:80;
    server_name www.qaboom.io;
    server_tokens off;
    return 301 http://qaboom.io$request_uri;
}

# ─── Main HTTP block (certbot will add HTTPS) ─────────────────────────────────
server {
    listen 80;
    listen [::]:80;
    server_name qaboom.io;
    server_tokens off;

    client_max_body_size 100M;

    # Required for certbot ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location /auth/ {
        limit_req zone=qb_auth burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        limit_req zone=qb_general burst=40 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/qaboom.io /etc/nginx/sites-enabled/qaboom.io
nginx -t
systemctl reload nginx
echo "    nginx config installed and reloaded (HTTP only)."

echo ""
echo "==> Installing systemd service..."
cp "$DEPLOY_DIR/qaboom.service" /etc/systemd/system/qaboom.service
systemctl daemon-reload
systemctl enable qaboom
systemctl restart qaboom
sleep 2
systemctl status qaboom --no-pager

echo ""
echo "==> Fixing certbot (snap)..."
apt-get remove -y certbot python3-certbot-nginx 2>/dev/null || true
snap install --classic certbot 2>/dev/null || snap refresh certbot
ln -sf /snap/bin/certbot /usr/local/bin/certbot 2>/dev/null || true
echo "    certbot (snap) ready."

echo ""
echo "============================================================"
echo "  If DNS is already pointing to this server, run now:"
echo "    sudo certbot --nginx -d qaboom.io -d www.qaboom.io"
echo "  certbot will add the HTTPS block automatically."
echo "============================================================"
