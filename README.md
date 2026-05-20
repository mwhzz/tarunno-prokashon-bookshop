# Tarunno Prokashon Bookshop Management System

A production-ready Django bookshop management system for sales, inventory, customers, expenses, reporting, and Bangla printable invoices. The app is designed for day-to-day bookstore operations with a fast POS workflow and clean admin dashboards.

## Highlights

- Modern POS for retail and wholesale book sales
- Book catalog with pricing, categories, publication, and cover images
- Shop and warehouse stock tracking
- Customer ledger with due balance support
- Partial payment and due invoice management
- Sales return workflow with stock adjustment
- Expense and account tracking
- Profit, loss, stock value, and dashboard reports
- Daily owner Telegram summary automation
- Bangla A4 printable invoices with logo, local font, and multi-page print support
- Role-aware frontend for admin, manager, and staff users

## Tech Stack

- Python
- Django
- Django REST Framework
- SQLite by default
- HTML, CSS, and vanilla JavaScript frontend
- Local Bangla fonts for invoice printing

## Project Structure

```text
accounts/      Account and cash history APIs
books/         Book catalog and publication data
bookshop/      Django project settings and URL config
expenses/      Expense category and expense tracking
frontend/      Page rendering views and routes
sales/         POS, invoices, customers, payments, returns, reports
static/        Logo, fonts, and static assets
stock/         Stock entries and stock summary
templates/     Frontend pages and printable invoice templates
users/         Staff profile, roles, permissions
```

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

For local development, edit `.env` before running the server:

```env
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SESSION_COOKIE_SECURE=False
DJANGO_CSRF_COOKIE_SECURE=False
DJANGO_SECURE_HSTS_SECONDS=0
```

Then open:

```text
http://127.0.0.1:8000/
```

## Environment

Create a `.env` file from `.env.example` and set production values:

```env
DJANGO_SECRET_KEY=change-this-to-a-long-random-secret
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=pos.tarunyaprokashon.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://pos.tarunyaprokashon.com
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
```

Optional Telegram daily report settings:

```env
TELEGRAM_REPORT_ENABLED=True
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
TELEGRAM_REPORT_STATE_FILE=/var/www/pos.tarunyaprokashon.com/logs/telegram_report_bot_state.json
```

Create a bot from Telegram `@BotFather`, send `/start` to that bot from the owner account, then use `python manage.py send_daily_telegram_report --get-chat-id` to find the chat id.

For local development, you can temporarily set:

```env
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

When `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` are required.

## Production Checklist

1. Set a strong `DJANGO_SECRET_KEY`.
2. Set `DJANGO_DEBUG=False`.
3. Set `DJANGO_ALLOWED_HOSTS` to the real domain.
4. Set `DJANGO_CSRF_TRUSTED_ORIGINS` with the real `https://` origin.
5. Run migrations:

```bash
python manage.py migrate
```

6. Collect static files:

```bash
python manage.py collectstatic
```

7. Serve `staticfiles/` and `media/` from the production web server.
8. Back up the production database and `media/` regularly.

## DigitalOcean Deployment

This setup is designed for a droplet that already hosts another website. It does not use port `8000`; Gunicorn listens on a Unix socket:

```text
/run/bookshop-pos/gunicorn.sock
```

Default production values:

| Item | Value |
| --- | --- |
| Project path | `/var/www/pos.tarunyaprokashon.com` |
| Service | `bookshop-pos` |
| App user | `bookshoppos` |
| Env file | `/var/www/pos.tarunyaprokashon.com/.bookshop_env` |
| Nginx site | `/etc/nginx/sites-available/bookshop-pos` |
| Domain | `pos.tarunyaprokashon.com` |

### First Deploy

Point DNS first:

```text
Type: A
Name: pos
Value: YOUR_DROPLET_IP
```

Then SSH into the droplet:

```bash
ssh root@YOUR_DROPLET_IP
apt-get update
apt-get install -y git
git clone https://github.com/YOUR_USERNAME/tarunno-prokashon-bookshop.git /var/www/pos.tarunyaprokashon.com
cd /var/www/pos.tarunyaprokashon.com
bash deploy/setup.sh
bash deploy/add_domain.sh pos.tarunyaprokashon.com
bash deploy/setup_telegram_report.sh
```

The setup script creates the venv, environment file, database, static files, systemd service, and Nginx server block for only the POS subdomain. It does not remove or replace the droplet's existing main website config.

### Update From GitHub

Local PC:

```bash
git add .
git commit -m "Update POS"
git push
```

Droplet:

```bash
cd /var/www/pos.tarunyaprokashon.com
bash deploy/update.sh
```

The update script fetches GitHub, resets tracked code to the selected branch, installs dependencies, runs `check`, `migrate`, `collectstatic`, and restarts `bookshop-pos`. It keeps `.bookshop_env`, `db.sqlite3`, `media/`, `staticfiles/`, `logs/`, and `venv/` outside Git.

### Daily Telegram Report

After production setup, configure the owner summary automation:

```bash
cd /var/www/pos.tarunyaprokashon.com
bash deploy/setup_telegram_report.sh
```

The script stores Telegram credentials in `.bookshop_env`, writes `/etc/cron.d/bookshop-pos-telegram-report` for the daily scheduled report, and creates a `bookshop-pos-telegram-bot` systemd worker for near-instant bot replies. You can preview the message locally:

```bash
python manage.py send_daily_telegram_report --dry-run
```

Send a short test message:

```bash
python manage.py send_daily_telegram_report --test
```

Send a one-off report from the server:

```bash
python manage.py send_daily_telegram_report --force
```

Ask the bot for reports from Telegram:

```text
/report
/yesterday
/report yesterday
/report 2026-05-20
```

The bot also shows a simple keyboard with `Today's Report`, `Yesterday's Report`, `Help`, and `Date Format` buttons.

When the Accounts page daily cash close button is clicked, the same Telegram report is sent automatically if `TELEGRAM_REPORT_ENABLED=True`.

If the bot does not reply, send `/report` to the bot and run:

```bash
systemctl status bookshop-pos-telegram-bot
journalctl -u bookshop-pos-telegram-bot -n 80 --no-pager
python manage.py poll_telegram_report_bot --show-updates
python manage.py poll_telegram_report_bot --reset-state
```

If `--show-updates` shows a different `chat_id`, update `TELEGRAM_CHAT_ID` in `.bookshop_env` and rerun `bash deploy/setup_telegram_report.sh`.

### Useful Commands

```bash
systemctl status bookshop-pos
journalctl -u bookshop-pos -n 80 --no-pager
tail -f /var/www/pos.tarunyaprokashon.com/logs/error.log
nginx -t
systemctl reload nginx
```

## Main Pages

| Page | Route | Purpose |
| --- | --- | --- |
| Dashboard | `/` | Sales, stock, and finance overview |
| POS | `/pos/` | Create sales and print invoices |
| Books | `/books/` | Manage book catalog |
| Stock | `/stock/` | Track shop and warehouse stock |
| Invoices | `/sales/` | View and print all invoices |
| Due List | `/sales/due/` | Track unpaid and partial invoices |
| Wholesale Customers | `/customers/wholesale/` | Manage wholesale customer ledger |
| Accounts | `/accounts/` | Cash and account overview |
| Reports | `/reports/` | Profit, loss, and stock reports |
| Expenses | `/expenses/` | Track business expenses |
| Users | `/users/manage/` | Manage staff access |

## Key API Endpoints

| Area | Endpoint |
| --- | --- |
| Books | `/api/books/` |
| Stock | `/api/stock/` |
| Sales | `/api/sales/` |
| Customers | `/api/sales/customers/` |
| Expenses | `/api/expenses/` |
| Accounts | `/api/accounts/` |
| Users | `/api/users/` |

## Invoice Printing

Invoices can be printed from the POS success modal or from the invoice list. The print view is:

```text
/api/sales/<sale_id>/print_invoice/
```

The invoice template supports:

- Bangla text and Bengali digits
- Local Kalpurush font
- Business logo
- A4 print layout
- Multi-page item tables
- Repeated table headers on page breaks
- Clean totals and signature section

## Data Safety

The repository intentionally ignores local runtime data:

- `db.sqlite3`
- `media/`
- `.env`
- `staticfiles/`
- Python cache files

Keep production data and uploaded media backed up outside Git.

## License

Private business project. Add a license before publishing publicly.
