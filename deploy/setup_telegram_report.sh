#!/bin/bash
# Configure the daily owner Telegram report cron job.
# Run as root on Ubuntu:
#   bash deploy/setup_telegram_report.sh

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
VENV="${VENV:-$APP_DIR/venv}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.bookshop_env}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/bookshop-pos-telegram-report}"
OLD_WHATSAPP_CRON_FILE="${OLD_WHATSAPP_CRON_FILE:-/etc/cron.d/bookshop-pos-whatsapp-report}"
REPORT_TIME="${REPORT_TIME:-22:30}"
STATE_FILE="${STATE_FILE:-$LOG_DIR/telegram_report_bot_state.json}"

[[ -f "$APP_DIR/manage.py" ]] || err "Project folder not found or invalid: $APP_DIR"
[[ -x "$VENV/bin/python" ]] || err "Python venv not found: $VENV"
[[ -f "$ENV_FILE" ]] || err "Environment file not found: $ENV_FILE"

set_env() {
  local key="$1"
  local value="$2"
  ENV_TARGET="$ENV_FILE" ENV_KEY="$key" ENV_VALUE="$value" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_TARGET"])
key = os.environ["ENV_KEY"]
value = os.environ["ENV_VALUE"]

lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
updated = False
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = f"{key}={value}"
        updated = True
        break
if not updated:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

echo ""
echo "============================================================"
echo "  Daily Telegram Report Setup"
echo "============================================================"
echo ""

warn "Create a bot first in Telegram: open @BotFather, run /newbot, then copy the bot token."
read -r -s -p "Telegram bot token: " BOT_TOKEN
echo ""
[[ -n "$BOT_TOKEN" ]] || err "Telegram bot token is required."

read -r -p "Telegram chat ID, blank to auto-detect after /start: " CHAT_ID
if [[ -z "$CHAT_ID" ]]; then
  echo ""
  warn "Now open your new bot in Telegram and send /start."
  read -r -p "Press Enter after sending /start..."
  CHAT_ID="$(BOT_TOKEN="$BOT_TOKEN" python3 - <<'PY'
import json
import os
import sys
import urllib.request

token = os.environ["BOT_TOKEN"]
url = f"https://api.telegram.org/bot{token}/getUpdates"
with urllib.request.urlopen(url, timeout=30) as response:
    data = json.loads(response.read().decode("utf-8"))

for update in reversed(data.get("result", [])):
    message = (
        update.get("message")
        or update.get("channel_post")
        or update.get("edited_message")
        or update.get("edited_channel_post")
    )
    if not message:
        continue
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is not None:
        print(chat_id)
        sys.exit(0)

sys.exit(1)
PY
)" || err "Could not auto-detect chat ID. Send /start to the bot, then run this script again."
fi

read -r -p "Daily send time in 24h format [$REPORT_TIME]: " REPORT_TIME_INPUT
REPORT_TIME="${REPORT_TIME_INPUT:-$REPORT_TIME}"
[[ "$REPORT_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || err "Invalid time format. Use HH:MM."

info "Saving Telegram report settings..."
set_env "TELEGRAM_REPORT_ENABLED" "True"
set_env "TELEGRAM_BOT_TOKEN" "$BOT_TOKEN"
set_env "TELEGRAM_CHAT_ID" "$CHAT_ID"
set_env "TELEGRAM_REPORT_STATE_FILE" "$STATE_FILE"
chown "$APP_USER":"$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

mkdir -p "$LOG_DIR"
chown -R "$APP_USER":www-data "$LOG_DIR"
rm -f "$OLD_WHATSAPP_CRON_FILE" 2>/dev/null || true

CRON_HOUR="${REPORT_TIME%:*}"
CRON_MINUTE="${REPORT_TIME#*:}"
CRON_HOUR="$((10#$CRON_HOUR))"
CRON_MINUTE="$((10#$CRON_MINUTE))"

info "Writing cron job to $CRON_FILE..."
cat > "$CRON_FILE" <<CRONEOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

$CRON_MINUTE $CRON_HOUR * * * $APP_USER set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; '$VENV/bin/python' manage.py send_daily_telegram_report >> '$LOG_DIR/telegram_report.log' 2>&1
* * * * * $APP_USER set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; '$VENV/bin/python' manage.py poll_telegram_report_bot >> '$LOG_DIR/telegram_bot.log' 2>&1
CRONEOF

chmod 644 "$CRON_FILE"

info "Generating a dry-run report preview..."
sudo -u "$APP_USER" bash -c "
  set -a
  source '$ENV_FILE'
  set +a
  cd '$APP_DIR'
  '$VENV/bin/python' manage.py send_daily_telegram_report --dry-run
"

info "Clearing any existing Telegram webhook so polling can work..."
BOT_TOKEN="$BOT_TOKEN" python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request

token = os.environ["BOT_TOKEN"]
data = urllib.parse.urlencode({"drop_pending_updates": "false"}).encode("utf-8")
request = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/deleteWebhook",
    data=data,
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    result = json.loads(response.read().decode("utf-8"))
if not result.get("ok"):
    raise SystemExit(result)
PY

info "Configuring Telegram command menu..."
sudo -u "$APP_USER" bash -c "
  set -a
  source '$ENV_FILE'
  set +a
  cd '$APP_DIR'
  '$VENV/bin/python' manage.py send_daily_telegram_report --set-menu
"

info "Running one Telegram poll now..."
sudo -u "$APP_USER" bash -c "
  set -a
  source '$ENV_FILE'
  set +a
  cd '$APP_DIR'
  '$VENV/bin/python' manage.py poll_telegram_report_bot --reset-state
"

echo ""
echo "============================================================"
echo -e "  ${GREEN}Daily Telegram report automation is configured.${NC}"
echo "============================================================"
echo -e "  Chat ID: ${CYAN}$CHAT_ID${NC}"
echo -e "  Time:    ${CYAN}$REPORT_TIME daily${NC}"
echo -e "  Log:     ${CYAN}$LOG_DIR/telegram_report.log${NC}"
echo -e "  Bot log: ${CYAN}$LOG_DIR/telegram_bot.log${NC}"
echo ""
echo "  Ask the bot for a report:"
echo -e "  ${YELLOW}Today's Report${NC}"
echo -e "  ${YELLOW}Yesterday's Report${NC}"
echo -e "  ${YELLOW}/report${NC}"
echo -e "  ${YELLOW}/yesterday${NC}"
echo -e "  ${YELLOW}/report yesterday${NC}"
echo -e "  ${YELLOW}/report 2026-05-20${NC}"
echo ""
echo "  Send a test now:"
echo -e "  ${YELLOW}sudo -u $APP_USER bash -c \"set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; '$VENV/bin/python' manage.py send_daily_telegram_report --test\"${NC}"
echo "  Send the full report now:"
echo -e "  ${YELLOW}sudo -u $APP_USER bash -c \"set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; '$VENV/bin/python' manage.py send_daily_telegram_report --force\"${NC}"
echo "============================================================"
