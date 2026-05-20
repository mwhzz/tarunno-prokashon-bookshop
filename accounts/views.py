import logging

from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import DailyCash, CashTransaction
from .serializers import DailyCashSerializer, CashTransactionSerializer
from users.permissions import IsAdmin

logger = logging.getLogger(__name__)

class DailyCashViewSet(viewsets.ModelViewSet):
    queryset = DailyCash.objects.all().order_by('-date')
    serializer_class = DailyCashSerializer
    permission_classes = [IsAdmin]

    @action(detail=False, methods=['get'])
    def today(self, request):
        obj = DailyCash.get_for_today()
        # রিক্যালকুলেট ব্যালেন্স জাস্ট ইন কেস
        obj.update_closing_balance()
        serializer = self.get_serializer(obj)
        return Response(serializer.data)

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
