import csv
import io
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.http import HttpResponse
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Book, Group
from .serializers import BookListSerializer, BookSerializer, GroupSerializer
from stock.models import StockEntry, StockSummary


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']


class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.select_related('group')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'author', 'publisher', 'isbn']
    ordering_fields = ['title', 'selling_price', 'created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return BookListSerializer
        return BookSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        group = self.request.query_params.get('group')
        if group:
            qs = qs.filter(group_id=group)
        active = self.request.query_params.get('active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() == 'true')
        return qs

    @action(detail=False, methods=['get'])
    def out_of_stock(self, request):
        books = Book.objects.filter(stock_summary__godown_quantity__lte=0, stock_summary__shop_quantity__lte=0)
        serializer = BookListSerializer(books.select_related('group'), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        threshold = int(request.query_params.get('threshold', 5))
        books = Book.objects.filter(
            stock_summary__godown_quantity__gte=0,
            stock_summary__shop_quantity__gte=0,
        )
        books = [book for book in books.select_related('group') if 0 < book.current_stock <= threshold]
        serializer = BookListSerializer(books, many=True)
        return Response(serializer.data)


CSV_HEADERS = [
    'title', 'author', 'publisher', 'isbn', 'edition',
    'mrp', 'purchase_price', 'selling_price',
    'commission', 'discount', 'discount_type',
    'group', 'product_code', 'book_type',
    'stock_quantity', 'stock_godown_quantity', 'stock_shop_quantity',
    'stock_location', 'image_url', 'notes',
]

CSV_EXAMPLE_ROW = [
    'Hazar Bochor Dhore', 'Jahir Rayhan', 'Ananya', '9789840413232', '5th',
    '300', '210', '250',
    '0', '0', 'amount',
    'Novel', 'BK-1001', 'single',
    '10', '0', '10', 'shop', 'https://example.com/cover.jpg', '',
]


class BookCSVTemplateView(APIView):
    def get(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="books_template.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(CSV_HEADERS)
        writer.writerow(CSV_EXAMPLE_ROW)
        writer.writerow([
            'Amar Chelebela', 'Humayun Ahmed', 'Anyaprokash', '', '2nd',
            '350', '245', '280', '0', '0', 'amount', 'Autobiography', '', 'single',
            '5', '5', '0', 'godown', '', '',
        ])
        return response


class BookCSVImportView(APIView):
    def post(self, request):
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return Response({'error': 'CSV file is required'}, status=400)

        if not csv_file.name.endswith('.csv'):
            return Response({'error': 'Only .csv files are allowed'}, status=400)

        try:
            text = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_file.seek(0)
                text = csv_file.read().decode('cp1252')
            except Exception:
                return Response({'error': 'Could not read file. Save it as UTF-8 CSV.'}, status=400)

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or 'title' not in reader.fieldnames:
            return Response({'error': "CSV must contain a 'title' column"}, status=400)

        created, updated, skipped, errors = [], [], [], []

        for row_num, row in enumerate(reader, start=2):
            title = row.get('title', '').strip()
            if not title:
                errors.append({'row': row_num, 'msg': 'title is empty'})
                continue

            try:
                book, was_created = self._upsert_book(row)
                self._sync_stock(book, row)
                self._save_image_if_needed(book, row.get('image_url', ''))

                payload = {'row': row_num, 'id': book.id, 'title': book.title}
                if was_created:
                    created.append(payload)
                else:
                    updated.append(payload)
            except Exception as exc:
                errors.append({'row': row_num, 'title': title, 'msg': str(exc)})

        return Response({
            'created': created,
            'updated': updated,
            'skipped': skipped,
            'errors': errors,
            'summary': {
                'total': len(created) + len(updated) + len(skipped) + len(errors),
                'created': len(created),
                'updated': len(updated),
                'skipped': len(skipped),
                'errors': len(errors),
            },
        }, status=200)

    def _upsert_book(self, row):
        title = row.get('title', '').strip()
        isbn = row.get('isbn', '').strip() or None
        product_code = row.get('product_code', '').strip() or None

        existing = Book.objects.filter(isbn=isbn).first() if isbn else None
        if not existing:
            existing = Book.objects.filter(title=title).first()

        if product_code and Book.objects.filter(product_code=product_code).exclude(
            id=existing.id if existing else 0
        ).exists():
            product_code = None

        group = None
        group_name = row.get('group', '').strip()
        if group_name:
            group, _ = Group.objects.get_or_create(name=group_name)

        book_type = row.get('book_type', 'single').strip() or 'single'
        if book_type not in ('single', 'series'):
            book_type = 'single'

        discount_type = row.get('discount_type', 'amount').strip() or 'amount'
        if discount_type not in ('amount', 'percentage'):
            discount_type = 'amount'

        data = {
            'title': title,
            'author': row.get('author', '').strip(),
            'publisher': row.get('publisher', '').strip(),
            'isbn': isbn,
            'edition': row.get('edition', '').strip(),
            'mrp': self._to_decimal(row.get('mrp', 0)),
            'purchase_price': self._to_decimal(row.get('purchase_price', 0)),
            'selling_price': self._to_decimal(row.get('selling_price', 0)),
            'commission': self._to_decimal(row.get('commission', 0)),
            'discount': self._to_decimal(row.get('discount', 0)),
            'discount_type': discount_type,
            'book_type': book_type,
            'product_code': product_code,
            'notes': row.get('notes', '').strip(),
            'group': group,
        }

        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            existing.save()
            return existing, False

        return Book.objects.create(**data), True

    def _sync_stock(self, book, row):
        godown_qty = self._to_int(row.get('stock_godown_quantity', ''))
        shop_qty = self._to_int(row.get('stock_shop_quantity', ''))

        if godown_qty is None and shop_qty is None:
            stock_qty = self._to_int(row.get('stock_quantity', ''))
            if stock_qty is None:
                return
            location = row.get('stock_location', '').strip() or 'godown'
            if location == 'shop':
                shop_qty = stock_qty
            else:
                godown_qty = stock_qty

        summary, _ = StockSummary.objects.get_or_create(
            book=book,
            defaults={'godown_quantity': 0, 'shop_quantity': 0},
        )

        for location, field_name, target_qty in (
            ('godown', 'godown_quantity', godown_qty),
            ('shop', 'shop_quantity', shop_qty),
        ):
            if target_qty is None:
                continue
            current_qty = getattr(summary, field_name)
            diff = target_qty - current_qty
            if diff:
                StockEntry.objects.create(
                    book=book,
                    quantity=diff,
                    source='adjustment',
                    location=location,
                    purchase_price=book.purchase_price,
                    note='CSV import stock sync',
                )
                setattr(summary, field_name, target_qty)

        summary.save()

    def _save_image_if_needed(self, book, image_url):
        image_url = (image_url or '').strip()
        if not image_url.startswith('http') or book.image:
            return

        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            image_data = response.read()

        ext = image_url.split('?')[0].rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            ext = 'jpg'
        book.image.save(f'book_{book.id}.{ext}', ContentFile(image_data), save=True)

    def _to_decimal(self, val, default='0'):
        try:
            return Decimal(str(val).strip()) if str(val).strip() else Decimal(default)
        except InvalidOperation:
            return Decimal(default)

    def _to_int(self, val):
        val = str(val).strip()
        if not val:
            return None
        try:
            return int(Decimal(val))
        except (InvalidOperation, ValueError):
            return None
