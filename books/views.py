import csv
import io
from decimal import Decimal, InvalidOperation

from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from django.http import HttpResponse

from .models import Book, Group
from .serializers import BookSerializer, BookListSerializer, GroupSerializer


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']


class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.select_related('group').prefetch_related('stock_summary')
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
        """স্টক শেষ হওয়া বইয়ের তালিকা"""
        books = Book.objects.filter(
            stock_summary__quantity__lte=0
        ).select_related('group')
        serializer = BookListSerializer(books, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """কম স্টক (৫ এর নিচে)"""
        threshold = int(request.query_params.get('threshold', 5))
        books = Book.objects.filter(
            stock_summary__quantity__lte=threshold,
            stock_summary__quantity__gt=0
        ).select_related('group')
        serializer = BookListSerializer(books, many=True)
        return Response(serializer.data)


CSV_HEADERS = [
    'title', 'author', 'publisher', 'isbn', 'edition',
    'mrp', 'purchase_price', 'selling_price',
    'commission', 'discount', 'discount_type',
    'group', 'product_code', 'book_type', 'notes',
]

CSV_EXAMPLE_ROW = [
    'হাজার বছর ধরে', 'জহির রায়হান', 'অনন্যা', '9789840413232', '৫ম',
    '300', '210', '250',
    '0', '0', 'amount',
    'উপন্যাস', 'BK-1001', 'single', '',
]


class BookCSVTemplateView(APIView):
    """GET → example CSV download"""
    def get(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="books_template.csv"'
        response.write('﻿')  # BOM for Excel
        writer = csv.writer(response)
        writer.writerow(CSV_HEADERS)
        writer.writerow(CSV_EXAMPLE_ROW)
        writer.writerow([
            'আমার ছেলেবেলা', 'হুমায়ূন আহমেদ', 'অন্যপ্রকাশ', '', '২য়',
            '350', '245', '280', '0', '0', 'amount', 'আত্মজীবনী', '', 'single', '',
        ])
        return response


class BookCSVImportView(APIView):
    """POST → parse CSV, create books, return result JSON"""

    def post(self, request):
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return Response({'error': 'CSV ফাইল দিন'}, status=400)

        if not csv_file.name.endswith('.csv'):
            return Response({'error': 'শুধু .csv ফাইল গ্রহণযোগ্য'}, status=400)

        try:
            text = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_file.seek(0)
                text = csv_file.read().decode('cp1252')
            except Exception:
                return Response({'error': 'ফাইল পড়া যাচ্ছে না — UTF-8 এ সেভ করুন'}, status=400)

        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames or 'title' not in reader.fieldnames:
            return Response({'error': "CSV-তে 'title' কলাম নেই"}, status=400)

        created, skipped, errors = [], [], []

        def to_dec(val, default='0'):
            try:
                return Decimal(str(val).strip()) if str(val).strip() else Decimal(default)
            except InvalidOperation:
                return Decimal(default)

        for i, row in enumerate(reader, start=2):
            title = row.get('title', '').strip()
            if not title:
                errors.append({'row': i, 'msg': 'title খালি'})
                continue

            isbn = row.get('isbn', '').strip() or None
            product_code = row.get('product_code', '').strip() or None

            # duplicate check
            if isbn and Book.objects.filter(isbn=isbn).exists():
                skipped.append({'row': i, 'title': title, 'reason': 'ISBN আগে থেকে আছে'})
                continue
            if Book.objects.filter(title=title).exists():
                skipped.append({'row': i, 'title': title, 'reason': 'একই নামের বই আছে'})
                continue

            # product_code uniqueness
            if product_code and Book.objects.filter(product_code=product_code).exists():
                product_code = None

            group = None
            group_name = row.get('group', '').strip()
            if group_name:
                group, _ = Group.objects.get_or_create(name=group_name)

            book_type = row.get('book_type', 'single').strip()
            if book_type not in ('single', 'series'):
                book_type = 'single'

            discount_type = row.get('discount_type', 'amount').strip()
            if discount_type not in ('amount', 'percentage'):
                discount_type = 'amount'

            try:
                book = Book.objects.create(
                    title=title,
                    author=row.get('author', '').strip(),
                    publisher=row.get('publisher', '').strip(),
                    isbn=isbn,
                    edition=row.get('edition', '').strip(),
                    mrp=to_dec(row.get('mrp', 0)),
                    purchase_price=to_dec(row.get('purchase_price', 0)),
                    selling_price=to_dec(row.get('selling_price', 0)),
                    commission=to_dec(row.get('commission', 0)),
                    discount=to_dec(row.get('discount', 0)),
                    discount_type=discount_type,
                    book_type=book_type,
                    product_code=product_code,
                    notes=row.get('notes', '').strip(),
                    group=group,
                )
                created.append({'row': i, 'id': book.id, 'title': book.title})
            except Exception as e:
                errors.append({'row': i, 'title': title, 'msg': str(e)})

        return Response({
            'created': created,
            'skipped': skipped,
            'errors':  errors,
            'summary': {
                'total':   len(created) + len(skipped) + len(errors),
                'created': len(created),
                'skipped': len(skipped),
                'errors':  len(errors),
            }
        }, status=200)
