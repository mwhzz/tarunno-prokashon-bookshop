from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from books.models import Book
from stock.models import StockSummary
from .models import ExternalTrade


class ExternalBookToStockTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def trade(self, **extra):
        return ExternalTrade.objects.create(book_title='বাইরের বই', quantity=4, purchase_price='300', **extra)

    def test_cover_price_and_commission_come_from_body_rate_not_buying_price(self):
        trade = self.trade(body_rate='500')
        response = self.client.post(f'/api/sales/external-trades/{trade.pk}/add_to_stock/', {'selling_price': '400'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        book = Book.objects.get(pk=response.data['book_id'])
        self.assertEqual((book.mrp, book.commission, book.selling_price), (Decimal('500.00'), Decimal('20.00'), Decimal('400.00')))
        self.assertEqual(StockSummary.objects.get(book=book).shop_quantity, 4)

    def test_without_selling_price_it_refuses_instead_of_using_buying_price(self):
        trade = self.trade(body_rate='500')
        response = self.client.post(f'/api/sales/external-trades/{trade.pk}/add_to_stock/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Book.objects.exists())

    def test_without_body_rate_selling_price_is_the_cover_price(self):
        trade = self.trade()
        response = self.client.post(f'/api/sales/external-trades/{trade.pk}/add_to_stock/', {'selling_price': '450'}, format='json')
        book = Book.objects.get(pk=response.data['book_id'])
        self.assertEqual((book.mrp, book.commission), (Decimal('450.00'), Decimal('0')))


class InvoiceCommissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.book = Book.objects.create(title='চিত্রসহ তাজবিদ শিক্ষা', mrp='590', selling_price='590', purchase_price='300')
        StockSummary.objects.create(book=self.book, shop_quantity=5)

    def test_print_and_pdf_show_cover_price_total_and_commission(self):
        response = self.client.post('/api/sales/', {
            'request_id': str(uuid4()), 'paid_amount': '472.00', 'payment_method': 'cash',
            'items': [{'book': self.book.pk, 'quantity': 1, 'unit_price': '590.00', 'discount': '118.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        sale_id = response.data['id']
        html = self.client.get(f'/api/sales/{sale_id}/print_invoice/').content.decode()
        self.assertIn('কমিশন', html)
        self.assertIn('20%', html)
        self.assertIn('৳590.00', html)  # মোট গায়ের মূল্য, কমিশনের আগে
        self.assertIn('৳472.00', html)
        self.assertEqual(self.client.get(f'/api/sales/{sale_id}/download_pdf/').status_code, 200)


class BuyingPriceVisibilityTests(TestCase):
    def setUp(self):
        Book.objects.create(title='Secret cost', mrp='100', selling_price='100', purchase_price='55')

    def books_for(self, role):
        user = User.objects.create_user(username=role, password='pw')
        user.profile.role = role
        user.profile.save()
        client = APIClient()
        client.force_authenticate(user=user)
        return client.get('/api/books/').data['results'][0]

    def test_staff_never_receive_buying_price(self):
        self.assertNotIn('purchase_price', self.books_for('staff'))
        self.assertEqual(self.books_for('manager')['purchase_price'], '55.00')
