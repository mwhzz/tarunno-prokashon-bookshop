#!/bin/bash
# Pull latest code from GitHub and update the live POS app.
# Run on the droplet:
#   cd /var/www/pos.tarunyaprokashon.com
#   bash deploy/update.sh

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${CYAN}[..]${NC} $1"; }
err() { echo -e "${RED}[ERR]${NC} $1"; exit 1; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || err "Run this script as root."

APP_DIR="${APP_DIR:-/var/www/pos.tarunyaprokashon.com}"
APP_USER="${APP_USER:-bookshoppos}"
SERVICE_NAME="${SERVICE_NAME:-bookshop-pos}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-bookshop-pos-telegram-bot}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-}"
VENV="${VENV:-$APP_DIR/venv}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.bookshop_env}"

[[ -d "$APP_DIR" ]] || err "Project folder not found: $APP_DIR"
[[ -d "$APP_DIR/.git" ]] || err "$APP_DIR is not a Git repository. Clone the project there first."
[[ -f "$ENV_FILE" ]] || err "Environment file not found: $ENV_FILE"
[[ -x "$VENV/bin/python" ]] || err "Python venv not found: $VENV"

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [[ -z "$BRANCH" ]]; then
  BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"
fi
[[ "$BRANCH" != "HEAD" && -n "$BRANCH" ]] || BRANCH="main"

log "Project: $APP_DIR"
log "Environment: $ENV_FILE"
log "Git target: $REMOTE/$BRANCH"

info "Fetching latest code from GitHub..."
git -C "$APP_DIR" fetch "$REMOTE" "$BRANCH"
git -C "$APP_DIR" reset --hard "$REMOTE/$BRANCH"

info "Fixing file permissions..."
chown -R "$APP_USER":www-data "$APP_DIR"
chmod -R 775 "$APP_DIR/media" "$APP_DIR/staticfiles" "$APP_DIR/logs" 2>/dev/null || true
chmod 600 "$ENV_FILE" 2>/dev/null || true

info "Installing production dependencies..."
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

info "Restarting service..."
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl is-active --quiet "$SERVICE_NAME" || err "$SERVICE_NAME restart failed. Check: journalctl -u $SERVICE_NAME -n 80 --no-pager"

if systemctl cat "$BOT_SERVICE_NAME" >/dev/null 2>&1; then
  info "Restarting Telegram bot worker..."
  systemctl restart "$BOT_SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$BOT_SERVICE_NAME" || err "$BOT_SERVICE_NAME restart failed. Check: journalctl -u $BOT_SERVICE_NAME -n 80 --no-pager"
fi

log "Update complete."
