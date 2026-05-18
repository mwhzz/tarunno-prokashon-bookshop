#!/bin/bash
# Tarunno Prokashon Bookshop POS - first production setup
# Run as root on Ubuntu:
#   bash deploy/setup.sh

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

APP_DIR="${APP_DIR:-/var/www/pos.tarunyaprokashon.com}"
APP_USER="${APP_USER:-bookshoppos}"
SERVICE_NAME="${SERVICE_NAME:-bookshop-pos}"
DOMAIN="${DOMAIN:-pos.tarunyaprokashon.com}"
VENV="${VENV:-$APP_DIR/venv}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.bookshop_env}"
SOCKET_DIR="${SOCKET_DIR:-/run/$SERVICE_NAME}"
SOCKET_FILE="${SOCKET_FILE:-$SOCKET_DIR/gunicorn.sock}"
NGINX_SITE="${NGINX_SITE:-$SERVICE_NAME}"

echo ""
echo "============================================================"
echo "  Tarunno Prokashon Bookshop POS - Production Setup"
echo "============================================================"
echo ""

read -r -p "Domain [$DOMAIN]: " DOMAIN_INPUT
DOMAIN="${DOMAIN_INPUT:-$DOMAIN}"
[[ -n "$DOMAIN" ]] || err "Domain is required."

read -r -p "Admin username [admin]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"

read -r -s -p "Admin password: " ADMIN_PASS
echo ""
while [[ ${#ADMIN_PASS} -lt 8 ]]; do
  warn "Password must be at least 8 characters."
  read -r -s -p "Admin password: " ADMIN_PASS
  echo ""
done

read -r -p "Admin email: " ADMIN_EMAIL

info "Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx curl git rsync

SERVER_IP="$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
SECRET_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
)"

log "Server IP detected: $SERVER_IP"

info "Creating system user..."
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
usermod -aG www-data "$APP_USER"

info "Preparing project directory..."
mkdir -p "$APP_DIR" "$LOG_DIR" "$APP_DIR/media/books" "$APP_DIR/staticfiles"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
if [[ "$(readlink -f "$PROJECT_ROOT")" != "$(readlink -f "$APP_DIR")" ]]; then
  rsync -a \
    --exclude '.env' \
    --exclude '.bookshop_env' \
    --exclude 'db.sqlite3' \
    --exclude 'media/' \
    --exclude 'staticfiles/' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    "$PROJECT_ROOT/" "$APP_DIR/"
else
  log "Project already lives in $APP_DIR; copy skipped."
fi

chown -R "$APP_USER":www-data "$APP_DIR"
chmod -R 775 "$APP_DIR/media" "$APP_DIR/staticfiles" "$LOG_DIR"

info "Writing environment file..."
cat > "$ENV_FILE" <<ENVEOF
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$SERVER_IP,$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=http://$SERVER_IP,http://$DOMAIN,https://$DOMAIN
DJANGO_CORS_ALLOW_ALL_ORIGINS=False
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SESSION_COOKIE_SECURE=False
DJANGO_CSRF_COOKIE_SECURE=False
DJANGO_SECURE_HSTS_SECONDS=0
DJANGO_DB_NAME=$APP_DIR/db.sqlite3
DJANGO_STATIC_ROOT=$APP_DIR/staticfiles
DJANGO_MEDIA_ROOT=$APP_DIR/media
ENVEOF
chown "$APP_USER":"$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

info "Creating Python virtual environment..."
sudo -u "$APP_USER" python3 -m venv "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

run_django() {
  sudo -u "$APP_USER" bash -c "
    set -a
    source '$ENV_FILE'
    set +a
    cd '$APP_DIR'
    '$VENV/bin/python' manage.py $*
  "
}

info "Running Django checks..."
run_django check

info "Running migrations..."
run_django migrate --noinput -v 0

info "Collecting static files..."
run_django collectstatic --noinput -v 0

info "Creating admin user..."
TMP_ADMIN_SCRIPT="$(mktemp)"
trap 'rm -f "$TMP_ADMIN_SCRIPT"' EXIT
cat > "$TMP_ADMIN_SCRIPT" <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ['ADMIN_USER']
email = os.environ.get('ADMIN_EMAIL', '')
password = os.environ['ADMIN_PASS']

user, created = User.objects.get_or_create(username=username, defaults={'email': email})
if created:
    user.is_staff = True
    user.is_superuser = True
    user.set_password(password)
    user.save()
    print('Admin user created.')
else:
    print('Admin user already exists.')
PY
chmod 644 "$TMP_ADMIN_SCRIPT"
sudo -u "$APP_USER" env ADMIN_USER="$ADMIN_USER" ADMIN_EMAIL="$ADMIN_EMAIL" ADMIN_PASS="$ADMIN_PASS" bash -c "
  set -a
  source '$ENV_FILE'
  set +a
  cd '$APP_DIR'
  '$VENV/bin/python' manage.py shell < '$TMP_ADMIN_SCRIPT'
"

info "Creating systemd service..."
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<SVCEOF
[Unit]
Description=Tarunno Prokashon Bookshop POS - Gunicorn
After=network.target

[Service]
User=$APP_USER
Group=www-data
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
RuntimeDirectory=$SERVICE_NAME
RuntimeDirectoryMode=0755
ExecStart=$VENV/bin/gunicorn \\
    --workers 3 \\
    --bind unix:$SOCKET_FILE \\
    --umask 007 \\
    --access-logfile $LOG_DIR/access.log \\
    --error-logfile $LOG_DIR/error.log \\
    --timeout 120 \\
    bookshop.wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl is-active --quiet "$SERVICE_NAME" || err "$SERVICE_NAME did not start. Check: journalctl -u $SERVICE_NAME -n 80 --no-pager"
log "Gunicorn service is running through Unix socket."

info "Creating Nginx site for $DOMAIN..."
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
log "Nginx site enabled without changing existing default/main sites."

info "Configuring firewall..."
ufw allow OpenSSH -q || true
ufw allow 'Nginx Full' -q || true
ufw --force enable -q || true

echo ""
echo "============================================================"
echo -e "  ${GREEN}Setup complete.${NC}"
echo "============================================================"
echo -e "  Site:  ${CYAN}http://$DOMAIN${NC}"
echo -e "  Admin: ${CYAN}http://$DOMAIN/admin/${NC}"
echo ""
echo "  Add SSL after DNS points to this droplet:"
echo -e "  ${YELLOW}bash deploy/add_domain.sh $DOMAIN${NC}"
echo ""
echo "  Update later from GitHub:"
echo -e "  ${YELLOW}cd $APP_DIR && bash deploy/update.sh${NC}"
echo "============================================================"
