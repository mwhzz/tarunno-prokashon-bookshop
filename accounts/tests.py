from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import DailyCash


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
        send_report.assert_called_once()
