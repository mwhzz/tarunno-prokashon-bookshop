from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import CashTransaction, DailyCash
from books.models import Book
from expenses.models import Expense, ExpenseCategory
from sales.models import Customer, Payment, Sale, SaleItem
from sales.daily_report import build_daily_owner_summary


class DailyTelegramReportTests(TestCase):
    def setUp(self):
        self.report_date = timezone.localdate()
        self.cash = DailyCash.objects.create(
            date=self.report_date,
            opening_balance=Decimal("500.00"),
        )
        self.customer = Customer.objects.create(
            name="Wholesale Customer",
            phone="01700000000",
            total_due=Decimal("200.00"),
        )
        self.book = Book.objects.create(
            title="Test Book",
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("100.00"),
        )
        self.sale = Sale.objects.create(
            customer=self.customer,
            customer_name=self.customer.name,
            customer_phone=self.customer.phone,
            subtotal=Decimal("1000.00"),
            total=Decimal("1000.00"),
            total_cost=Decimal("500.00"),
            paid_amount=Decimal("800.00"),
        )
        SaleItem.objects.create(
            sale=self.sale,
            book=self.book,
            quantity=10,
            unit_price=Decimal("100.00"),
            cost_price=Decimal("50.00"),
        )
        Payment.objects.create(
            sale=self.sale,
            amount=Decimal("800.00"),
            method="cash",
        )
        CashTransaction.objects.create(
            daily_cash=self.cash,
            transaction_type="sale",
            amount=Decimal("800.00"),
            note="Test cash sale",
        )
        category = ExpenseCategory.objects.create(name="Office")
        Expense.objects.create(category=category, amount=Decimal("100.00"))

    def test_build_daily_owner_summary_contains_key_totals(self):
        message = build_daily_owner_summary(self.report_date)

        self.assertIn("Tarunno Prokashon - Daily Summary", message)
        self.assertIn("Invoices: 1", message)
        self.assertIn("Sales: BDT 1,000.00", message)
        self.assertIn("Collections: BDT 800.00", message)
        self.assertIn("Expenses: BDT 100.00", message)
        self.assertIn("Top books:", message)

    def test_management_command_dry_run_prints_message(self):
        out = StringIO()

        call_command("send_daily_telegram_report", "--dry-run", stdout=out)

        self.assertIn("Daily Summary", out.getvalue())
