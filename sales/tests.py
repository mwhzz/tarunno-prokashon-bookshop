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

        def fake_telegram_request(bot_token, method, payload=None, request_timeout=30):
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
        def fake_telegram_request(bot_token, method, payload=None, request_timeout=30):
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

    def test_poll_telegram_report_bot_uses_long_poll_timeout(self):
        get_updates_payloads = []

        def fake_telegram_request(bot_token, method, payload=None, request_timeout=30):
            if method == "getUpdates":
                get_updates_payloads.append((payload, request_timeout))
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 1}}

        with TemporaryDirectory() as temp_dir:
            state_file = f"{temp_dir}/telegram_state.json"
            with self.settings(
                TELEGRAM_BOT_TOKEN="test-token",
                TELEGRAM_CHAT_ID="123",
                TELEGRAM_REPORT_STATE_FILE=state_file,
            ):
                with patch("sales.daily_report._telegram_request", side_effect=fake_telegram_request):
                    poll_telegram_report_bot(telegram_timeout=25)

        self.assertEqual(get_updates_payloads[0][0]["timeout"], 25)
        self.assertGreaterEqual(get_updates_payloads[0][1], 35)


class DuePaymentCashTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        from sales.models import Sale, Customer
        self.client = APIClient()
        self.customer = Customer.objects.create(name='Due customer', phone='01711111111', total_due=1000)
        self.sale = Sale.objects.create(customer=self.customer, customer_name='Due customer', subtotal=1000, total=1000, paid_amount=0)

    def test_cash_due_collection_creates_one_ledger_entry(self):
        from sales.models import Payment
        response = self.client.post(f'/api/sales/{self.sale.pk}/add_payment/', {'amount':100, 'discount':20, 'method':'cash'}, format='json')
        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(sale=self.sale)
        self.assertEqual(CashTransaction.objects.filter(transaction_type='sale').count(), 1)
        self.assertEqual(CashTransaction.objects.get().reference_id, f'pay_{payment.pk}')
        self.sale.refresh_from_db(); self.customer.refresh_from_db()
        self.assertEqual(self.sale.paid_amount, 100)
        self.assertEqual(self.sale.due_amount, 880)
        self.assertEqual(self.customer.total_due, 880)
        self.assertEqual(DailyCash.get_for_today().closing_balance, 100)

    def test_removing_payment_also_removes_signal_cash_entry(self):
        from sales.models import Payment
        self.client.post(f'/api/sales/{self.sale.pk}/add_payment/', {'amount':100, 'method':'cash'}, format='json')
        payment = Payment.objects.get(sale=self.sale)
        result = self.client.post(f'/api/sales/{self.sale.pk}/remove_payment/', {'payment_id':payment.pk}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(CashTransaction.objects.exists())
        self.assertEqual(DailyCash.get_for_today().closing_balance, 0)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.due_amount, 1000)

    def test_non_cash_and_discount_only_do_not_increase_cash(self):
        self.client.post(f'/api/sales/{self.sale.pk}/add_payment/', {'amount':100, 'method':'bank'}, format='json')
        self.client.post(f'/api/sales/{self.sale.pk}/add_payment/', {'amount':0, 'discount':100, 'method':'cash'}, format='json')
        self.assertFalse(CashTransaction.objects.exists())

    def test_proven_legacy_duplicates_and_carried_balances_are_repaired(self):
        import importlib
        from datetime import timedelta
        from django.apps import apps
        from django.db import connection
        from sales.models import Payment
        payment = Payment.objects.create(sale=self.sale, amount=100, method='cash')
        day = DailyCash.get_for_today()
        CashTransaction.objects.create(daily_cash=day, transaction_type='sale', amount=100, reference_id=f'sale_payment_{payment.pk}')
        day.refresh_from_db()
        carried = DailyCash.objects.create(date=day.date+timedelta(days=1), opening_balance=200, closing_balance=200)
        manual = DailyCash.objects.create(date=day.date+timedelta(days=2), opening_balance=999, closing_balance=999)
        repair = importlib.import_module('accounts.migrations.0002_remove_duplicate_due_cash').repair_duplicate_cash
        from types import SimpleNamespace
        repair(apps, SimpleNamespace(connection=connection))
        self.assertEqual(CashTransaction.objects.count(), 1)
        day.refresh_from_db(); carried.refresh_from_db(); manual.refresh_from_db()
        self.assertEqual(day.closing_balance, 100)
        self.assertEqual(carried.opening_balance, 100)
        self.assertEqual(carried.closing_balance, 100)
        self.assertEqual(manual.opening_balance, 999)
