from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal
from django.utils.timezone import localtime
from .models import Sale, SaleItem, Customer, ExternalTrade, Payment
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


def _realized(sale, paid):
    """কতটুকু টাকা পেলে কত লাভ: আগে খরচ উঠবে, তারপর যা আসবে সবই লাভ; পুরো পরিশোধে পুরো লাভ/ক্ষতি"""
    if paid >= sale.total:
        return sale.total - sale.total_cost
    return max(paid - sale.total_cost, Decimal('0'))


def collections_and_profit(date_from, date_to):
    """যেদিন টাকা হাতে এসেছে সেদিনের হিসাবে আদায় ও লাভ — বিল যেদিন বানানো সেদিনের নয়।
    পুরনো বাকি আজ আদায় হলে সেটা আজকের আদায় ও আজকের লাভে যোগ হয়।"""
    from accounts.models import CashTransaction

    sale_ids = set(Payment.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to).values_list('sale_id', flat=True))
    # খুব পুরনো বিক্রয়ে পেমেন্টের আলাদা রেকর্ড নেই; সেগুলোর পাওয়া টাকা বিক্রয়ের দিনেই ধরা হয়
    sale_ids |= set(Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to).values_list('id', flat=True))

    collected, profit = {}, {}
    for sale in Sale.objects.filter(id__in=sale_ids).prefetch_related('payments'):
        events = [(localtime(p.created_at).date(), p.created_at, p.amount) for p in sale.payments.all()]
        legacy = sale.paid_amount - sum((e[2] for e in events), Decimal('0'))
        if legacy > 0:
            events.append((localtime(sale.created_at).date(), sale.created_at, legacy))
        events.sort(key=lambda e: e[1])
        paid = Decimal('0')
        for day, _, amount in events:
            before = _realized(sale, paid)
            paid += amount
            if date_from <= day <= date_to:
                collected[day] = collected.get(day, 0) + float(amount)
                profit[day] = profit.get(day, 0) + float(_realized(sale, paid) - before)

    # ইনভয়েসের বাইরের পুরনো বাকি (ওপেনিং ব্যালেন্স) আদায়ও আদায়ের মধ্যে পড়ে, লাভে নয়
    for t in CashTransaction.objects.filter(reference_id__startswith='customer_due_', timestamp__date__gte=date_from, timestamp__date__lte=date_to):
        day = localtime(t.timestamp).date()
        collected[day] = collected.get(day, 0) + float(t.amount)
    from accounts.models import MoneyMovement
    for m in MoneyMovement.objects.filter(kind='collection', created_at__date__gte=date_from, created_at__date__lte=date_to):
        day = localtime(m.created_at).date()
        collected[day] = collected.get(day, 0) + float(m.amount)
    return collected, profit


def sales_report(date_from, date_to):
    sales = Sale.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    items = SaleItem.objects.filter(sale__created_at__date__gte=date_from, sale__created_at__date__lte=date_to)
    expenses = Expense.objects.filter(date__date__gte=date_from, date__date__lte=date_to)

    total_revenue = sales.aggregate(t=Sum('total'))['t'] or 0
    collected_by_day, realized_by_day = collections_and_profit(date_from, date_to)
    total_paid = sum(collected_by_day.values())
    total_due = sales.aggregate(t=Sum('due_amount'))['t'] or 0
    total_expenses = expenses.aggregate(t=Sum('amount'))['t'] or 0

    # মোট লাভ (Gross Profit) = (বিক্রয় মূল্য - ক্রয় মূল্য) × পরিমাণ - আইটেম ডিসকাউন্ট
    gross_profit = items.annotate(
        item_profit=ExpressionWrapper(
            (F('unit_price') - F('cost_price')) * F('quantity') - F('discount'),
            output_field=DecimalField()
        )
    ).aggregate(total=Sum('item_profit'))['total'] or 0
    
    # আদায়কৃত লাভ (Realized Profit) = Gross Profit * (Paid / Total)
    realized_profit = sum(realized_by_day.values())

    # নিট লাভ (Net Profit) = আদায়কৃত লাভ - মোট খরচ
    external_trades_sold = ExternalTrade.objects.filter(status='sold', sold_at__date__gte=date_from, sold_at__date__lte=date_to)
    external_trade_profit = external_trades_sold.aggregate(total=Sum(ExpressionWrapper((F('selling_price') - F('purchase_price')) * F('quantity'), output_field=DecimalField())))['total'] or 0
    total_profit = float(realized_profit) + float(external_trade_profit)
    net_profit = float(realized_profit) + float(external_trade_profit) - float(total_expenses)

    # দৈনিক breakdown
    daily = []
    current = date_from
    while current <= date_to:
        day_sales = sales.filter(created_at__date=current)
        day_expenses = expenses.filter(date__date=current)
        
        day_revenue = day_sales.aggregate(t=Sum('total'))['t'] or 0
        day_exp = day_expenses.aggregate(t=Sum('amount'))['t'] or 0
        
        daily.append({
            'date': str(current),
            'invoices': day_sales.count(),
            'revenue': float(day_revenue),
            'paid': collected_by_day.get(current, 0),
            'expenses': float(day_exp),
            'profit': realized_by_day.get(current, 0),
        })
        current += timedelta(days=1)

    # সর্বাধিক বিক্রিত বই
    top_books = items.values('book__title', 'book__author').annotate(
        total_qty=Sum('quantity'),
        total_revenue=Sum('total')
    ).order_by('-total_qty')[:10]

    # কাস্টমার বকেয়া সামারি
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
            'external_trade_profit': float(external_trade_profit),
            'total_profit': float(total_profit),
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

