from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, F
from .models import StockEntry, StockSummary, StockTransfer
from .serializers import StockEntrySerializer, StockSummarySerializer, StockTransferSerializer


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

    def get_queryset(self):
        qs = super().get_queryset()
        # শুধু positive stock
        only_available = self.request.query_params.get('available')
        if only_available == 'true':
            qs = qs.annotate(
                total_stock=F('godown_quantity') + F('shop_quantity')
            ).filter(total_stock__gt=0)
        return qs

    @action(detail=False, methods=['get'])
    def report(self, request):
        """স্টকের সারাংশ রিপোর্ট"""
        total_books = StockSummary.objects.count()
        total_qty = StockSummary.objects.aggregate(
            godown=Sum('godown_quantity'),
            shop=Sum('shop_quantity')
        )
        total_godown = total_qty['godown'] or 0
        total_shop = total_qty['shop'] or 0
        total_combined = total_godown + total_shop
        
        total_value = StockSummary.objects.annotate(
            value=(F('godown_quantity') + F('shop_quantity')) * F('book__selling_price')
        ).aggregate(total=Sum('value'))['total'] or 0
        out_of_stock = StockSummary.objects.filter(godown_quantity=0, shop_quantity=0).count()

        return Response({
            'total_book_types': total_books,
            'total_quantity': total_combined,
            'total_godown_quantity': total_godown,
            'total_shop_quantity': total_shop,
            'total_stock_value': total_value,
            'out_of_stock_count': out_of_stock,
        })

class StockTransferViewSet(viewsets.ModelViewSet):
    queryset = StockTransfer.objects.select_related('book')
    serializer_class = StockTransferSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['book__title']
    http_method_names = ['get', 'post', 'head', 'options']
