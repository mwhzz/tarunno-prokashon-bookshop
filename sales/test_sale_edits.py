from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from rest_framework.test import APIClient

from books.models import Book
from stock.models import StockSummary
from .models import Customer, SaleEdit, SaleReturn


class SaleEditTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.book = Book.objects.create(title='Book A', mrp='200', selling_price='200', purchase_price='120')
        self.other = Book.objects.create(title='Book B', mrp='100', selling_price='100', purchase_price='60')
        self.stock = StockSummary.objects.create(book=self.book, shop_quantity=5)
        self.other_stock = StockSummary.objects.create(book=self.other, shop_quantity=5)
        response = self.client.post('/api/sales/', {
            'request_id': str(uuid4()), 'paid_amount': '100.00', 'payment_method': 'cash',
            'customer_name': 'Buyer', 'customer_phone': '01700000000',
            'items': [{'book': self.book.pk, 'quantity': 2, 'unit_price': '200.00', 'discount': '80.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.sale_id = response.data['id']
        self.base = {
            'edit_count': 0, 'customer_name': 'Buyer', 'customer_phone': '01700000000', 'reason': 'ভুল পরিমাণ',
            'items': [{'book': self.book.pk, 'quantity': 2, 'unit_price': '200.00', 'discount': '80.00'}],
        }

    def edit(self, **changes):
        return self.client.post(f'/api/sales/{self.sale_id}/edit/', {**self.base, **changes}, format='json')

    def refresh(self):
        self.stock.refresh_from_db()
        self.other_stock.refresh_from_db()
        return Customer.objects.get(phone='01700000000')

    def test_changing_items_moves_stock_due_and_cost_and_keeps_history(self):
        self.assertEqual(self.refresh().total_due, Decimal('220.00'))
        response = self.edit(items=[
            {'book': self.book.pk, 'quantity': 1, 'unit_price': '200.00', 'discount': '40.00'},
            {'book': self.other.pk, 'quantity': 3, 'unit_price': '100.00', 'discount': '0'},
        ])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], '460.00')
        self.assertEqual(response.data['due_amount'], '360.00')
        self.assertEqual(response.data['total_cost'], '300.00')
        customer = self.refresh()
        self.assertEqual(customer.total_due, Decimal('360.00'))
        self.assertEqual((self.stock.shop_quantity, self.other_stock.shop_quantity), (4, 2))
        edit = SaleEdit.objects.get()
        self.assertEqual(edit.before['total'], '320.00')
        self.assertEqual(edit.after['total'], '460.00')
        self.assertEqual(edit.reason, 'ভুল পরিমাণ')
        self.assertTrue(any('যোগ: Book B' in c for c in edit.changes))

    def test_stale_edit_and_total_below_paid_are_rejected_without_changes(self):
        self.assertEqual(self.edit(edit_count=1).status_code, 409)
        response = self.edit(items=[{'book': self.book.pk, 'quantity': 1, 'unit_price': '50.00', 'discount': '0'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.refresh().total_due, Decimal('220.00'))
        self.assertEqual(self.stock.shop_quantity, 3)
        self.assertFalse(SaleEdit.objects.exists())

    def test_not_enough_shop_stock_rolls_back(self):
        response = self.edit(items=[{'book': self.book.pk, 'quantity': 9, 'unit_price': '200.00', 'discount': '0'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.refresh().total_due, Decimal('220.00'))
        self.assertEqual(self.stock.shop_quantity, 3)

    def test_changing_phone_moves_due_to_the_new_customer(self):
        response = self.edit(customer_name='Right Buyer', customer_phone='01800000000')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.refresh().total_due, Decimal('0.00'))
        self.assertEqual(Customer.objects.get(phone='01800000000').total_due, Decimal('220.00'))

    def test_unchanged_memo_and_returned_memo_are_rejected(self):
        self.assertEqual(self.edit().status_code, 400)
        SaleReturn.objects.create(sale_id=self.sale_id, book=self.book, quantity=1)
        self.assertEqual(self.edit(note='x').status_code, 400)

    def test_history_lists_edits(self):
        self.edit(note='ঠিক করা')
        response = self.client.get(f'/api/sales/{self.sale_id}/edit_history/')
        self.assertEqual(response.data['edit_count'], 1)
        self.assertIn('নোট: — → ঠিক করা', response.data['edits'][0]['changes'])

    def test_wrong_received_amount_is_corrected_with_cash_and_due_in_step(self):
        from accounts.models import CashTransaction, DailyCash
        response = self.edit(paid_amount='150.00', payment_method='cash')  # আগে ১০০ লেখা হয়েছিল
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['paid_amount'], response.data['due_amount']), ('150.00', '170.00'))
        self.assertEqual(self.refresh().total_due, Decimal('170.00'))
        self.assertEqual(DailyCash.get_for_today().closing_balance, Decimal('150.00'))
        response = self.edit(edit_count=1, paid_amount='80.00', payment_method='cash')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.refresh().total_due, Decimal('240.00'))
        self.assertEqual(DailyCash.get_for_today().closing_balance, Decimal('80.00'))
        self.assertTrue(CashTransaction.objects.filter(transaction_type='cash_out', amount=Decimal('70.00')).exists())

    def test_received_more_than_bill_is_rejected(self):
        self.assertEqual(self.edit(paid_amount='321.00').status_code, 400)

    def test_received_by_bkash_goes_to_bkash(self):
        from sales.models import Payment
        self.assertEqual(self.edit(paid_amount='320.00', payment_method='bkash').status_code, 200)
        self.assertEqual(Payment.objects.get(method='bkash').amount, Decimal('220.00'))
        self.assertEqual(self.refresh().total_due, Decimal('0.00'))

    def test_address_and_sale_type_are_editable_and_in_history(self):
        response = self.edit(customer_address='বাংলাবাজার, ঢাকা', sale_type='wholesale')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.refresh().address, 'বাংলাবাজার, ঢাকা')
        changes = SaleEdit.objects.get().changes
        self.assertTrue(any(c.startswith('ঠিকানা') for c in changes))
        self.assertTrue(any(c.startswith('বিক্রয়ের ধরন') for c in changes))
