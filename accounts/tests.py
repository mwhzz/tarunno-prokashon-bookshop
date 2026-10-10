from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CashTransaction, DailyCash


class DailyCashCloseDayTests(TestCase):
    def test_close_day_sends_telegram_report_when_enabled(self):
        user = User.objects.create_user(username="admin", password="password")
        user.profile.role = "admin"
        user.profile.save()
        daily_cash = DailyCash.objects.create(opening_balance=Decimal("100.00"))
        client = APIClient()
        client.force_authenticate(user=user)

        with self.settings(TELEGRAM_REPORT_ENABLED=True):
            with patch("sales.daily_report.send_telegram_owner_report") as send_report:
                send_report.return_value = {"ok": True, "result": {"message_id": 1}}
                response = client.post(f"/api/accounts/daily/{daily_cash.id}/close_day/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["telegram_report"]["sent"])
        daily_cash.refresh_from_db()
        self.assertTrue(daily_cash.is_closed)
        send_report.assert_called_once()


class CashCarryTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        first = timezone.localdate() - timedelta(days=3)
        self.days = [DailyCash.objects.create(
            date=first + timedelta(days=i), opening_balance=100, closing_balance=100,
        ) for i in range(3)]

    def test_historical_cash_save_and_delete_reach_later_openings(self):
        entry = CashTransaction.objects.create(daily_cash=self.days[0], transaction_type='cash_in', amount=25)
        self.days[2].refresh_from_db()
        self.assertEqual(self.days[2].opening_balance, 125)
        entry.amount = Decimal('10')
        entry.save()
        self.days[2].refresh_from_db()
        self.assertEqual(self.days[2].closing_balance, 110)
        entry.delete()
        self.days[2].refresh_from_db()
        self.assertEqual(self.days[2].opening_balance, 100)

    def test_independent_opening_is_preserved(self):
        self.days[1].opening_balance = self.days[1].closing_balance = Decimal('999')
        self.days[1].save()
        CashTransaction.objects.create(daily_cash=self.days[0], transaction_type='cash_in', amount=25)
        self.days[1].refresh_from_db()
        self.assertEqual(self.days[1].opening_balance, 999)

    def test_stale_related_object_cannot_overwrite_corrected_opening(self):
        self.days[0].opening_balance = Decimal('150')
        self.days[0].save(update_fields=['opening_balance'])
        self.days[0].update_closing_balance()
        CashTransaction.objects.create(daily_cash=self.days[1], transaction_type='cash_in', amount=10)
        self.days[2].refresh_from_db()
        self.assertEqual(self.days[2].opening_balance, 160)

    def test_new_day_has_carried_opening_and_closing_without_a_transaction(self):
        today = DailyCash.get_for_today()
        self.assertEqual(today.opening_balance, 100)
        self.assertEqual(today.closing_balance, 100)

    def test_today_uses_the_dhaka_calendar_date(self):
        from datetime import datetime, timezone as tz
        with patch('django.utils.timezone.now', return_value=datetime(2026, 10, 9, 20, tzinfo=tz.utc)):
            self.assertEqual(str(DailyCash.get_for_today().date), '2026-10-10')

    def test_real_expenses_are_not_hidden_by_clamping_a_negative_balance(self):
        CashTransaction.objects.create(daily_cash=self.days[0], transaction_type='purchase', amount=125)
        self.days[2].refresh_from_db()
        self.assertEqual(self.days[2].opening_balance, -25)


class LegacyOffsetReconciliationTests(TestCase):
    def setUp(self):
        import importlib
        from datetime import timedelta
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        self.repair = importlib.import_module('accounts.migrations.0003_reconcile_duplicate_payment_offsets').reconcile_offsets
        self.apps, self.schema = apps, SimpleNamespace(connection=connection)
        self.day = DailyCash.objects.create(date='2026-08-04', opening_balance=100, closing_balance=100)
        self.carry = DailyCash.objects.create(date='2026-08-05', opening_balance=100, closing_balance=100)
        # The real receipt and compensating withdrawal cancel each other, after
        # migration 0002 already removed the second receipt.
        CashTransaction.objects.bulk_create([
            CashTransaction(id=55, daily_cash=self.day, transaction_type='sale', amount=5600, reference_id='pay_23'),
            CashTransaction(id=57, daily_cash=self.day, transaction_type='adjustment_out', amount=5600,
                            note='software error er karone 2 bar payment dekhaise'),
        ])

    def test_repair_keeps_original_withdrawal_and_reverses_once_with_carried_balances(self):
        self.repair(self.apps, self.schema)
        self.repair(self.apps, self.schema)
        self.assertEqual(CashTransaction.objects.count(), 3)
        original = CashTransaction.objects.get(pk=57)
        self.assertEqual(original.amount, 5600)
        reversal = CashTransaction.objects.get(reference_id='cash_offset_repair_20261010_57')
        self.assertEqual(reversal.amount, original.amount)
        self.assertEqual(reversal.daily_cash_id, original.daily_cash_id)
        self.carry.refresh_from_db()
        self.assertEqual(self.carry.opening_balance, 5700)

    def test_changed_source_or_missing_receipt_is_not_automatically_reversed(self):
        CashTransaction.objects.filter(pk=57).update(note='actual withdrawal')
        self.repair(self.apps, self.schema)
        self.assertEqual(CashTransaction.objects.count(), 2)
        CashTransaction.objects.filter(pk=57).update(note='software error er karone 2 bar payment dekhaise')
        CashTransaction.objects.filter(pk=55).update(reference_id='unrelated')
        self.repair(self.apps, self.schema)
        self.assertEqual(CashTransaction.objects.count(), 2)

    def test_repair_waits_until_legacy_duplicate_has_been_removed(self):
        CashTransaction.objects.bulk_create([
            CashTransaction(daily_cash=self.day, transaction_type='sale', amount=5600, reference_id='sale_payment_23'),
        ])
        self.repair(self.apps, self.schema)
        self.assertFalse(CashTransaction.objects.filter(reference_id__startswith='cash_offset_repair_').exists())

    def test_api_preserves_both_sides_of_the_reconciliation_trail(self):
        self.repair(self.apps, self.schema)
        user = User.objects.create_user(username='cash-admin', password='test-password')
        user.profile.role = 'admin'
        user.profile.save()
        client = APIClient()
        client.force_authenticate(user=user)
        reversal = CashTransaction.objects.get(reference_id='cash_offset_repair_20261010_57')
        for pk in [57, reversal.pk]:
            self.assertEqual(client.patch(f'/api/accounts/transactions/{pk}/', {'amount': '1'}, format='json').status_code, 400)
            self.assertEqual(client.delete(f'/api/accounts/transactions/{pk}/').status_code, 400)
        self.assertEqual(CashTransaction.objects.count(), 3)
