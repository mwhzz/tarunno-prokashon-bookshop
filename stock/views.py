from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, F, Q, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce
from books.models import Book
from .models import StockEntry, StockSummary, StockTransfer
from .serializers import StockEntrySerializer, StockSummarySerializer, StockTransferSerializer, StockCatalogSerializer


def stock_catalog_queryset():
    return Book.objects.annotate(
        inventory_shop=Coalesce('stock_summary__shop_quantity', Value(0)),
        inventory_godown=Coalesce('stock_summary__godown_quantity', Value(0)),
    ).annotate(
        inventory_total=F('inventory_shop') + F('inventory_godown'),
    ).annotate(
        inventory_cost_value=ExpressionWrapper(
            F('inventory_total') * F('purchase_price'),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        ),
        inventory_value=ExpressionWrapper(
            F('inventory_total') * F('selling_price'),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        ),
    ).order_by('title', 'pk')


class StockEntryViewSet(viewsets.ModelViewSet):
    queryset = StockEntry.objects.select_related('book')
    serializer_class = StockEntrySerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['book__title']
    http_method_names = ['get', 'post', 'head', 'options']  # no update/delete

    def get_queryset(self):
        qs = super().get_queryset()
        book_id = self.request.query_params.get('book')
        if book_id:
            qs = qs.filter(book_id=book_id)
        source = self.request.query_params.get('source')
        if source:
            qs = qs.filter(source=source)
        return qs


class StockSummaryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockSummary.objects.select_related('book__group').order_by('book__title')
    serializer_class = StockSummarySerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['book__title', 'book__author']

    @action(detail=False, methods=['get'])
    def catalog(self, request):
        qs = stock_catalog_queryset()
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(author__icontains=search)
                | Q(product_code__icontains=search) | Q(isbn__icontains=search)
            )
        source = request.query_params.get('book_source')
        if source:
            qs = qs.filter(source=source)
        if request.query_params.get('available') == 'true':
            qs = qs.filter(inventory_total__gt=0)
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(StockCatalogSerializer(page, many=True).data)
        return Response(StockCatalogSerializer(qs, many=True).data)

    def get_queryset(self):
        qs = super().get_queryset()
        # শুধু positive stock
        only_available = self.request.query_params.get('available')
        if only_available == 'true':
            qs = qs.annotate(
                total_stock=F('godown_quantity') + F('shop_quantity')
            ).filter(total_stock__gt=0)
        book_source = self.request.query_params.get('book_source')
        if book_source:
            qs = qs.filter(book__source=book_source)
        return qs

    @action(detail=False, methods=['get'])
    def report(self, request):
        """স্টকের সারাংশ রিপোর্ট"""
        catalog = stock_catalog_queryset()
        total_books = catalog.count()
        total_qty = catalog.aggregate(
            godown=Sum('inventory_godown'),
            shop=Sum('inventory_shop')
        )
        total_godown = total_qty['godown'] or 0
        total_shop = total_qty['shop'] or 0
        total_combined = total_godown + total_shop
        
        total_value = catalog.aggregate(total=Sum('inventory_value'))['total'] or 0
        cost_value = catalog.aggregate(total=Sum('inventory_cost_value'))['total'] or 0
        out_of_stock = catalog.filter(inventory_total__lte=0).count()

        return Response({
            'total_book_types': total_books,
            'total_quantity': total_combined,
            'total_godown_quantity': total_godown,
            'total_shop_quantity': total_shop,
            'total_stock_value': total_value,
            'retail_stock_value': total_value,
            'cost_stock_value': cost_value,
            'missing_cost_count': catalog.filter(inventory_total__gt=0, purchase_price__lte=0).count(),
            'out_of_stock_count': out_of_stock,
        })

class StockTransferViewSet(viewsets.ModelViewSet):
    queryset = StockTransfer.objects.select_related('book')
    serializer_class = StockTransferSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['book__title']
    http_method_names = ['get', 'post', 'head', 'options']
