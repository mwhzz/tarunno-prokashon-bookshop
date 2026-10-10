from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CashTransaction, DailyCash
from .models import Customer, DuePaymentSubmission, Payment, Sale


class DuePaymentRetryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(name='Buyer', phone='01700000001', total_due=1000)
        self.sale = Sale.objects.create(customer=self.customer, customer_name='Buyer', subtotal=1000, total=1000)
        self.path = f'/api/sales/{self.sale.pk}/add_payment/'
        self.payload = {'request_id': str(uuid4()), 'amount': '100.00', 'discount': '20.00', 'method': 'cash'}

    def post(self, payload=None, path=None):
        return self.client.post(path or self.path, payload or self.payload, format='json')

    def test_same_partial_payment_retries_once_even_with_more_due_remaining(self):
        first = self.post(); second = self.post()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.data, first.data)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(DuePaymentSubmission.objects.count(), 1)
        self.sale.refresh_from_db(); self.customer.refresh_from_db()
        self.assertEqual(self.sale.paid_amount, 100)
        self.assertEqual(self.sale.total, 980)
        self.assertEqual(self.sale.due_amount, 880)
        self.assertEqual(self.customer.total_due, 880)
        self.assertEqual(DailyCash.get_for_today().closing_balance, 100)

    def test_completed_bill_replay_returns_success_without_collecting_again(self):
        payload = {**self.payload, 'amount': '980.00'}
        first = self.post(payload); second = self.post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.data, first.data)
        self.assertEqual(second.data['due_amount'], '0.00')
        self.assertEqual(Payment.objects.count(), 1)

    def test_same_request_id_cannot_change_amount_or_target(self):
        self.post()
        result = self.post({**self.payload, 'amount': '200.00'})
        self.assertEqual(result.status_code, 409)
        other = Sale.objects.create(customer=self.customer, subtotal=1000, total=1000)
        self.assertEqual(self.post(path=f'/api/sales/{other.pk}/add_payment/').status_code, 409)
        self.assertEqual(Payment.objects.count(), 1)

    def test_separate_legitimate_partial_payments_are_allowed(self):
        self.post()
        result = self.post({**self.payload, 'request_id': str(uuid4())})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(CashTransaction.objects.count(), 2)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.total_due, 760)

    def test_invalid_money_or_method_never_changes_cash_or_due(self):
        for changes in [{'amount': '-1'}, {'amount': 'NaN'}, {'amount': 'Infinity'},
                        {'amount': '0.001'}, {'amount': '1001'}, {'discount': '-1'},
                        {'method': 'credit'}, {'amount': '0', 'discount': '0'}]:
            with self.subTest(changes=changes):
                result = self.post({**self.payload, **changes})
                self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashTransaction.objects.exists())
        self.assertFalse(DuePaymentSubmission.objects.exists())
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.total_due, 1000)

    def test_non_cash_and_discount_only_never_add_cash(self):
        self.post({**self.payload, 'method': 'bkash'})
        self.post({**self.payload, 'request_id': str(uuid4()), 'amount': '0', 'discount': '10'})
        self.assertEqual(Payment.objects.count(), 2)
        self.assertFalse(CashTransaction.objects.exists())

    def test_removed_payment_replay_cannot_recreate_it(self):
        self.post()
        payment = Payment.objects.get()
        self.client.post(f'/api/sales/{self.sale.pk}/remove_payment/', {'payment_id': payment.pk}, format='json')
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashTransaction.objects.exists())
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.total_due, 1000)

    def test_customer_opening_collection_cannot_collect_the_invoice_portion(self):
        result = self.post(path=f'/api/sales/customers/{self.customer.pk}/collect_other_due/')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(CashTransaction.objects.exists())
        self.assertFalse(DuePaymentSubmission.objects.exists())

    def test_opening_due_retry_only_collects_once_and_leaves_invoice_untouched(self):
        self.customer.total_due = 1120
        self.customer.save()
        path = f'/api/sales/customers/{self.customer.pk}/collect_other_due/'
        first = self.post(path=path); second = self.post(path=path)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(CashTransaction.objects.get().amount, 100)
        self.customer.refresh_from_db(); self.sale.refresh_from_db()
        self.assertEqual(self.customer.total_due, 1000)
        self.assertEqual(self.sale.due_amount, 1000)
        self.assertFalse(Payment.objects.exists())

    def test_request_id_cannot_be_reused_for_customer_opening_due(self):
        self.post()
        self.customer.refresh_from_db()
        self.customer.total_due += 500
        self.customer.save()
        result = self.post(path=f'/api/sales/customers/{self.customer.pk}/collect_other_due/')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(CashTransaction.objects.count(), 1)

    def test_fractional_partial_payments_remain_exact_until_the_last_paisa(self):
        self.sale.subtotal = self.sale.total = Decimal('0.30')
        self.sale.save()
        self.customer.total_due = Decimal('0.30')
        self.customer.save()
        for _ in range(3):
            result = self.post({'request_id': str(uuid4()), 'amount': '0.10', 'discount': '0.00', 'method': 'cash'})
            self.assertEqual(result.status_code, 200, result.data)
        self.sale.refresh_from_db(); self.customer.refresh_from_db()
        self.assertEqual(self.sale.paid_amount, Decimal('0.30'))
        self.assertEqual(self.sale.due_amount, 0)
        self.assertEqual(self.customer.total_due, 0)

    def test_a_second_stale_form_cannot_collect_the_same_partial_payment_again(self):
        first = self.post({**self.payload, 'expected_due': '1000.00'})
        self.assertEqual(first.status_code, 200)
        stale = self.post({**self.payload, 'request_id': str(uuid4()), 'expected_due': '1000.00'})
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashTransaction.objects.count(), 1)
        replay = self.post({**self.payload, 'expected_due': '1000.00'})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(Payment.objects.count(), 1)
