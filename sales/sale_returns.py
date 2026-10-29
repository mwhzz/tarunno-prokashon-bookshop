"""বই ফেরত: কমিশন বাদে দাম বিল থেকে কমে, আগে বাকি কমে, তারপরও বেশি পাওয়া থাকলে টাকা ফেরত যায়।"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F, Sum
from rest_framework import serializers

from accounts.models import CashTransaction, DailyCash
from stock.models import StockEntry, StockSummary
from .models import Customer, Payment, Sale, SaleReturn

CENT = Decimal('0.01')


class ReturnItemInput(serializers.Serializer):
    book_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=0)


class SaleReturnInput(serializers.Serializer):
    items = ReturnItemInput(many=True)
    reason = serializers.CharField(allow_blank=True, default='')
    refund_method = serializers.ChoiceField(choices=['cash', 'bkash', 'nagad', 'bank'], default='cash')

    def validate_items(self, items):
        items = [i for i in items if i['quantity'] > 0]
        if not items:
            raise serializers.ValidationError('অন্তত একটি বইয়ের ফেরত পরিমাণ লিখুন।')
        ids = [i['book_id'] for i in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('একই বই একাধিকবার দেওয়া যাবে না।')
        return items


@transaction.atomic
def return_sale_items(sale_pk, raw):
    serializer = SaleReturnInput(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    sale = Sale.objects.select_for_update().get(pk=sale_pk)
    lines = {i.book_id: i for i in sale.items.select_related('book')}
    old_total, old_due = sale.total, sale.due_amount

    returned_value = Decimal('0')
    for row in data['items']:
        item = lines.get(row['book_id'])
        if not item:
            raise serializers.ValidationError({'items': 'এই বই এই মেমোতে ছিল না।'})
        qty = row['quantity']
        if qty > item.quantity:
            raise serializers.ValidationError({'items': f"'{item.book.title}' মেমোতে আছে {item.quantity} কপি, এর বেশি ফেরত নেওয়া যাবে না।"})

        # কমিশন একই হারে কমে, তাই এক কপির দাম = (দাম − কমিশন) / পরিমাণ
        old_line_total = item.total
        remaining = item.quantity - qty
        if remaining:
            item.discount = (item.discount * remaining / item.quantity).quantize(CENT, ROUND_HALF_UP)
            item.quantity = remaining
            item.save()
            value = old_line_total - item.total
        else:
            item.delete()
            value = old_line_total
        returned_value += value

        SaleReturn.objects.create(sale=sale, book=item.book, quantity=qty, amount=value, reason=data['reason'] or 'Customer Return')
        StockSummary.update_stock(item.book, qty, location='shop')
        StockEntry.objects.create(
            book=item.book, quantity=qty, source='return', location='shop', reference_id=sale.id,
            note=f"Return from Invoice #{sale.invoice_number}",
        )

    remaining_items = sale.items.all()
    sale.subtotal = remaining_items.aggregate(t=Sum('total'))['t'] or Decimal('0')
    sale.total_cost = sum((i.cost_price * i.quantity for i in remaining_items), Decimal('0'))
    # সব বই ফেরত এলে বাড়তি ছাড় বিলকে মাইনাসে নিয়ে যেতে পারে না
    sale.discount = min(sale.discount, sale.subtotal)
    sale.total = sale.subtotal - sale.discount + sale.packaging_charge + sale.courier_charge + sale.transaction_fee

    # আগে বাকি কমবে; বিল পাওয়া টাকার নিচে নামলে বাড়তিটা ফেরত দিতে হবে
    refund = max(sale.paid_amount - sale.total, Decimal('0'))
    if refund:
        sale.paid_amount -= refund
        payment = Payment.objects.create(
            sale=sale, amount=-refund, method=data['refund_method'], transaction_id='RETURN',
        )
        if data['refund_method'] == 'cash':
            CashTransaction.objects.create(
                daily_cash=DailyCash.get_for_today(), transaction_type='cash_out', amount=refund,
                note=f"বই ফেরতের টাকা ফেরত #{sale.invoice_number}", reference_id=f"pay_{payment.id}",
            )
    sale.save()

    due_reduced = old_due - sale.due_amount
    if sale.customer_id and due_reduced:
        Customer.objects.filter(pk=sale.customer_id).update(total_due=F('total_due') - due_reduced)

    return {
        'returned_value': returned_value,
        'bill_reduced': old_total - sale.total,
        'due_reduced': due_reduced,
        'refund': refund,
        'refund_method': data['refund_method'],
    }
