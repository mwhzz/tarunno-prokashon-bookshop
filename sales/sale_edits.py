"""বিক্রয়ের পরে মেমো সংশোধন: স্টক, কাস্টমারের বাকি ও খরচ মিলিয়ে বদলায়, আর প্রতিটা সংশোধনের ইতিহাস রাখে।"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from accounts.models import CashTransaction, DailyCash
from books.models import Book
from stock.models import StockEntry, StockSummary
from .models import Customer, Payment, Sale, SaleEdit, SaleItem


class SaleEditConflict(APIException):
    status_code = 409
    default_detail = 'এই মেমো এর মধ্যে অন্য কেউ বদলেছে। পেজ রিফ্রেশ করে আবার চেষ্টা করুন।'


class SaleEditItemInput(serializers.Serializer):
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)

    def validate(self, data):
        if data['discount'] > data['unit_price'] * data['quantity']:
            raise serializers.ValidationError('বইয়ের ছাড় তার মোট দামের বেশি হতে পারবে না।')
        return data


class SaleEditInput(serializers.Serializer):
    edit_count = serializers.IntegerField(min_value=0)
    customer_name = serializers.CharField(max_length=200, allow_blank=True, default='')
    customer_phone = serializers.CharField(max_length=20, allow_blank=True, default='')
    customer_address = serializers.CharField(allow_blank=True, required=False)
    sale_type = serializers.ChoiceField(choices=Sale.SALE_TYPE_CHOICES, required=False)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    payment_method = serializers.ChoiceField(choices=['cash', 'bkash', 'nagad', 'bank'], default='cash')
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    packaging_charge = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    courier_charge = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    transaction_fee = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    note = serializers.CharField(allow_blank=True, default='')
    reason = serializers.CharField(allow_blank=True, default='')
    items = SaleEditItemInput(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError('কমপক্ষে একটি বই লাগবে।')
        ids = [item['book'].pk for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('একই বই একাধিক লাইনে দেওয়া যাবে না। পরিমাণ এক লাইনে লিখুন।')
        return items


def _money(value):
    return format(Decimal(value), '.2f')


def payment_discount_total(sale):
    """বাকি তোলার সময় দেওয়া ছাড়ও Sale.discount-এ যোগ হয়; মেমোর নিজের ছাড় আলাদা করতে এটা বাদ দিতে হয়।"""
    return sale.payments.aggregate(t=Sum('discount'))['t'] or Decimal('0')


def snapshot(sale):
    return {
        'customer_name': sale.customer_name,
        'customer_phone': sale.customer_phone,
        'customer_address': sale.customer.address if sale.customer else '',
        'sale_type': sale.sale_type,
        'discount': _money(sale.discount - payment_discount_total(sale)),
        'packaging_charge': _money(sale.packaging_charge),
        'courier_charge': _money(sale.courier_charge),
        'transaction_fee': _money(sale.transaction_fee),
        'note': sale.note,
        'total': _money(sale.total),
        'paid_amount': _money(sale.paid_amount),
        'due_amount': _money(sale.due_amount),
        'items': [
            {'book': i.book_id, 'title': i.book.title, 'quantity': i.quantity,
             'unit_price': _money(i.unit_price), 'discount': _money(i.discount), 'total': _money(i.total)}
            for i in sale.items.select_related('book').order_by('id')
        ],
    }


FIELD_LABELS = [
    ('customer_name', 'গ্রাহকের নাম'), ('customer_phone', 'ফোন'), ('customer_address', 'ঠিকানা'),
    ('sale_type', 'বিক্রয়ের ধরন'), ('discount', 'বাড়তি ছাড়'),
    ('packaging_charge', 'প্যাকিং চার্জ'), ('courier_charge', 'কুরিয়ার চার্জ'),
    ('transaction_fee', 'ট্রানজেকশন ফি'), ('note', 'নোট'), ('total', 'মোট বিল'),
    ('paid_amount', 'পাওয়া টাকা'), ('due_amount', 'বাকি'),
]


def describe_changes(before, after):
    changes = []
    for key, label in FIELD_LABELS:
        if before[key] != after[key]:
            changes.append(f"{label}: {before[key] or '—'} → {after[key] or '—'}")
    old_items = {i['book']: i for i in before['items']}
    new_items = {i['book']: i for i in after['items']}
    for book_id, old in old_items.items():
        new = new_items.get(book_id)
        if not new:
            changes.append(f"বাদ: {old['title']} ({old['quantity']} কপি)")
            continue
        parts = []
        if old['quantity'] != new['quantity']:
            parts.append(f"পরিমাণ {old['quantity']} → {new['quantity']}")
        if old['unit_price'] != new['unit_price']:
            parts.append(f"দাম ৳{old['unit_price']} → ৳{new['unit_price']}")
        if old['discount'] != new['discount']:
            parts.append(f"কমিশন ৳{old['discount']} → ৳{new['discount']}")
        if parts:
            changes.append(f"{old['title']}: " + ', '.join(parts))
    for book_id, new in new_items.items():
        if book_id not in old_items:
            changes.append(f"যোগ: {new['title']} ({new['quantity']} কপি, ৳{new['total']})")
    return changes


@transaction.atomic
def edit_sale(sale_pk, raw, user=None):
    serializer = SaleEditInput(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    sale = Sale.objects.select_for_update().get(pk=sale_pk)
    if sale.edits.count() != data['edit_count']:
        raise SaleEditConflict()
    if sale.returns.exists():
        raise serializers.ValidationError('এই মেমো থেকে বই ফেরত নেওয়া হয়েছে, তাই এটা আর এডিট করা যাবে না।')

    before = snapshot(sale)
    old_items = {i.book_id: i for i in sale.items.select_related('book')}
    new_items = data['items']

    for item in new_items:
        if item['book'].pk not in old_items and not item['book'].is_active:
            raise serializers.ValidationError({'items': f"'{item['book'].title}' বইটি বন্ধ করা আছে, নতুন করে যোগ করা যাবে না।"})

    subtotal = sum(i['unit_price'] * i['quantity'] - i['discount'] for i in new_items)
    if data['discount'] > subtotal:
        raise serializers.ValidationError({'discount': 'ছাড় বইয়ের মোট দামের বেশি হতে পারবে না।'})
    payment_discount = payment_discount_total(sale)
    total = subtotal - data['discount'] - payment_discount + data['packaging_charge'] + data['courier_charge'] + data['transaction_fee']
    new_paid = data.get('paid_amount', sale.paid_amount)
    if total < new_paid:
        raise serializers.ValidationError({'paid_amount': f"নতুন বিল (৳{_money(total)}) পাওয়া টাকার (৳{_money(new_paid)}) চেয়ে কম। পাওয়া টাকা কমান।"})
    phone = data['customer_phone'].strip()
    if total > new_paid and not phone:
        raise serializers.ValidationError({'customer_phone': 'বাকি থাকলে ক্রেতার ফোন নম্বর লাগবে।'})

    # স্টক: শুধু পরিমাণের পার্থক্যটুকু দোকান থেকে নেওয়া/ফেরত দেওয়া হয়
    deltas = {book_id: -item.quantity for book_id, item in old_items.items()}
    books = {item.book_id: item.book for item in old_items.values()}
    for item in new_items:
        deltas[item['book'].pk] = deltas.get(item['book'].pk, 0) + item['quantity']
        books[item['book'].pk] = item['book']
    for book_id in sorted(deltas):
        delta = deltas[book_id]
        if not delta:
            continue
        book = books[book_id]
        if delta > 0:
            taken = StockSummary.objects.filter(book=book, shop_quantity__gte=delta).update(
                shop_quantity=F('shop_quantity') - delta, last_updated=timezone.now())
            if taken != 1:
                raise serializers.ValidationError({'items': f"'{book.title}' বইয়ের দোকানে আর {delta} কপি নেই।"})
        else:
            StockSummary.update_stock(book, -delta, location='shop')
        StockEntry.objects.create(
            book=book, quantity=-delta, source='adjustment', location='shop', reference_id=sale.id,
            note=f"Invoice edit #{sale.invoice_number}",
        )

    # খরচ: আগের লাইনের ক্রয়মূল্য যেমন ছিল তেমন থাকে, নতুন বইয়ে এখনকার ক্রয়মূল্য
    sale.items.all().delete()
    total_cost = Decimal('0')
    for item in new_items:
        old = old_items.get(item['book'].pk)
        cost = old.cost_price if old else (item['book'].purchase_price or 0)
        total_cost += cost * item['quantity']
        SaleItem.objects.create(
            sale=sale, book=item['book'], quantity=item['quantity'],
            unit_price=item['unit_price'], cost_price=cost, discount=item['discount'],
        )

    # কাস্টমারের বাকি: পুরনো কাস্টমার থেকে আগের বাকি সরিয়ে নতুন/একই কাস্টমারে নতুন বাকি বসে
    # পাওয়া টাকা ভুল লেখা থাকলে পার্থক্যটা একটা পেমেন্ট হিসেবে বসে, তাই ক্যাশ/অ্যাকাউন্টও মিলে যায়
    paid_diff = new_paid - sale.paid_amount
    if paid_diff:
        payment = Payment.objects.create(
            sale=sale, amount=paid_diff, method=data['payment_method'], transaction_id='MEMO-EDIT',
        )
        if paid_diff < 0 and data['payment_method'] == 'cash':
            day = DailyCash.objects.select_for_update().get(pk=DailyCash.get_for_today().pk)
            if day.is_closed:
                raise serializers.ValidationError('আজকের ক্যাশ ক্লোজ করা হয়েছে, ক্যাশ ফেরত দেখানো যাবে না।')
            CashTransaction.objects.create(
                daily_cash=day, transaction_type='cash_out', amount=-paid_diff,
                note=f"মেমো সংশোধন: পাওয়া টাকা কমানো #{sale.invoice_number}", reference_id=f"pay_{payment.id}",
            )
        sale.paid_amount = new_paid

    old_due = sale.due_amount
    new_due = total - new_paid
    customer = sale.customer
    if phone and (not customer or customer.phone != phone):
        customer, _ = Customer.objects.get_or_create(phone=phone, defaults={'name': data['customer_name'] or phone})
    elif not phone:
        customer = None
    if sale.customer_id and old_due:
        Customer.objects.filter(pk=sale.customer_id).update(total_due=F('total_due') - old_due)
    if customer and new_due:
        Customer.objects.filter(pk=customer.pk).update(total_due=F('total_due') + new_due)

    if customer and 'customer_address' in data and customer.address != data['customer_address']:
        customer.address = data['customer_address']
        customer.save(update_fields=['address'])
    if 'sale_type' in data:
        sale.sale_type = data['sale_type']
    sale.customer = customer
    sale.customer_name = data['customer_name'] or 'Walk-in Customer'
    sale.customer_phone = phone
    sale.subtotal = subtotal
    sale.discount = data['discount'] + payment_discount
    sale.packaging_charge = data['packaging_charge']
    sale.courier_charge = data['courier_charge']
    sale.transaction_fee = data['transaction_fee']
    sale.note = data['note']
    sale.total = total
    sale.total_cost = total_cost
    sale.save()

    after = snapshot(sale)
    changes = describe_changes(before, after)
    if not changes:
        raise serializers.ValidationError('কিছুই বদলানো হয়নি।')
    SaleEdit.objects.create(
        sale=sale, edited_by=user if user and user.is_authenticated else None,
        reason=data['reason'], before=before, after=after, changes=changes,
    )
    return sale
