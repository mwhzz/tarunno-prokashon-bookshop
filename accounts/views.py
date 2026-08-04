import logging

from django.conf import settings
from django.db.models import Sum
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import DailyCash, CashTransaction
from .serializers import DailyCashSerializer, CashTransactionSerializer
from users.permissions import IsAdmin

logger = logging.getLogger(__name__)


def _method_breakdown(date_from=None, date_to=None):
    """বিক্রয়ে গ্রাহকের কাছ থেকে আদায় আর ভেন্ডরকে দেওয়া পেমেন্ট — দুটোই মেথড (নগদ/ব্যাংক/
    বিকাশ/নগদ) অনুযায়ী ভাগ করে দেখায়। ক্যাশ লেজার শুধু হাতের নগদ ট্র্যাক করে, তাই ব্যাংক/
    মোবাইলে যাওয়া টাকা দেখতে এই দুটো লেজার (sales.Payment, purchase.VendorPayment) থেকেই
    সরাসরি হিসাব করতে হয়।"""
    from purchase.models import VendorPayment
    from sales.models import Payment as SalePayment

    collections = SalePayment.objects.all()
    vendor_payments = VendorPayment.objects.all()
    if date_from:
        collections = collections.filter(created_at__date__gte=date_from)
        vendor_payments = vendor_payments.filter(created_at__date__gte=date_from)
    if date_to:
        collections = collections.filter(created_at__date__lte=date_to)
        vendor_payments = vendor_payments.filter(created_at__date__lte=date_to)

    collections = collections.values('method').annotate(total=Sum('amount')).order_by('method')
    vendor_payments = vendor_payments.values('method').annotate(total=Sum('amount')).order_by('method')

    return {
        'collections': [{'method': c['method'], 'amount': c['total'] or 0} for c in collections],
        'vendor_payments': [{'method': v['method'], 'amount': v['total'] or 0} for v in vendor_payments],
    }

class DailyCashViewSet(viewsets.ModelViewSet):
    queryset = DailyCash.objects.all().order_by('-date')
    serializer_class = DailyCashSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs

    def perform_update(self, serializer):
        instance = serializer.save()
        instance.update_closing_balance()

    @action(detail=False, methods=['get'])
    def today(self, request):
        obj = DailyCash.get_for_today()
        # রিক্যালকুলেট ব্যালেন্স জাস্ট ইন কেস
        obj.update_closing_balance()
        serializer = self.get_serializer(obj)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def method_breakdown(self, request, pk=None):
        """এই নির্দিষ্ট দিনের বিক্রয়-আদায় ও ভেন্ডর-পেমেন্ট মেথড অনুযায়ী ভাগ করে দেখায়
        (ব্যাংক/বিকাশ/নগদে যাওয়া টাকা, যা ক্যাশ লেজারে দেখা যায় না)।"""
        daily_cash = self.get_object()
        data = _method_breakdown(date_from=daily_cash.date, date_to=daily_cash.date)
        data['date'] = daily_cash.date
        return Response(data)

    @action(detail=False, methods=['get'])
    def method_summary(self, request):
        """একটা সময়সীমার (ডিফল্ট: এই মাস) জন্য মেথড অনুযায়ী মোট আদায়/পেমেন্ট।"""
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        return Response(_method_breakdown(date_from=date_from, date_to=date_to))

    @action(detail=True, methods=['post'])
    def close_day(self, request, pk=None):
        obj = self.get_object()
        obj.is_closed = True
        obj.update_closing_balance()

        telegram_report = {'sent': False}
        if settings.TELEGRAM_REPORT_ENABLED:
            try:
                from sales.daily_report import build_telegram_daily_owner_summary, send_telegram_owner_report

                send_telegram_owner_report(build_telegram_daily_owner_summary(obj.date))
                telegram_report['sent'] = True
            except Exception as exc:
                logger.exception("Failed to send Telegram report after closing day %s.", obj.date)
                telegram_report['error'] = str(exc)

        return Response({
            'message': f'Day {obj.date} closed successfully',
            'closing_balance': obj.closing_balance,
            'telegram_report': telegram_report,
        })

class CashTransactionViewSet(viewsets.ModelViewSet):
    queryset = CashTransaction.objects.all().order_by('-timestamp')
    serializer_class = CashTransactionSerializer
    permission_classes = [IsAdmin]

    # sale/expense/purchase এন্ট্রি অন্য মডেল (Sale/Expense/PurchaseBill) থেকে স্বয়ংক্রিয়ভাবে
    # আসে — এগুলো এখান থেকে সরাসরি এডিট/ডিলিট করলে মূল রেকর্ডের হিসাব (paid_amount, due
    # ইত্যাদি) আর ক্যাশ লেজারের সাথে মিলবে না। শুধু ম্যানুয়াল এন্ট্রি (cash_in/cash_out/
    # adjustment_in/adjustment_out) — যেগুলোর কোনো লিংকড উৎস নেই — এখান থেকে বদলানো নিরাপদ।
    AUTO_LINKED_TYPES = {'sale', 'expense', 'purchase'}

    def _block_if_auto_linked(self, instance):
        if instance.transaction_type in self.AUTO_LINKED_TYPES:
            return Response(
                {'error': 'এই এন্ট্রি স্বয়ংক্রিয়ভাবে বিক্রয়/খরচ/ক্রয় থেকে এসেছে — এখান থেকে সরাসরি এডিট বা ডিলিট করা যাবে না। মূল রেকর্ড থেকে পরিবর্তন করুন।'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        blocked = self._block_if_auto_linked(instance)
        if blocked:
            return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        blocked = self._block_if_auto_linked(instance)
        if blocked:
            return blocked
        response = super().partial_update(request, *args, **kwargs)
        # CashTransaction.save() নিজে থেকেই ব্যালেন্স রিক্যালকুলেট করে, তবু নিশ্চিত হতে আবার করা হচ্ছে
        instance.daily_cash.update_closing_balance()
        return response

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        blocked = self._block_if_auto_linked(instance)
        if blocked:
            return blocked
        daily_cash = instance.daily_cash
        response = super().destroy(request, *args, **kwargs)
        # save()-এর মতো delete()-এ কোনো হুক নেই, তাই ম্যানুয়ালি ব্যালেন্স রিক্যালকুলেট করতে হচ্ছে —
        # নাহলে এন্ট্রি মুছে গেলেও closing_balance পুরোনো (ভুল) মানই দেখাতে থাকতো
        daily_cash.update_closing_balance()
        return response

    def create(self, request, *args, **kwargs):
        daily_cash = DailyCash.get_for_today()
        if daily_cash.is_closed:
            return Response({'error': 'আজকের ক্যাশ হিসাব অলরেডি ক্লোজ করা হয়েছে।'}, status=status.HTTP_400_BAD_REQUEST)
        
        t_type = request.data.get('transaction_type')
        amount = float(request.data.get('amount', 0))
        
        # ব্যালেন্স চেক (যদি ক্যাশ আউট বা খরচ হয়)
        out_types = ['expense', 'cash_out', 'adjustment_out']
        if t_type in out_types:
            # বর্তমান ক্লোজিং ব্যালেন্স দেখা (রিক্যালকুলেট করে)
            daily_cash.update_closing_balance()
            if daily_cash.closing_balance < amount:
                return Response({
                    'error': f'অপর্যাপ্ত ব্যালেন্স! আপনার বর্তমান ক্যাশ আছে ৳{daily_cash.closing_balance}, কিন্তু আপনি ৳{amount} আউট করার চেষ্টা করছেন।'
                }, status=status.HTTP_400_BAD_REQUEST)

        data = request.data.copy()
        data['daily_cash'] = daily_cash.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
