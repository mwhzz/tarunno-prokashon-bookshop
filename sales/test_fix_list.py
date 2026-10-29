from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CashTransaction, DailyCash, MoneyMovement
from books.models import Book
from stock.models import StockSummary
from .models import Customer, ExternalTrade, Payment, Sale
from .reports import sales_report


def admin_client(username='owner'):
    user = User.objects.create_user(username=username, password='pw')
    user.profile.role = 'admin'
    user.profile.save()
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class ReportCountsMoneyOnTheDayItArrivedTests(TestCase):
    """বিল গতকাল, টাকা আজ এলে আদায় ও লাভ আজকের হিসাবে আসে"""

    def setUp(self):
        self.client = APIClient()
        self.book = Book.objects.create(title='B', mrp='1000', selling_price='1000', purchase_price='800')
        StockSummary.objects.create(book=self.book, shop_quantity=5)
        response = self.client.post('/api/sales/', {
            'request_id': str(uuid4()), 'paid_amount': '0', 'payment_method': 'credit',
            'customer_name': 'C', 'customer_phone': '01700000000',
            'items': [{'book': self.book.pk, 'quantity': 1, 'unit_price': '1000.00', 'discount': '0'}],
        }, format='json')
        self.sale = Sale.objects.get(pk=response.data['id'])
        self.today = timezone.localdate()
        self.yesterday = self.today - timedelta(days=1)
        Sale.objects.filter(pk=self.sale.pk).update(created_at=timezone.now() - timedelta(days=1))

    def collect(self, amount):
        return self.client.post(f'/api/sales/{self.sale.pk}/add_payment/', {
            'request_id': str(uuid4()), 'amount': amount, 'method': 'cash',
        }, format='json')

    def test_collection_and_profit_land_on_payment_day(self):
        self.assertEqual(self.collect('900').status_code, 200)
        today = sales_report(self.today, self.today)['summary']
        self.assertEqual(today['total_paid'], 900.0)
        self.assertEqual(today['realized_profit'], 100.0)  # খরচ ৮০০ উঠে বাকি ১০০ লাভ
        yesterday = sales_report(self.yesterday, self.yesterday)['summary']
        self.assertEqual((yesterday['total_paid'], yesterday['realized_profit'], yesterday['total_revenue']), (0, 0, 1000.0))

    def test_profit_is_split_across_payment_days(self):
        self.collect('500')
        Payment.objects.update(created_at=timezone.now() - timedelta(days=1))
        self.collect('500')
        self.assertEqual(sales_report(self.yesterday, self.yesterday)['summary']['realized_profit'], 0)
        self.assertEqual(sales_report(self.today, self.today)['summary']['realized_profit'], 200.0)
        self.assertEqual(sales_report(self.yesterday, self.today)['summary']['total_paid'], 1000.0)


class OutsideBookAccountTests(TestCase):
    def setUp(self):
        self.client = admin_client()

    def balances(self):
        return {r['account']: r['balance'] for r in self.client.get('/api/accounts/money/').data['accounts']}

    def test_bought_by_bkash_and_sold_into_bank_skip_cash(self):
        self.client.post('/api/accounts/money/adjust/', {'request_id': str(uuid4()), 'account': 'bkash', 'actual_balance': '1000'}, format='json')
        response = self.client.post('/api/sales/external-trades/', {
            'book_title': 'X', 'purchase_price': '300', 'quantity': 2, 'purchase_account': 'bkash',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        sold = self.client.post(f"/api/sales/external-trades/{response.data['id']}/complete_sale/", {'selling_price': '400', 'sale_account': 'bank'}, format='json')
        self.assertEqual(sold.status_code, 200, sold.data)
        b = self.balances()
        self.assertEqual((b['bkash'], b['bank'], b['cash']), (Decimal('400.00'), Decimal('800.00'), Decimal('0')))
        self.assertFalse(CashTransaction.objects.filter(reference_id__startswith='ext_').exists())

    def test_cash_trade_still_uses_cash_ledger(self):
        self.client.post('/api/accounts/money/adjust/', {'request_id': str(uuid4()), 'account': 'cash', 'actual_balance': '1000'}, format='json')
        self.client.post('/api/sales/external-trades/', {'book_title': 'X', 'purchase_price': '300', 'quantity': 1, 'already_sold': True, 'selling_price': '450'}, format='json')
        self.assertEqual(self.balances()['cash'], Decimal('1150.00'))

    def test_account_cannot_be_changed_after_creation(self):
        response = self.client.post('/api/sales/external-trades/', {'book_title': 'X', 'purchase_price': '300', 'quantity': 1}, format='json')
        self.client.patch(f"/api/sales/external-trades/{response.data['id']}/", {'purchase_account': 'bank'}, format='json')
        self.assertEqual(ExternalTrade.objects.get().purchase_account, 'cash')


class OpeningDueByBkashTests(TestCase):
    def test_opening_due_can_be_collected_into_bkash(self):
        client = admin_client()
        customer = Customer.objects.create(name='Old', phone='01800000000', total_due=Decimal('500'))
        response = client.post(f'/api/sales/customers/{customer.pk}/collect_other_due/', {
            'request_id': str(uuid4()), 'amount': '300', 'method': 'bkash',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        customer.refresh_from_db()
        self.assertEqual(customer.total_due, Decimal('200.00'))
        self.assertEqual(MoneyMovement.objects.get().to_account, 'bkash')
        self.assertFalse(CashTransaction.objects.exists())
        balances = {r['account']: r['balance'] for r in client.get('/api/accounts/money/').data['accounts']}
        self.assertEqual(balances['bkash'], Decimal('300.00'))
        today = timezone.localdate()
        self.assertEqual(sales_report(today, today)['summary']['total_paid'], 300.0)


class StaffCannotSeeBuyingPriceTests(TestCase):
    def test_book_detail_hides_cost_from_staff(self):
        book = Book.objects.create(title='Secret', mrp='100', selling_price='100', purchase_price='55')
        owner = admin_client()  # প্রথম ইউজার নিজে থেকেই অ্যাডমিন হয়, তাই স্টাফ পরে বানানো হচ্ছে
        staff = User.objects.create_user(username='staff', password='pw')
        client = APIClient()
        client.force_authenticate(user=staff)
        self.assertEqual(staff.profile.role, 'staff')
        self.assertNotIn('purchase_price', client.get(f'/api/books/{book.pk}/').data)
        self.assertEqual(owner.get(f'/api/books/{book.pk}/').data['purchase_price'], '55.00')


class TelegramLabelTests(TestCase):
    def test_bkash_and_nagad_are_named_properly(self):
        import inspect
        from . import daily_report
        source = inspect.getsource(daily_report)
        self.assertEqual(source.count('"bkash": "bKash"'), 2)
        self.assertEqual(source.count('"nagad": "Nagad (নগদ)"'), 2)
