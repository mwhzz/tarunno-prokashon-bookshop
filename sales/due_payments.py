"""Atomic due collection; retrying one request never collects twice."""
import hashlib
import json

from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Round
from rest_framework import serializers
from rest_framework.exceptions import APIException

from accounts.models import CashTransaction, DailyCash
from .models import Customer, DuePaymentSubmission, Payment, Sale
from .serializers import CustomerSerializer, SaleSerializer


class DuePaymentInput(serializers.Serializer):
    request_id = serializers.UUIDField(required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    expected_due = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    method = serializers.ChoiceField(choices=['cash', 'bkash', 'nagad', 'bank', 'mobile'], default='cash')

    def validate(self, data):
        if data['amount'] + data['discount'] <= 0:
            raise serializers.ValidationError('পরিমাণ অথবা ছাড় দিন।')
        return data


class PaymentRequestConflict(APIException):
    status_code = 409
    default_detail = 'এই পেমেন্টের তথ্য বদলে গেছে। আগের জমার ফল আগে যাচাই করুন।'


def _start(raw, kind, target_id):
    serializer = DuePaymentInput(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    request_id = data.pop('request_id', None)
    if request_id is None:
        return data, None
    normalized = {'amount': format(data['amount'], '.2f'), 'discount': format(data['discount'], '.2f'), 'method': data['method']}
    if 'expected_due' in data:
        normalized['expected_due'] = format(data['expected_due'], '.2f')
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    attempt, _ = DuePaymentSubmission.objects.get_or_create(
        request_id=request_id,
        defaults={'target_kind': kind, 'target_id': target_id, 'payload_hash': digest},
    )
    attempt = DuePaymentSubmission.objects.select_for_update().get(pk=request_id)
    if (attempt.target_kind, attempt.target_id, attempt.payload_hash) != (kind, target_id, digest):
        raise PaymentRequestConflict()
    return data, attempt


def _finish(attempt, result):
    if attempt:
        attempt.result = result
        attempt.save(update_fields=['result'])
    return result


@transaction.atomic
def collect_invoice_due(sale_id, raw):
    data, attempt = _start(raw, 'sale', sale_id)
    if attempt and attempt.result is not None:
        return attempt.result
    sale = Sale.objects.select_for_update().get(pk=sale_id)
    amount, discount, method = data['amount'], data['discount'], data['method']
    delta = amount + discount
    if 'expected_due' in data and data['expected_due'] != sale.due_amount:
        raise serializers.ValidationError('এই বিলের বাকি বদলেছে। তালিকা থেকে আবার পেমেন্ট নিন।')
    if delta > sale.due_amount:
        raise serializers.ValidationError('পরিমাণ ও ছাড় মিলিয়ে মোট বাকির বেশি হতে পারবে না।')
    due = sale.due_amount - delta
    status = 'paid' if due == 0 else 'partial' if sale.paid_amount + amount > 0 else 'due'
    # The conditional write also protects SQLite, which ignores SELECT FOR UPDATE.
    changed = Sale.objects.filter(pk=sale.pk, due_amount=sale.due_amount).update(
        discount=Round(F('discount') + discount, 2), total=Round(F('total') - discount, 2),
        paid_amount=Round(F('paid_amount') + amount, 2), due_amount=Round(F('due_amount') - delta, 2), status=status,
    )
    if changed != 1:
        raise serializers.ValidationError('এই বিলের বাকি বদলে গেছে। আবার যাচাই করুন।')
    if sale.customer_id:
        changed = Customer.objects.filter(pk=sale.customer_id, total_due__gte=delta).update(total_due=Round(F('total_due') - delta, 2))
        if changed != 1:
            raise serializers.ValidationError('ক্রেতার মোট বাকি ও বিলের বাকি মিলছে না। হিসাব যাচাই করুন।')
    payment = Payment.objects.create(sale=sale, amount=amount, discount=discount, method=method)
    # Only Payment's signal creates cash; this is a note update, never a second receipt.
    if amount > 0 and method == 'cash':
        note = f'বাকি আদায়: {sale.customer_name} — ইনভয়েস #{sale.invoice_number}'
        if discount > 0:
            note += f' [ছাড় ৳{discount}]'
        CashTransaction.objects.filter(reference_id=f'pay_{payment.pk}').update(note=note)
    sale.refresh_from_db()
    return _finish(attempt, SaleSerializer(sale).data)


@transaction.atomic
def collect_customer_due(customer_id, raw):
    data, attempt = _start(raw, 'customer', customer_id)
    if attempt and attempt.result is not None:
        return attempt.result
    if data['method'] != 'cash':
        raise serializers.ValidationError('ওপেনিং বাকি আদায়ের এই ফর্মে শুধু হাতে পাওয়া নগদ জমা হবে।')
    customer = Customer.objects.select_for_update().get(pk=customer_id)
    amount, discount = data['amount'], data['discount']
    invoice_due = customer.sales.filter(due_amount__gt=0).aggregate(total=Sum('due_amount'))['total'] or 0
    delta = amount + discount
    if 'expected_due' in data and data['expected_due'] != customer.total_due - invoice_due:
        raise serializers.ValidationError('এই ক্রেতার বাকি বদলেছে। তালিকা থেকে আবার পেমেন্ট নিন।')
    # Do not accidentally collect an invoice again through the opening-due form.
    if delta > customer.total_due - invoice_due:
        raise serializers.ValidationError('পরিমাণ ও ছাড় ইনভয়েসের বাইরের বর্তমান বাকির বেশি হতে পারবে না।')
    changed = Customer.objects.filter(pk=customer_id, total_due__gte=invoice_due + delta).update(total_due=Round(F('total_due') - delta, 2))
    if changed != 1:
        raise serializers.ValidationError('ক্রেতার বাকি বদলে গেছে। আবার যাচাই করুন।')
    if amount > 0:
        from uuid import uuid4
        reference = attempt.pk if attempt else uuid4()
        note = f'বাকি আদায় (ওপেনিং ব্যালেন্স): {customer.name}'
        if discount > 0:
            note += f' [ছাড় ৳{discount}]'
        CashTransaction.objects.create(
            daily_cash=DailyCash.get_for_today(), transaction_type='sale', amount=amount,
            note=note, reference_id=f'customer_due_{reference}',
        )
    customer.refresh_from_db()
    return _finish(attempt, CustomerSerializer(customer).data)
