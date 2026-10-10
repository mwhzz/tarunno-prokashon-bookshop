from django.test import TestCase
from rest_framework.test import APIClient
from books.models import Book
from .models import StockSummary


class StockCatalogTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.stocked = Book.objects.create(title='Stocked', selling_price='10.00')
        self.fresh = Book.objects.create(
            title='Fresh book', selling_price='20.00', product_code='NEW-101',
            isbn='9789849990895',
        )
        self.inactive = Book.objects.create(title='Inactive', is_active=False)
        self.summary = StockSummary.objects.create(
            book=self.stocked, shop_quantity=3, godown_quantity=2,
        )

    def test_catalog_includes_new_and_inactive_books_without_creating_stock(self):
        response = self.client.get('/api/stock/summary/catalog/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)
        rows = {r['book']: r for r in response.data['results']}
        for book in [self.fresh, self.inactive]:
            row = rows[book.pk]
            self.assertEqual(row['shop_quantity'], 0)
            self.assertEqual(row['godown_quantity'], 0)
            self.assertEqual(row['quantity'], 0)
            self.assertEqual(row['stock_value'], '0.00')
        self.assertFalse(rows[self.inactive.pk]['is_active'])
        self.assertEqual(rows[self.stocked.pk]['quantity'], 5)
        self.assertEqual(rows[self.stocked.pk]['stock_value'], '50.00')
        self.assertEqual(StockSummary.objects.count(), 1)

    def test_report_counts_books_without_stock_and_preserves_stock_totals(self):
        data = self.client.get('/api/stock/summary/report/').data
        self.assertEqual(data['total_book_types'], 3)
        self.assertEqual(data['out_of_stock_count'], 2)
        self.assertEqual(data['total_shop_quantity'], 3)
        self.assertEqual(data['total_godown_quantity'], 2)
        self.assertEqual(data['total_quantity'], 5)
        self.assertEqual(data['total_stock_value'], 50)

    def test_all_pages_include_each_book_once(self):
        first = self.client.get('/api/stock/summary/catalog/?page_size=2').data
        self.assertIsNotNone(first['next'])
        second = self.client.get('/api/stock/summary/catalog/?page_size=2&page=2').data
        ids = [r['book'] for r in first['results'] + second['results']]
        self.assertCountEqual(ids, [self.stocked.pk, self.fresh.pk, self.inactive.pk])
        self.assertIsNone(second['next'])

    def test_search_matches_isbn_and_product_code(self):
        for search in ['9789849990895', 'NEW-101']:
            response = self.client.get('/api/stock/summary/catalog/', {'search': search})
            self.assertEqual([r['book'] for r in response.data['results']], [self.fresh.pk])

    def test_available_filter_does_not_include_zero_stock(self):
        response = self.client.get('/api/stock/summary/catalog/?available=true')
        self.assertEqual([r['book'] for r in response.data['results']], [self.stocked.pk])

    def test_empty_report_and_existing_summary_endpoint(self):
        data = self.client.get('/api/stock/summary/').data
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['id'], self.summary.pk)
        Book.objects.all().delete()
        report = self.client.get('/api/stock/summary/report/').data
        self.assertEqual(report['total_book_types'], 0)
        self.assertEqual(report['total_quantity'], 0)
        self.assertEqual(report['total_stock_value'], 0)
