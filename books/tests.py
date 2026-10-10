from django.test import TestCase
from rest_framework.test import APIClient

from .models import Book


class BookCreationTests(TestCase):
    def test_multipart_book_is_active_and_listed_by_default(self):
        client = APIClient()
        response = client.post('/api/books/', {
            'title': 'New book', 'selling_price': '100',
            'isbn': '', 'product_code': '',
        }, format='multipart')

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['is_active'])
        book = Book.objects.get(pk=response.data['id'])
        self.assertTrue(book.is_active)
        listing = client.get('/api/books/')
        self.assertIn(book.id, [item['id'] for item in listing.data['results']])

    def test_explicit_inactive_book_is_preserved(self):
        response = APIClient().post('/api/books/', {
            'title': 'Inactive book', 'is_active': 'false',
        }, format='multipart')

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data['is_active'])

    def test_partial_update_preserves_inactive_status(self):
        book = Book.objects.create(title='Inactive book', is_active=False)
        response = APIClient().patch(
            f'/api/books/{book.id}/', {'title': 'Updated title'}, format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        book.refresh_from_db()
        self.assertFalse(book.is_active)
