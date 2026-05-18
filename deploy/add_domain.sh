#!/bin/bash
# Add or refresh the production domain and SSL certificate.
# DNS A record must point the domain to this droplet first.
# Usage:
#   bash deploy/add_domain.sh pos.tarunyaprokashon.com

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
info() { echo -e "${CYAN}[..]${NC} $1"; }
err() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || err "Run this script as root."

DOMAIN="${1:-${DOMAIN:-}}"
[[ -n "$DOMAIN" ]] || err "Domain is required. Usage: bash deploy/add_domain.sh pos.tarunyaprokashon.com"

APP_DIR="${APP_DIR:-/var/www/pos.tarunyaprokashon.com}"
SERVICE_NAME="${SERVICE_NAME:-bookshop-pos}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.bookshop_env}"
SOCKET_DIR="${SOCKET_DIR:-/run/$SERVICE_NAME}"
SOCKET_FILE="${SOCKET_FILE:-$SOCKET_DIR/gunicorn.sock}"
NGINX_SITE="${NGINX_SITE:-$SERVICE_NAME}"

[[ -f "$APP_DIR/manage.py" ]] || err "Project folder not found or invalid: $APP_DIR"
[[ -f "$ENV_FILE" ]] || err "Environment file not found: $ENV_FILE"

set_env() {
  local key="$1"
  local value="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s#^$key=.*#$key=$value#" "$ENV_FILE"
  else
    echo "$key=$value" >> "$ENV_FILE"
  fi
}

info "Installing DNS/SSL tools if needed..."
apt-get update -qq
apt-get install -y -qq curl dnsutils certbot python3-certbot-nginx

SERVER_IP="$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
RESOLVED_IP="$(dig +short A "$DOMAIN" | tail -1)"

if [[ "$RESOLVED_IP" != "$SERVER_IP" ]]; then
  warn "DNS does not appear to point here yet."
  warn "  $DOMAIN resolves to: ${RESOLVED_IP:-not found}"
  warn "  This droplet IP:     $SERVER_IP"
  read -r -p "Continue anyway? (y/N): " CONTINUE
  [[ "$CONTINUE" == "y" || "$CONTINUE" == "Y" ]] || exit 0
else
  log "DNS is correct: $DOMAIN -> $SERVER_IP"
fi

read -r -p "Email for Let's Encrypt SSL: " SSL_EMAIL
[[ -n "$SSL_EMAIL" ]] || err "Email is required for SSL."

info "Updating Django environment..."
set_env "DJANGO_ALLOWED_HOSTS" "$SERVER_IP,$DOMAIN"
set_env "DJANGO_CSRF_TRUSTED_ORIGINS" "http://$SERVER_IP,http://$DOMAIN,https://$DOMAIN"
set_env "DJANGO_CORS_ALLOW_ALL_ORIGINS" "False"

info "Writing Nginx site for $DOMAIN..."
cat > "/etc/nginx/sites-available/$NGINX_SITE" <<NGINXEOF
upstream bookshop_pos_app {
    server unix:$SOCKET_FILE;
}

server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 20M;

    location /media/ {
        alias $APP_DIR/media/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location /static/ {
        alias $APP_DIR/staticfiles/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://bookshop_pos_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
NGINXEOF

ln -sf "/etc/nginx/sites-available/$NGINX_SITE" "/etc/nginx/sites-enabled/$NGINX_SITE"
nginx -t
systemctl reload nginx

info "Requesting Let's Encrypt certificate..."
certbot --nginx \
  -d "$DOMAIN" \
  --non-interactive \
  --agree-tos \
  -m "$SSL_EMAIL" \
  --redirect

info "Enabling secure Django flags..."
set_env "DJANGO_SECURE_SSL_REDIRECT" "True"
set_env "DJANGO_SESSION_COOKIE_SECURE" "True"
set_env "DJANGO_CSRF_COOKIE_SECURE" "True"
set_env "DJANGO_SECURE_HSTS_SECONDS" "31536000"
set_env "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS" "False"
set_env "DJANGO_SECURE_HSTS_PRELOAD" "False"

chmod 600 "$ENV_FILE"
systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" || err "$SERVICE_NAME restart failed. Check: journalctl -u $SERVICE_NAME -n 80 --no-pager"

(crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet") | sort -u | crontab -

echo ""
echo "============================================================"
echo -e "  ${GREEN}Domain and SSL complete.${NC}"
echo "============================================================"
echo -e "  Site:  ${CYAN}https://$DOMAIN${NC}"
echo -e "  Admin: ${CYAN}https://$DOMAIN/admin/${NC}"
echo "============================================================"
