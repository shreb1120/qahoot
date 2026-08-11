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
    # https, not http. This redirect fires for anyone who reaches the www host,
    # and the app hands out invite links carrying a single-use token **in the
    # URL path**. Redirecting to http:// put that token on the wire in cleartext
    # on the first hop — and a first-time invitee has no HSTS entry for this
    # domain yet, so the browser really does send it.
    return 301 https://qaboom.io$request_uri;
}

# ─── Main HTTP block (certbot will add HTTPS) ─────────────────────────────────
server {
    listen 80;
    listen [::]:80;
    server_name qaboom.io;
    server_tokens off;

    # Must exceed the app's MAX_CONTENT_LENGTH (500 MB), not undercut it.
    # At 100M nginx rejected a 150 MB recording itself, with its own bare error
    # page — while the app's upload screen and its 413 handler both said the
    # limit was 500 MB. The app owns this limit so that the person who hits it
    # gets an explanation and a suggestion; nginx is only here to stop something
    # absurd reaching the worker.
    client_max_body_size 512M;

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
# This block used to fail silently, and did — for months, across six domains.
#
# Two faults, and the second is the one that actually mattered:
#
#   * every command sent its errors to /dev/null and ended in `|| true`, so the
#     script printed "certbot (snap) ready" whether or not anything worked;
#   * it symlinked /usr/local/bin/certbot, while systemd's certbot.timer invokes
#     **/usr/bin/certbot** by absolute path. So even a perfectly successful snap
#     install left the timer running the broken apt binary twice a day, quietly
#     failing, with certificates marching toward expiry.
#
# `set -euo pipefail` is already on, so removing the suppression is most of the
# fix. The rest is putting the symlink where the timer will actually look, and
# proving the result works instead of announcing that it does.
if dpkg -l certbot 2>/dev/null | grep -q '^ii'; then
  apt-get remove -y certbot python3-certbot python3-certbot-nginx
fi

snap install --classic certbot || snap refresh certbot

# /usr/bin, not /usr/local/bin — see above.
ln -sf /snap/bin/certbot /usr/bin/certbot

# The apt package's timer is masked by the snap; if it survived a partial
# removal it would race the snap timer, so make sure it cannot fire.
systemctl mask certbot.timer 2>/dev/null || true

# Prove it. A renewal that is merely *scheduled* is exactly the state this
# script used to leave behind: certbot.timer ran on time and crashed every time.
if [ -d /etc/letsencrypt/renewal ] && ls /etc/letsencrypt/renewal/*.conf >/dev/null 2>&1; then
  echo "    verifying renewal against the staging server..."
  certbot renew --dry-run
  echo "    certbot (snap) ready — renewals verified."
else
  certbot --version
  echo "    certbot (snap) installed; no certificates yet, nothing to verify."
fi

echo ""
echo "============================================================"
echo "  If DNS is already pointing to this server, run now:"
echo "    sudo certbot --nginx -d qaboom.io -d www.qaboom.io"
echo "  certbot will add the HTTPS block automatically."
echo "============================================================"
