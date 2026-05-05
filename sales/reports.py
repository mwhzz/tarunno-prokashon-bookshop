from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.utils import timezone
from datetime import timedelta, date
from .models import Sale, SaleItem, Customer
from stock.models import StockSummary
from expenses.models import Expense


def get_date_range(period, date_from=None, date_to=None):
    today = timezone.now().date()
    if period == 'today':
        return today, today
    elif period == 'week':
        return today - timedelta(days=6), today
    elif period == 'month':
        return today.replace(day=1), today
    elif period == 'year':
        return today.replace(month=1, day=1), today
    elif period == 'custom' and date_from and date_to:
        return date_from, date_to
    return today.replace(day=1), today


def sales_report(date_from, date_to):
    sales = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    items = SaleItem.objects.filter(sale__created_at__date__gte=date_from, sale__created_at__date__lte=date_to)
    expenses = Expense.objects.filter(date__date__gte=date_from, date__date__lte=date_to)

    total_revenue = sales.aggregate(t=Sum('total'))['t'] or 0
    total_paid = sales.aggregate(t=Sum('paid_amount'))['t'] or 0
    total_due = sales.aggregate(t=Sum('due_amount'))['t'] or 0
    total_expenses = expenses.aggregate(t=Sum('amount'))['t'] or 0

    # মোট লাভ (Gross Profit) = (বিক্রয় মূল্য - ক্রয় মূল্য) × পরিমাণ - আইটেম ডিসকাউন্ট
    gross_profit = items.annotate(
        item_profit=ExpressionWrapper(
            (F('unit_price') - F('cost_price')) * F('quantity') - F('discount'),
            output_field=DecimalField()
        )
    ).aggregate(total=Sum('item_profit'))['total'] or 0
    
    # আদায়কৃত লাভ (Realized Profit) = Gross Profit * (Paid / Total)
    realized_profit = 0
    for s in sales:
        realized_profit += float(s.realized_profit)

    # নিট লাভ (Net Profit) = আদায়কৃত লাভ - মোট খরচ
    net_profit = float(realized_profit) - float(total_expenses)

    # দৈনিক breakdown
    daily = []
    current = date_from
    while current <= date_to:
        day_sales = sales.filter(created_at__date=current)
        day_items = items.filter(sale__created_at__date=current)
        day_expenses = expenses.filter(date__date=current)
        
        day_revenue = day_sales.aggregate(t=Sum('total'))['t'] or 0
        day_paid = day_sales.aggregate(t=Sum('paid_amount'))['t'] or 0
        day_exp = day_expenses.aggregate(t=Sum('amount'))['t'] or 0
        
        day_gross_profit = day_items.annotate(
            item_profit=ExpressionWrapper(
                (F('unit_price') - F('cost_price')) * F('quantity') - F('discount'),
                output_field=DecimalField()
            )
        ).aggregate(total=Sum('item_profit'))['total'] or 0
        
        daily.append({
            'date': str(current),
            'invoices': day_sales.count(),
            'revenue': float(day_revenue),
            'paid': float(day_paid),
            'expenses': float(day_exp),
            'profit': float(day_gross_profit),
        })
        current += timedelta(days=1)

    # সর্বাধিক বিক্রিত বই
    top_books = items.values('book__title', 'book__author').annotate(
        total_qty=Sum('quantity'),
        total_revenue=Sum('total')
    ).order_by('-total_qty')[:10]

    # কাস্টমার বকেয়া সামারি
    top_due_customers = Customer.objects.filter(total_due__gt=0).order_by('-total_due')[:5]
    total_receivable = Customer.objects.aggregate(t=Sum('total_due'))['t'] or 0

    return {
        'period': {'from': str(date_from), 'to': str(date_to)},
        'summary': {
            'total_invoices': sales.count(),
            'total_revenue': float(total_revenue),
            'total_paid': float(total_paid),
            'total_due': float(total_due),
            'total_expenses': float(total_expenses),
            'gross_profit': float(gross_profit),
            'realized_profit': float(realized_profit),
            'net_profit': float(net_profit),
            'total_receivable': float(total_receivable),
        },
        'daily_breakdown': daily,
        'top_books': list(top_books),
        'top_due_customers': [
            {'name': c.name, 'phone': c.phone, 'due': float(c.total_due)} 
            for c in top_due_customers
        ]
    }


def stock_value_report():
    summaries = StockSummary.objects.select_related('book').annotate(
        total_stock=F('godown_quantity') + F('shop_quantity')
    ).filter(total_stock__gt=0)
    
    total_value = summaries.annotate(
        value=ExpressionWrapper(F('total_stock') * F('book__selling_price'), output_field=DecimalField())
    ).aggregate(total=Sum('value'))['total'] or 0
    
    cost_value = summaries.annotate(
        value=ExpressionWrapper(F('total_stock') * F('book__purchase_price'), output_field=DecimalField())
    ).aggregate(total=Sum('value'))['total'] or 0

    # ক্যাটাগরি ভিত্তিক স্টক ভ্যালু
    category_stock = summaries.values('book__group__name').annotate(
        total_qty=Sum('total_stock'),
        total_value=Sum(ExpressionWrapper(F('total_stock') * F('book__selling_price'), output_field=DecimalField()))
    ).order_by('-total_value')

    return {
        'total_book_types': summaries.count(),
        'total_quantity': summaries.aggregate(t=Sum('total_stock'))['t'] or 0,
        'retail_value': float(total_value),
        'cost_value': float(cost_value),
        'potential_profit': float(total_value - cost_value),
        'category_breakdown': [
            {
                'category': item['book__group__name'] or 'Uncategorized',
                'qty': item['total_qty'],
                'value': float(item['total_value'])
            } for item in category_stock
        ]
    }

