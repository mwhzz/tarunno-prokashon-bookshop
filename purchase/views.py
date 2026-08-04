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

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """সার্চ যাই হোক, প্রকৃত মোট বকেয়া (সব ভেন্ডর মিলিয়ে) — লোড হওয়া পেজের উপর
        নির্ভরশীল না, তাই ভেন্ডরের সংখ্যা বেশি হলেও সংখ্যাটা সঠিক থাকে।"""
        qs = self.filter_queryset(self.get_queryset())
        agg = qs.aggregate(total_due=Sum('total_due'))
        return Response({
            'count': qs.count(),
            'total_due': agg['total_due'] or 0,
        })


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
        """শুধু বিল-লেভেল তথ্য (মেমো, ডিসকাউন্ট, তারিখ, নোট) এডিট করা যাবে — বইয়ের লাইন
        পরিবর্তন করলে স্টক এলোমেলো হয়ে যেতে পারে, তাই সেটা এখানে সাপোর্ট করা হয়নি।
        paid_amount/account_name ইচ্ছাকৃতভাবে এখানে এডিটযোগ্য না — পেমেন্ট শুধু add_payment
        দিয়েই যোগ করা যাবে, যাতে প্রতিটা পেমেন্টের মেথড (নগদ/ব্যাংক/বিকাশ) সবসময় VendorPayment
        লেজারে সঠিকভাবে রেকর্ড হয় এবং ক্যাশ লেজারে দ্বিগুণ গণনা না হয়।"""
        bill = self.get_object()
        old_due = bill.due_amount

        for field in ['memo_number', 'discount', 'purchase_date', 'note']:
            if field in request.data:
                setattr(bill, field, request.data[field])

        bill.total = bill.subtotal - bill.discount
        bill.save()  # save() recomputes due_amount/status

        if bill.vendor:
            bill.vendor.total_due += (bill.due_amount - old_due)
            bill.vendor.save(update_fields=['total_due'])

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

        payment_refs = [f"pbill_payment_{p.id}" for p in bill.payments.all()]
        affected_days = list(DailyCash.objects.filter(transactions__reference_id__in=payment_refs).distinct())
        CashTransaction.objects.filter(reference_id__in=payment_refs).delete()

        if bill.vendor:
            bill.vendor.total_due -= bill.due_amount
            bill.vendor.save(update_fields=['total_due'])

        bill.delete()

        for dc in affected_days:
            dc.update_closing_balance()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        """ক্রয় বিলের বকেয়ার বিপরীতে ভেন্ডরকে দেওয়া পেমেন্ট জমা করা — চাইলে বাড়তি ছাড়
        (discount) দিয়ে বিলটা কম টাকায়ও পুরোপুরি ক্লোজ করা যাবে।"""
        from decimal import Decimal, InvalidOperation

        from accounts.models import CashTransaction, DailyCash

        from .models import VendorPayment

        bill = self.get_object()
        amount_str = request.data.get('amount', 0)
        discount_str = request.data.get('discount', 0)
        method = request.data.get('method', 'cash')

        try:
            amount = Decimal(str(amount_str or 0))
            discount = Decimal(str(discount_str or 0))
        except InvalidOperation:
            return Response({'error': 'Invalid amount/discount format'}, status=status.HTTP_400_BAD_REQUEST)

        if amount < 0 or discount < 0:
            return Response({'error': 'Amount/discount must not be negative'}, status=status.HTTP_400_BAD_REQUEST)
        if amount + discount <= 0:
            return Response({'error': 'Amount is required'}, status=status.HTTP_400_BAD_REQUEST)
        if amount + discount > bill.due_amount:
            return Response({'error': 'পরিমাণ ও ছাড় মিলিয়ে মোট বাকির বেশি হতে পারবে না'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            payment = VendorPayment.objects.create(bill=bill, amount=amount, discount=discount, method=method)

            bill.discount += discount
            bill.total = bill.subtotal - bill.discount
            bill.paid_amount += amount
            bill.save()  # save() recomputes due_amount/status

            if bill.vendor:
                bill.vendor.total_due -= (amount + discount)
                bill.vendor.save(update_fields=['total_due'])

            # নগদ ছাড়া অন্য মাধ্যমে (bkash/nagad/bank) দেওয়া পেমেন্ট হাতের ক্যাশ লেজারে দেখানো যাবে না;
            # শুধু ছাড় দিয়ে বিল ক্লোজ করলে (amount=0) কোনো প্রকৃত ক্যাশ লেনদেনই হয়নি, তাই সেটাও বাদ
            if amount > 0 and method == 'cash':
                daily_cash = DailyCash.get_for_today()
                note = f"ক্রয় বিল পেমেন্ট: {bill.vendor_name} — {bill.books_summary()} (Bill #{bill.id})"
                if discount > 0:
                    note += f" [ছাড় ৳{discount}]"
                CashTransaction.objects.create(
                    daily_cash=daily_cash,
                    transaction_type='purchase',
                    amount=amount,
                    note=note,
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
