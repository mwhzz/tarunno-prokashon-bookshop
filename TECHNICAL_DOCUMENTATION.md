# 📘 Bookshop Management System: Full Technical Documentation (End-to-End)

## 1. Project Architecture Overview
The system is built using a decoupled architecture where the Backend (Django REST Framework) manages business logic and data, and the Frontend (Vanilla HTML/JS) consumes these APIs. This allows for a "Single Page Application" feel within a traditional Django project.

---

## 2. Detailed App Breakdown

### 📂 `books` (The Product Catalog)
This app manages the core inventory items (books).

#### Models:
- **`Group`**: Subject/Series/Category.
    - Fields: `name`, `description`.
- **`Book`**: The central entity.
    - Fields: `title`, `author`, `publisher`, `isbn`, `edition`, `image`, `book_type` (single/series).
    - Pricing: `mrp`, `purchase_price`, `selling_price`.
    - Logic: Calculates `current_stock` via a property that links to the `stock` app.
- **APIs:**
    - `BookViewSet`: Searchable by title/author. Actions: `out_of_stock`, `low_stock`.

---

### 📂 `sales` (Revenue & CRM)
Manages the sales process, invoicing, and customer accounts.

#### Models:
- **`Customer`**: 
    - Fields: `name`, `phone`, `customer_code`, `customer_type` (Retail/Wholesale), `total_due`, `default_commission`.
- **`Sale` (The Invoice)**:
    - Fields: `invoice_number`, `customer`, `sale_type`, `subtotal`, `discount`, `packaging_charge`, `courier_charge`, `total`, `paid_amount`, `due_amount`, `status`.
    - Logic: Automated generation of invoice numbers (INV-YYYYMMDD-XXXX).
- **`SaleItem`**: Individual book entries in a sale. Calculates profit per item.
- **`Payment`**: Ledger for payments against a specific invoice.
- **`SaleReturn`**: Handles book returns and balance adjustments.

#### Key API Actions:
- `dashboard`: Returns aggregate data (Revenue, Net Profit, Top Books).
- `add_payment`: Records a new payment and reduces customer due.
- `return_items`: Adjusts stock back to 'Shop' and updates financial balances.
- `download_pdf`: Generates invoice PDFs with Bengali font support.

---

### 📂 `stock` (Inventory Control)
Advanced multi-location stock tracking.

#### Models:
- **`StockEntry`**: The log of every quantity change.
    - Fields: `book`, `quantity` (positive/negative), `source` (purchase, return, adjustment, damage), `location` (shop/godown).
- **`StockSummary`**: A high-performance cache.
    - Fields: `godown_quantity`, `shop_quantity`.
- **`StockTransfer`**: Formal movement records between locations.

#### Business Logic:
- **Atomic Updates:** Stock levels are updated using `select_for_update()` to prevent race conditions during high-volume sales.
- **Location Separation:** Purchases enter 'Godown' by default; POS sales decrement from 'Shop'.

---

### 📂 `expenses` (Operational Costing)
Tracks where the money goes.

#### Models:
- **`ExpenseCategory`**: Rent, Electric, Tea, Salary, etc.
- **`Expense`**: Linked to `DailyCash`.
- **Logic:** Prevents adding an expense if the `DailyCash` balance is insufficient.

---

### 📂 `accounts` (Cash Management)
The system's financial truth.

#### Models:
- **`DailyCash`**:
    - Fields: `date`, `opening_balance`, `closing_balance`, `is_closed`.
- **`CashTransaction`**:
    - Types: `sale`, `cash_in`, `expense`, `cash_out`, `adjustment_in/out`.
- **Workflow:** Every Sale or Expense automatically creates a `CashTransaction`. The `closing_balance` is recalculated in real-time.

---

### 📂 `users` (Access Control)
Role-Based Access Control (RBAC).

#### Roles:
- **Admin:** Can edit prices, see net profit, and manage users.
- **Manager:** Can manage stock and view general reports.
- **Staff:** Limited to POS operations.
- **Logic:** Uses a `Profile` model linked to the Django `User` model via signals.

---

### 📂 `frontend` (UI Layer)
The presentation layer.
- **Templates:** Uses a `base.html` with a custom sidebar and header.
- **Communication:** Uses a central `api()` helper function in JavaScript to talk to DRF.
- **Dynamic Content:** Pages like `dashboard.html` and `pos.html` are dynamic and don't require full page reloads for most actions.

---

## 3. Financial Logic (Net Profit Calculation)
The system calculates **Realized Net Profit**:
1.  **Gross Profit:** (Sale Price - Buy Price) for all sold items.
2.  **Realized Profit:** Gross Profit adjusted by the percentage of the bill actually paid.
3.  **Net Profit:** Realized Profit minus all recorded **Expenses** for the period.

---

## 4. API Reference Summary
- `GET /api/sales/dashboard/`: Master data for charts and KPIs.
- `GET /api/stock/summary/report/`: Stock valuation by category.
- `GET /api/expenses/report/`: Expense breakdown for pie charts.
- `POST /api/sales/add_payment/`: Collect due payments.
- `POST /api/stock/transfers/`: Move stock from Godown to Shop.

---
*Documentation End-to-End v1.2*
*Generated for: Bookshop New Dashboard Project*
