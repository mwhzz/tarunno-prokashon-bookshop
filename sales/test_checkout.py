from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import CashTransaction
from books.models import Book
from stock.models import StockEntry, StockSummary
from .models import Customer, Payment, Sale, SaleSubmission
from .serializers import SaleCreateSerializer


class CheckoutSafetyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.book = Book.objects.create(title='Checkout book', mrp='200', selling_price='160', purchase_price='110', commission='20')
        self.stock = StockSummary.objects.create(book=self.book, shop_quantity=3)
        self.payload = {
            'request_id': str(uuid4()), 'paid_amount': '160.00', 'payment_method': 'cash',
            'items': [{'book': self.book.pk, 'quantity': 1, 'unit_price': '200.00', 'discount': '40.00'}],
        }

    def post(self, payload=None):
        return self.client.post('/api/sales/', payload or self.payload, format='json')

    def test_exact_cash_creates_one_payment_and_one_stock_movement(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total'], '160.00')
        self.assertEqual(response.data['due_amount'], '0.00')
        self.assertEqual(Payment.objects.get().amount, Decimal('160.00'))
        self.assertEqual(CashTransaction.objects.get().amount, Decimal('160.00'))
        self.assertEqual(StockEntry.objects.get().quantity, -1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 2)

    def test_partial_payment_and_full_due_go_to_the_customer(self):
        for paid, method, due in [('100.00', 'cash', '60.00'), ('0.00', 'credit', '160.00')]:
            with self.subTest(method=method):
                payload = {**self.payload, 'request_id': str(uuid4()), 'paid_amount': paid, 'payment_method': method,
                           'customer_name': 'Buyer', 'customer_phone': '01700000000'}
                response = self.post(payload)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['due_amount'], due)
        self.assertEqual(Customer.objects.get().total_due, Decimal('220.00'))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashTransaction.objects.count(), 1)

    def test_mobile_and_bank_do_not_add_cash(self):
        for method in ['mobile', 'bank']:
            response = self.post({**self.payload, 'request_id': str(uuid4()), 'payment_method': method})
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertFalse(CashTransaction.objects.exists())

    def test_retry_returns_original_sale_even_when_stock_is_now_zero_and_book_inactive(self):
        self.stock.shop_quantity = 1
        self.stock.save()
        first = self.post()
        self.book.is_active = False
        self.book.save()
        second = self.post()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(StockEntry.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 0)

    def test_reusing_request_id_for_a_different_bill_is_rejected(self):
        self.assertEqual(self.post().status_code, 201)
        response = self.post({**self.payload, 'customer_name': 'Another buyer'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Sale.objects.count(), 1)

    def test_overpayment_never_creates_negative_due_or_excess_cash(self):
        response = self.post({**self.payload, 'paid_amount': '500.00'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashTransaction.objects.exists())
        self.assertFalse(SaleSubmission.objects.exists())
        self.assertEqual(self.post().status_code, 201)  # Validation failure does not consume the ID.

    def test_negative_money_duplicate_books_and_unnamed_due_are_rejected(self):
        bad_payloads = [
            {**self.payload, key: '-1.00'} for key in
            ['paid_amount', 'discount', 'packaging_charge', 'courier_charge', 'transaction_fee']
        ]
        bad_payloads += [
            {**self.payload, 'items': [{**self.payload['items'][0], 'unit_price': '-1.00'}]},
            {**self.payload, 'items': [{**self.payload['items'][0], 'discount': '-1.00'}]},
            {**self.payload, 'items': self.payload['items'] * 2},
            {**self.payload, 'paid_amount': '0', 'payment_method': 'credit'},
            {**self.payload, 'payment_method': 'credit'},
            {**self.payload, 'discount': '161.00'},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(SaleSubmission.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 3)

    def test_stock_changed_after_validation_is_rechecked_at_write_time(self):
        serializer = SaleCreateSerializer(data=self.payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.stock.shop_quantity = 0
        self.stock.save()
        with self.assertRaises(ValidationError):
            serializer.save()
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(CashTransaction.objects.exists())

    def test_failure_reserving_later_book_rolls_back_earlier_reservation(self):
        other = Book.objects.create(title='Other')
        other_stock = StockSummary.objects.create(book=other, shop_quantity=1)
        serializer = SaleCreateSerializer(data={**self.payload, 'paid_amount': '160.00', 'items': [
            *self.payload['items'], {'book': other.pk, 'quantity': 1, 'unit_price': '0', 'discount': '0'}]})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        other_stock.shop_quantity = 0
        other_stock.save()
        with self.assertRaises(ValidationError):
            serializer.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 3)
        self.assertFalse(Sale.objects.exists())

    def test_fractional_price_discount_and_fees_remain_exact(self):
        response = self.post({**self.payload, 'paid_amount': '495.28', 'transaction_fee': '0.30', 'items': [
            {'book': self.book.pk, 'quantity': 3, 'unit_price': '199.99', 'discount': '104.99'}]})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total'], '495.28')
        self.assertEqual(CashTransaction.objects.get().amount, Decimal('495.28'))
