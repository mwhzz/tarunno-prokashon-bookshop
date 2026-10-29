from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CashTransaction
from books.models import Book
from stock.models import StockSummary
from .models import Customer, Payment, Sale


class SaleReturnTests(TestCase):
    """১০ কপি × ৳১০০, ২০% কমিশন = বিল ৳৮০০; এক কপির আসল দাম ৳৮০"""

    def setUp(self):
        self.client = APIClient()
        self.book = Book.objects.create(title='Return book', mrp='100', selling_price='100', purchase_price='60')
        self.stock = StockSummary.objects.create(book=self.book, shop_quantity=10)

    def sell(self, paid):
        response = self.client.post('/api/sales/', {
            'request_id': str(uuid4()), 'paid_amount': paid, 'payment_method': 'cash' if Decimal(paid) else 'credit',
            'customer_name': 'Shop', 'customer_phone': '01700000000',
            'items': [{'book': self.book.pk, 'quantity': 10, 'unit_price': '100.00', 'discount': '200.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['id']

    def return_books(self, sale_id, qty):
        return self.client.post(f'/api/sales/{sale_id}/return_items/', {
            'items': [{'book_id': self.book.pk, 'quantity': qty}], 'reason': '৬ মাস পরে ফেরত',
        }, format='json')

    def customer_due(self):
        return Customer.objects.get(phone='01700000000').total_due

    def test_half_return_on_unpaid_bill_only_reduces_due(self):
        sale_id = self.sell('0.00')
        response = self.return_books(sale_id, 5)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['due_reduced'], Decimal('400.00'))
        self.assertEqual(response.data['refund'], Decimal('0'))
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual((sale.total, sale.due_amount, sale.total_cost), (Decimal('400.00'), Decimal('400.00'), Decimal('300.00')))
        self.assertEqual(self.customer_due(), Decimal('400.00'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 5)
        self.assertFalse(CashTransaction.objects.filter(transaction_type='cash_out').exists())

    def test_full_return_on_paid_bill_refunds_cash(self):
        sale_id = self.sell('800.00')
        response = self.return_books(sale_id, 10)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['refund'], Decimal('800.00'))
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual((sale.total, sale.paid_amount, sale.due_amount), (Decimal('0'), Decimal('0'), Decimal('0')))
        self.assertFalse(sale.items.exists())
        self.assertEqual(Payment.objects.get(amount__lt=0).amount, Decimal('-800.00'))
        self.assertEqual(CashTransaction.objects.get(transaction_type='cash_out').amount, Decimal('800.00'))
        self.assertEqual(self.customer_due(), Decimal('0'))

    def test_partly_paid_bill_reduces_due_first_then_refunds_rest(self):
        sale_id = self.sell('600.00')  # বাকি ২০০
        response = self.return_books(sale_id, 5)  # ফেরত ৪০০
        self.assertEqual(response.data['due_reduced'], Decimal('200.00'))
        self.assertEqual(response.data['refund'], Decimal('200.00'))
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual((sale.total, sale.paid_amount, sale.due_amount), (Decimal('400.00'), Decimal('400.00'), Decimal('0')))
        self.assertEqual(self.customer_due(), Decimal('0'))
        self.assertEqual(self.client.post(f'/api/sales/{sale_id}/remove_payment/', {
            'payment_id': Payment.objects.get(amount__lt=0).pk}, format='json').status_code, 400)

    def test_cannot_return_more_than_left_on_memo(self):
        sale_id = self.sell('0.00')
        self.assertEqual(self.return_books(sale_id, 6).status_code, 200)
        self.assertEqual(self.return_books(sale_id, 5).status_code, 400)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.shop_quantity, 6)
