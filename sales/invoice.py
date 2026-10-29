"""প্রিন্ট ও PDF ইনভয়েস একই হিসাব দেখায় — সব টাকা পুরো টাকায় রাউন্ড করা।"""
from decimal import Decimal, ROUND_HALF_UP

ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
        'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def whole(value):
    return int(Decimal(value or 0).quantize(Decimal('1'), ROUND_HALF_UP))


def taka(value):
    """30650 → '30,650'"""
    return f"{whole(value):,}"


def _below_hundred(n):
    return ONES[n] if n < 20 else (TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else ''))


def _below_thousand(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{ONES[hundreds]} Hundred")
    if rest:
        parts.append(('And ' if hundreds else '') + _below_hundred(rest))
    return ' '.join(parts)


def amount_in_words(value):
    """দেশি নিয়মে (কোটি/লাখ/হাজার): 14580 → 'Fourteen Thousand Five Hundred And Eighty Tk Only'"""
    n = whole(value)
    if n == 0:
        return 'Zero Tk Only'
    parts = []
    for size, name in ((10_000_000, 'Crore'), (100_000, 'Lakh'), (1000, 'Thousand')):
        count, n = divmod(n, size)
        if count:
            parts.append(f"{amount_in_words(count)[:-8]} {name}" if count >= 100 else f"{_below_hundred(count)} {name}")
    if n:
        parts.append(_below_thousand(n))
    return ' '.join(parts) + ' Tk Only'


def invoice_context(sale):
    items = list(sale.items.select_related('book').order_by('id'))
    rows = []
    for index, item in enumerate(items, 1):
        gross = item.unit_price * item.quantity
        rows.append({
            'sn': index,
            'title': item.book.title,
            'qty': item.quantity,
            'mrp': taka(item.unit_price),
            'unit_price': taka(item.total / item.quantity) if item.quantity else '0',
            'commission': whole(item.discount * 100 / gross) if gross else 0,
            'line_total': taka(item.total),
        })

    adjustments = []
    if sale.discount > 0:
        adjustments.append(('Discount', '- ' + taka(sale.discount)))
    for label, amount in (('Packing', sale.packaging_charge), ('Courier', sale.courier_charge), ('Other Fee', sale.transaction_fee)):
        if amount > 0:
            adjustments.append((label, taka(amount)))

    customer = sale.customer
    sold_by = ''
    if sale.created_by:
        sold_by = sale.created_by.get_full_name() or sale.created_by.get_username()
    return {
        'sale': sale,
        'rows': rows,
        'total_qty': sum(i.quantity for i in items),
        'gross_total': taka(sale.gross_subtotal),
        'line_total_sum': taka(sale.subtotal),
        'adjustments': adjustments,
        'selling_price': taka(sale.total),
        'received': taka(sale.paid_amount),
        'due': taka(sale.due_amount),
        'total_due': taka(sale.previous_due + sale.due_amount),
        'customer_code': (customer.customer_code or customer.pk) if customer else '',
        'customer_address': customer.address if customer else '',
        'sold_by': sold_by,
        'amount_words': amount_in_words(sale.total),
    }
