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

Then open:

```text
http://127.0.0.1:8000/
```

## Environment

Create a `.env` file from `.env.example` and set production values:

```env
DJANGO_SECRET_KEY=change-this-to-a-long-random-secret
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

For local development, you can temporarily set:

```env
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

## Production Checklist

1. Set a strong `DJANGO_SECRET_KEY`.
2. Set `DJANGO_DEBUG=False`.
3. Set `DJANGO_ALLOWED_HOSTS` to the real domain.
4. Run migrations:

```bash
python manage.py migrate
```

5. Collect static files:

```bash
python manage.py collectstatic
```

6. Serve `staticfiles/` and `media/` from the production web server.
7. Back up the production database regularly.

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
