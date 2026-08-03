from django.db import transaction
from django.db.models import Sum
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import PurchaseBill, Vendor
from .serializers import (
    PurchaseBillCreateSerializer,
    PurchaseBillSerializer,
    VendorSerializer,
)


class VendorViewSet(viewsets.ModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'phone', 'vendor_code']


class PurchaseBillViewSet(viewsets.ModelViewSet):
    queryset = PurchaseBill.objects.select_related('vendor', 'created_by').prefetch_related('items', 'payments')
    filter_backends = [filters.SearchFilter]
    search_fields = ['vendor_name', 'memo_number']

    def get_serializer_class(self):
        if self.action == 'create':
            return PurchaseBillCreateSerializer
        return PurchaseBillSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def get_queryset(self):
        qs = super().get_queryset()
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(purchase_date__gte=date_from)
        if date_to:
            qs = qs.filter(purchase_date__lte=date_to)
        vendor_id = self.request.query_params.get('vendor')
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = PurchaseBillCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        bill = serializer.save()
        return Response(PurchaseBillSerializer(bill).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """শুধু বিল-লেভেল তথ্য (মেমো, ডিসকাউন্ট, পেইড অ্যামাউন্ট, নোট) এডিট করা যাবে —
        বইয়ের লাইন পরিবর্তন করলে স্টক এলোমেলো হয়ে যেতে পারে, তাই সেটা এখানে সাপোর্ট করা হয়নি।"""
        from accounts.models import CashTransaction, DailyCash

        bill = self.get_object()
        old_due = bill.due_amount
        old_paid = bill.paid_amount

        for field in ['memo_number', 'discount', 'paid_amount', 'account_name', 'purchase_date', 'note']:
            if field in request.data:
                setattr(bill, field, request.data[field])

        bill.total = bill.subtotal - bill.discount
        bill.save()  # save() recomputes due_amount/status

        if bill.vendor:
            bill.vendor.total_due += (bill.due_amount - old_due)
            bill.vendor.save(update_fields=['total_due'])

        if bill.paid_amount != old_paid:
            CashTransaction.objects.filter(reference_id=f"pbill_{bill.id}").delete()
            if bill.paid_amount > 0:
                daily_cash = DailyCash.get_for_today()
                CashTransaction.objects.create(
                    daily_cash=daily_cash,
                    transaction_type='purchase',
                    amount=bill.paid_amount,
                    note=f"ক্রয় বিল পেমেন্ট (আপডেট): {bill.vendor_name} (Bill #{bill.id})",
                    reference_id=f"pbill_{bill.id}",
                )
            for dc in DailyCash.objects.filter(transactions__reference_id=f"pbill_{bill.id}").distinct():
                dc.update_closing_balance()

        return Response(PurchaseBillSerializer(bill).data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        from accounts.models import CashTransaction, DailyCash
        from stock.models import StockEntry, StockSummary

        bill = self.get_object()

        for item in bill.items.all():
            if item.book_id:
                StockSummary.update_stock(item.book, -item.quantity, location=bill.location)
                StockEntry.objects.create(
                    book=item.book,
                    quantity=-item.quantity,
                    source='adjustment',
                    location=bill.location,
                    reference_id=bill.id,
                    note=f"Purchase Bill #{bill.id} বাতিল/মুছে ফেলার কারণে স্টক ফেরত",
                )

        affected_days = list(DailyCash.objects.filter(transactions__reference_id=f"pbill_{bill.id}").distinct())
        CashTransaction.objects.filter(reference_id=f"pbill_{bill.id}").delete()

        if bill.vendor:
            bill.vendor.total_due -= bill.due_amount
            bill.vendor.save(update_fields=['total_due'])

        bill.delete()

        for dc in affected_days:
            dc.update_closing_balance()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        """ক্রয় বিলের বকেয়ার বিপরীতে ভেন্ডরকে দেওয়া পেমেন্ট জমা করা"""
        from decimal import Decimal, InvalidOperation

        from accounts.models import CashTransaction, DailyCash

        from .models import VendorPayment

        bill = self.get_object()
        amount_str = request.data.get('amount')
        method = request.data.get('method', 'cash')

        if not amount_str:
            return Response({'error': 'Amount is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = Decimal(str(amount_str))
        except InvalidOperation:
            return Response({'error': 'Invalid amount format'}, status=status.HTTP_400_BAD_REQUEST)

        if amount <= 0:
            return Response({'error': 'Amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)
        if amount > bill.due_amount:
            return Response({'error': 'Amount exceeds due balance'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            payment = VendorPayment.objects.create(bill=bill, amount=amount, method=method)

            bill.paid_amount += amount
            bill.save()  # save() recomputes due_amount/status

            if bill.vendor:
                bill.vendor.total_due -= amount
                bill.vendor.save(update_fields=['total_due'])

            daily_cash = DailyCash.get_for_today()
            CashTransaction.objects.create(
                daily_cash=daily_cash,
                transaction_type='purchase',
                amount=amount,
                note=f"ক্রয় বিল পেমেন্ট: {bill.vendor_name} (Bill #{bill.id})",
                reference_id=f"pbill_payment_{payment.id}",
            )
            daily_cash.update_closing_balance()

        return Response(PurchaseBillSerializer(bill).data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        agg = qs.aggregate(
            total_price=Sum('total'),
            discount=Sum('discount'),
            paid=Sum('paid_amount'),
            due=Sum('due_amount'),
        )
        return Response({
            'total_price': agg['total_price'] or 0,
            'discount': agg['discount'] or 0,
            'paid': agg['paid'] or 0,
            'due': agg['due'] or 0,
            'count': qs.count(),
        })
