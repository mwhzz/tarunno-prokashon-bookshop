from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from .models import ExpenseCategory, Expense
from .serializers import ExpenseCategorySerializer, ExpenseSerializer
from users.permissions import IsAdmin, IsManager

class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all().order_by('name')
    serializer_class = ExpenseCategorySerializer
    permission_classes = [IsAdmin]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']

class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all().order_by('-date')
    serializer_class = ExpenseSerializer
    permission_classes = [IsManager]
    filter_backends = [filters.SearchFilter]
    search_fields = ['note', 'category__name']

    def get_queryset(self):
        qs = super().get_queryset()
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(date__date__gte=date_from)
        if date_to:
            qs = qs.filter(date__date__lte=date_to)
        return qs

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """স্ট্যাট কার্ডের জন্য সঠিক (পেজিনেশনের ঊর্ধ্বে না গিয়ে) সারাংশ —
        আজকের মোট/সংখ্যা এবং সর্বমোট (all-time) মোট/সংখ্যা/সবচেয়ে বড় খাত।"""
        from django.utils import timezone

        today = timezone.now().date()
        all_expenses = Expense.objects.all()
        today_expenses = all_expenses.filter(date__date=today)

        all_time_total = all_expenses.aggregate(t=Sum('amount'))['t'] or 0
        all_time_count = all_expenses.count()
        today_total = today_expenses.aggregate(t=Sum('amount'))['t'] or 0
        today_count = today_expenses.count()

        top = (
            all_expenses.values('category__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')
            .first()
        )

        return Response({
            'today_total': float(today_total),
            'today_count': today_count,
            'all_time_total': float(all_time_total),
            'all_time_count': all_time_count,
            'top_category': {'name': top['category__name'], 'amount': float(top['total'])} if top else None,
        })

    @action(detail=False, methods=['get'])
    def report(self, request):
        """খরচের সারাংশ এবং ক্যাটাগরি ভিত্তিক ব্রেকডাউন"""
        from django.utils import timezone
        from datetime import timedelta
        
        period = request.query_params.get('period', 'month')
        today = timezone.now().date()
        
        if period == 'today':
            date_from = today
        elif period == 'week':
            date_from = today - timedelta(days=6)
        else: # month
            date_from = today.replace(day=1)
            
        expenses = Expense.objects.filter(date__date__gte=date_from)
        total_amount = expenses.aggregate(Sum('amount'))['amount__sum'] or 0
        
        # ক্যাটাগরি ভিত্তিক ব্রেকডাউন
        category_breakdown = expenses.values('category__name').annotate(
            total=Sum('amount')
        ).order_by('-total')
        
        return Response({
            'total_expense': float(total_amount),
            'period': period,
            'category_breakdown': [
                {'category': item['category__name'], 'amount': float(item['total'])} 
                for item in category_breakdown
            ]
        })

    def create(self, request, *args, **kwargs):
        from accounts.models import DailyCash
        from rest_framework.response import Response
        from rest_framework import status
        
        amount = float(request.data.get('amount', 0))
        daily_cash = DailyCash.get_for_today()
        daily_cash.update_closing_balance()
        
        if daily_cash.closing_balance < amount:
            return Response({
                'error': f'অপর্যাপ্ত ক্যাশ ব্যালেন্স! আপনার বর্তমান ক্যাশ আছে ৳{daily_cash.closing_balance}, তাই আপনি ৳{amount} খরচ করতে পারবেন না।'
            }, status=status.HTTP_400_BAD_REQUEST)
            
        return super().create(request, *args, **kwargs)
