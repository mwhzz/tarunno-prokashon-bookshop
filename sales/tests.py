from decimal import Decimal
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import CashTransaction, DailyCash
from books.models import Book
from expenses.models import Expense, ExpenseCategory
from sales.models import Customer, Payment, Sale, SaleItem
from sales.daily_report import (
    build_daily_owner_summary,
    classify_telegram_report_text,
    poll_telegram_report_bot,
)


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

    def test_report_text_classifier_accepts_owner_commands(self):
        self.assertEqual(classify_telegram_report_text("/report")["type"], "report")
        self.assertEqual(classify_telegram_report_text("hishab dao")["type"], "report")
        self.assertEqual(classify_telegram_report_text("Today's Report")["type"], "report")
        self.assertEqual(classify_telegram_report_text("Yesterday's Report")["type"], "report")
        self.assertEqual(classify_telegram_report_text("Date Format")["type"], "date_help")
        self.assertEqual(classify_telegram_report_text("/start")["type"], "help")

    def test_poll_telegram_report_bot_replies_to_report_command(self):
        sent_payloads = []

        def fake_telegram_request(bot_token, method, payload=None):
            if method == "getUpdates":
                return {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 10,
                            "message": {
                                "chat": {"id": 123, "first_name": "Owner", "type": "private"},
                                "text": "/report",
                            },
                        }
                    ],
                }
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": 77}}
            return {"ok": True, "result": []}

        with TemporaryDirectory() as temp_dir:
            state_file = f"{temp_dir}/telegram_state.json"
            with self.settings(
                TELEGRAM_BOT_TOKEN="test-token",
                TELEGRAM_CHAT_ID="123",
                TELEGRAM_REPORT_STATE_FILE=state_file,
            ):
                with patch("sales.daily_report._telegram_request", side_effect=fake_telegram_request):
                    result = poll_telegram_report_bot()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["replied"], 1)
        self.assertEqual(sent_payloads[0]["chat_id"], 123)
        self.assertEqual(sent_payloads[0]["parse_mode"], "HTML")
        self.assertIn("reply_markup", sent_payloads[0])
        self.assertIn("Daily Summary", sent_payloads[0]["text"])

    def test_poll_telegram_report_bot_reports_wrong_chat(self):
        def fake_telegram_request(bot_token, method, payload=None):
            if method == "getUpdates":
                return {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 11,
                            "message": {
                                "chat": {"id": 999, "first_name": "Other", "type": "private"},
                                "text": "/report",
                            },
                        }
                    ],
                }
            return {"ok": True, "result": {"message_id": 77}}

        with TemporaryDirectory() as temp_dir:
            state_file = f"{temp_dir}/telegram_state.json"
            with self.settings(
                TELEGRAM_BOT_TOKEN="test-token",
                TELEGRAM_CHAT_ID="123",
                TELEGRAM_REPORT_STATE_FILE=state_file,
            ):
                with patch("sales.daily_report._telegram_request", side_effect=fake_telegram_request):
                    result = poll_telegram_report_bot()

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["replied"], 0)
        self.assertEqual(result["ignored_wrong_chat"], 1)
        self.assertEqual(result["last_seen_chat_id"], "999")
