from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CashTransaction, DailyCash, MoneyMovement
from books.models import Book
from expenses.models import Expense, ExpenseCategory
from purchase.models import PurchaseBill, Vendor, VendorPayment
from sales.models import Payment
from stock.models import StockSummary


class MoneyAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.user.profile.role = 'admin'
        self.user.profile.save()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.book = Book.objects.create(title='Money book', mrp='500', selling_price='500', purchase_price='300')
        StockSummary.objects.create(book=self.book, shop_quantity=50)

    def balances(self):
        response = self.client.get('/api/accounts/money/')
        self.assertEqual(response.status_code, 200, response.data)
        return {row['account']: row['balance'] for row in response.data['accounts']}

    def sell(self, method, paid='500.00'):
        response = self.client.post('/api/sales/', {
            'request_id': str(uuid4()), 'paid_amount': paid, 'payment_method': method,
            'items': [{'book': self.book.pk, 'quantity': 1, 'unit_price': '500.00', 'discount': '0'}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def adjust(self, account, actual, **extra):
        return self.client.post('/api/accounts/money/adjust/', {
            'request_id': str(uuid4()), 'account': account, 'actual_balance': actual, **extra,
        }, format='json')

    def transfer(self, src, dst, amount, fee='0', request_id=None):
        return self.client.post('/api/accounts/money/transfer/', {
            'request_id': request_id or str(uuid4()), 'from_account': src, 'to_account': dst,
            'amount': amount, 'fee': fee, 'note': 'test',
        }, format='json')

    def test_sales_land_in_the_account_they_were_paid_to(self):
        self.sell('cash')
        self.sell('bkash')
        self.sell('nagad')
        self.sell('bank')
        b = self.balances()
        self.assertEqual((b['cash'], b['bkash'], b['nagad'], b['bank']), (Decimal('500.00'),) * 4)
        self.assertNotIn('mobile', b)  # পুরনো মোবাইল ব্যাংকিং খালি থাকলে দেখায় না
        self.assertEqual(CashTransaction.objects.count(), 1)  # শুধু ক্যাশ বিক্রয় ক্যাশ লেজারে

    def test_vendor_payment_and_expense_reduce_their_account(self):
        self.sell('bkash')
        vendor = Vendor.objects.create(name='V')
        bill = PurchaseBill.objects.create(vendor=vendor, vendor_name='V', subtotal=1000, total=1000)
        VendorPayment.objects.create(bill=bill, amount=Decimal('120.00'), method='bkash')
        category = ExpenseCategory.objects.create(name='Rent')
        response = self.client.post('/api/expenses/records/', {'category': category.pk, 'amount': '80', 'account': 'bkash', 'note': 'x'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.balances()['bkash'], Decimal('300.00'))
        self.assertFalse(CashTransaction.objects.filter(transaction_type='expense').exists())
        # বিকাশে যা আছে তার বেশি খরচ করা যায় না
        response = self.client.post('/api/expenses/records/', {'category': category.pk, 'amount': '301', 'account': 'bkash'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_cash_out_from_bkash_with_fee_moves_money_and_books_the_fee_as_expense(self):
        self.sell('bkash')
        response = self.transfer('bkash', 'cash', '400.00', fee='7.40')
        self.assertEqual(response.status_code, 201, response.data)
        b = self.balances()
        self.assertEqual(b['bkash'], Decimal('92.60'))
        self.assertEqual(b['cash'], Decimal('400.00'))
        fee = Expense.objects.get()
        self.assertEqual((fee.amount, fee.account, fee.category.name), (Decimal('7.40'), 'bkash', 'লেনদেন চার্জ'))
        cash_in = CashTransaction.objects.get(transaction_type='cash_in')
        self.assertEqual(cash_in.reference_id, f"money_{MoneyMovement.objects.get().pk}")

    def test_cash_to_bank_deposit_leaves_cash_ledger(self):
        self.sell('cash')
        self.assertEqual(self.transfer('cash', 'bank', '300').status_code, 201)
        b = self.balances()
        self.assertEqual((b['cash'], b['bank']), (Decimal('200.00'), Decimal('300.00')))
        self.assertEqual(DailyCash.get_for_today().closing_balance, Decimal('200.00'))

    def test_transfer_cannot_overdraw_and_same_account_is_rejected(self):
        self.sell('nagad')
        self.assertEqual(self.transfer('nagad', 'bank', '500.01').status_code, 400)
        self.assertEqual(self.transfer('nagad', 'bank', '495', fee='10').status_code, 400)
        self.assertEqual(self.transfer('nagad', 'nagad', '1').status_code, 400)
        self.assertEqual(self.transfer('nagad', 'mobile', '1').status_code, 400)  # পুরনো অ্যাকাউন্টে টাকা ঢোকানো যায় না
        self.assertFalse(MoneyMovement.objects.exists())

    def test_double_submit_with_same_request_id_moves_money_once(self):
        self.sell('bkash')
        request_id = str(uuid4())
        self.assertEqual(self.transfer('bkash', 'bank', '100', request_id=request_id).status_code, 201)
        self.assertEqual(self.transfer('bkash', 'bank', '100', request_id=request_id).status_code, 201)
        self.assertEqual(MoneyMovement.objects.count(), 1)
        self.assertEqual(self.balances()['bank'], Decimal('100.00'))

    def test_adjust_sets_the_real_balance_up_or_down(self):
        self.assertEqual(self.adjust('bank', '25000', note='ব্যাংক স্টেটমেন্ট').status_code, 201)
        self.assertEqual(self.balances()['bank'], Decimal('25000.00'))
        self.assertEqual(self.adjust('bank', '24990').status_code, 201)
        self.assertEqual(self.balances()['bank'], Decimal('24990.00'))
        self.assertEqual(self.adjust('bank', '24990').status_code, 400)  # কিছুই বদলায়নি
        self.assertEqual(self.adjust('cash', '1000').status_code, 201)
        self.assertEqual(DailyCash.get_for_today().closing_balance, Decimal('1000.00'))
        self.assertEqual(CashTransaction.objects.get().transaction_type, 'adjustment_in')

    def test_cash_moves_are_blocked_after_day_close_and_their_ledger_rows_are_locked(self):
        self.adjust('cash', '1000')
        row = CashTransaction.objects.get()
        self.assertEqual(self.client.delete(f'/api/accounts/transactions/{row.pk}/').status_code, 400)
        day = DailyCash.get_for_today()
        day.is_closed = True
        day.save()
        self.adjust('bkash', '100')
        self.assertEqual(self.transfer('bkash', 'cash', '50').status_code, 400)
        self.assertEqual(self.transfer('bkash', 'bank', '50').status_code, 201)

    def test_legacy_mobile_money_shows_until_moved_out(self):
        self.sell('mobile')
        self.assertEqual(self.balances()['mobile'], Decimal('500.00'))
        self.assertEqual(self.transfer('mobile', 'bkash', '500').status_code, 201)
        b = self.balances()
        self.assertNotIn('mobile', b)
        self.assertEqual(b['bkash'], Decimal('500.00'))

    def test_return_refund_by_bkash_comes_out_of_bkash(self):
        sale = self.sell('bkash')
        response = self.client.post(f"/api/sales/{sale['id']}/return_items/", {
            'items': [{'book_id': self.book.pk, 'quantity': 1}], 'refund_method': 'bkash',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.balances()['bkash'], Decimal('0'))
        self.assertEqual(Payment.objects.filter(method='bkash').count(), 2)

    def test_statement_shows_running_balance(self):
        self.sell('bkash')
        self.transfer('bkash', 'cash', '200')
        response = self.client.get('/api/accounts/money/statement/bkash/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['rows']
        self.assertEqual([r['amount'] for r in rows], [Decimal('-200.00'), Decimal('500.00')])
        self.assertEqual([r['balance_after'] for r in rows], [Decimal('300.00'), Decimal('500.00')])

    def test_changed_pages_render_for_admin(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        for url in ['/accounts/money/', '/accounts/', '/pos/', '/expenses/', '/sales/', '/purchase/']:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)
        self.assertContains(client.get('/accounts/money/'), 'টাকার হিসাব')

    def test_staff_cannot_see_money(self):
        staff = User.objects.create_user(username='staff', password='pw')
        client = APIClient()
        client.force_authenticate(user=staff)
        self.assertEqual(client.get('/api/accounts/money/').status_code, 403)
