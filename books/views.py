from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
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
