"""সব অ্যাকাউন্টের (ক্যাশ/বিকাশ/নগদ/ব্যাংক) ব্যালেন্স, ট্রান্সফার ও সমন্বয়।

ক্যাশের ব্যালেন্স দৈনিক ক্যাশ লেজার (DailyCash) থেকেই আসে। বাকি অ্যাকাউন্টের ব্যালেন্স আলাদা
করে কোথাও জমা থাকে না — যেসব রেকর্ডে আগে থেকেই মাধ্যম লেখা থাকে সেগুলো থেকে হিসাব হয়:
  + বিক্রয়ের পেমেন্ট (ফেরতের টাকা ফেরত মাইনাস হিসেবে থাকে)
  − ভেন্ডরকে দেওয়া পেমেন্ট
  − খরচ
  ± ট্রান্সফার ও সমন্বয় (MoneyMovement)
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers

from expenses.models import Expense, ExpenseCategory
from purchase.models import VendorPayment
from sales.models import Payment
from .models import ACCOUNT_CHOICES, CashTransaction, DailyCash, MoneyMovement

ACCOUNT_LABELS = dict(ACCOUNT_CHOICES)
# পুরনো "মোবাইল ব্যাংকিং" শুধু টাকা সরিয়ে খালি করার জন্য; নতুন টাকা সেখানে ঢোকানো যায় না
TARGET_ACCOUNTS = ['cash', 'bkash', 'nagad', 'bank']
FEE_CATEGORY = 'লেনদেন চার্জ'
ZERO = Decimal('0')


def _sum(qs, field='amount'):
    return qs.aggregate(t=Sum(field))['t'] or ZERO


def cash_balance():
    day = DailyCash.get_for_today()
    day.update_closing_balance()
    return day.closing_balance


def account_balance(account):
    if account == 'cash':
        return cash_balance()
    moved_in = _sum(MoneyMovement.objects.filter(to_account=account))
    moved_out = _sum(MoneyMovement.objects.filter(from_account=account))
    return (
        _sum(Payment.objects.filter(method=account))
        - _sum(VendorPayment.objects.filter(method=account))
        - _sum(Expense.objects.filter(account=account))
        + moved_in - moved_out
    )


def all_balances():
    rows = []
    for code, label in ACCOUNT_CHOICES:
        balance = account_balance(code)
        if code == 'mobile' and not balance:
            continue
        rows.append({'account': code, 'label': label, 'balance': balance})
    return {'accounts': rows, 'total': sum((r['balance'] for r in rows), ZERO)}


class TransferInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    from_account = serializers.ChoiceField(choices=[c for c, _ in ACCOUNT_CHOICES])
    to_account = serializers.ChoiceField(choices=TARGET_ACCOUNTS)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    fee = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=ZERO, default=ZERO)
    note = serializers.CharField(allow_blank=True, default='')

    def validate(self, data):
        if data['from_account'] == data['to_account']:
            raise serializers.ValidationError('একই অ্যাকাউন্টে ট্রান্সফার করা যায় না।')
        return data


class AdjustInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    account = serializers.ChoiceField(choices=[c for c, _ in ACCOUNT_CHOICES])
    actual_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    note = serializers.CharField(allow_blank=True, default='')


def _existing(request_id):
    """একই ফর্ম দুইবার জমা হলে (ডাবল-ক্লিক/নেট সমস্যা) দ্বিতীয়বার নতুন এন্ট্রি হয় না"""
    return MoneyMovement.objects.filter(request_id=request_id).first()


def _open_cash_day():
    day = DailyCash.objects.select_for_update().get(pk=DailyCash.get_for_today().pk)
    if day.is_closed:
        raise serializers.ValidationError('আজকের ক্যাশ হিসাব ক্লোজ করা হয়েছে। ক্যাশের লেনদেন আর দেওয়া যাবে না।')
    return day


def _cash_entry(day, movement, transaction_type, amount, note):
    CashTransaction.objects.create(
        daily_cash=day, transaction_type=transaction_type, amount=amount,
        note=note, reference_id=f"money_{movement.pk}",
    )


@transaction.atomic
def transfer(raw, user=None):
    serializer = TransferInput(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if existing := _existing(data['request_id']):
        return existing

    src, dst, amount, fee = data['from_account'], data['to_account'], data['amount'], data['fee']
    day = _open_cash_day() if 'cash' in (src, dst) else None
    available = account_balance(src)
    if amount + fee > available:
        raise serializers.ValidationError(
            f"{ACCOUNT_LABELS[src]}-এ আছে ৳{available}, কিন্তু চার্জসহ লাগবে ৳{amount + fee}।"
        )

    movement = MoneyMovement.objects.create(
        request_id=data['request_id'], kind='transfer', from_account=src, to_account=dst,
        amount=amount, fee=fee, note=data['note'], created_by=user if user and user.is_authenticated else None,
    )
    label = f"ট্রান্সফার: {ACCOUNT_LABELS[src]} → {ACCOUNT_LABELS[dst]}" + (f" ({data['note']})" if data['note'] else '')
    if src == 'cash':
        _cash_entry(day, movement, 'cash_out', amount, label)
    if dst == 'cash':
        _cash_entry(day, movement, 'cash_in', amount, label)
    if fee:
        # চার্জ আসল খরচ, তাই খরচের হিসাবে বসে; উৎস ক্যাশ হলে খরচের signal নিজেই ক্যাশ লেজারে বসায়
        category, _ = ExpenseCategory.objects.get_or_create(name=FEE_CATEGORY)
        movement.fee_expense = Expense.objects.create(
            category=category, amount=fee, account=src,
            note=f"{ACCOUNT_LABELS[src]} → {ACCOUNT_LABELS[dst]} ট্রান্সফারের চার্জ",
        )
        movement.save(update_fields=['fee_expense'])
    return movement


@transaction.atomic
def adjust(raw, user=None):
    serializer = AdjustInput(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if existing := _existing(data['request_id']):
        return existing

    account = data['account']
    if account == 'mobile' and data['actual_balance'] != 0:
        raise serializers.ValidationError('পুরনো মোবাইল ব্যাংকিং-এর টাকা বিকাশ বা নগদে ট্রান্সফার করুন।')
    day = _open_cash_day() if account == 'cash' else None
    current = account_balance(account)
    diff = data['actual_balance'] - current
    if not diff:
        raise serializers.ValidationError(f"{ACCOUNT_LABELS[account]}-এর ব্যালেন্স আগে থেকেই ৳{current}।")

    movement = MoneyMovement.objects.create(
        request_id=data['request_id'], kind='adjustment',
        to_account=account if diff > 0 else '', from_account=account if diff < 0 else '',
        amount=abs(diff), note=data['note'], created_by=user if user and user.is_authenticated else None,
    )
    if day:
        note = f"ব্যালেন্স সমন্বয়: ৳{current} → ৳{data['actual_balance']}" + (f" ({data['note']})" if data['note'] else '')
        _cash_entry(day, movement, 'adjustment_in' if diff > 0 else 'adjustment_out', abs(diff), note)
    return movement


def statement(account, limit=200):
    """একটা অ্যাকাউন্টের সাম্প্রতিক লেনদেন, প্রতিটার পরে ব্যালেন্স কত ছিল সহ।
    ক্যাশের পুরো হিসাব দৈনিক ক্যাশ লেজারে থাকে, তাই এটা শুধু অন্য অ্যাকাউন্টের জন্য।"""
    rows = []
    for p in Payment.objects.filter(method=account).select_related('sale'):
        refund = p.amount < 0
        rows.append({
            'at': p.created_at, 'amount': p.amount,
            'title': ('বই ফেরতের টাকা ফেরত' if refund else 'বিক্রয়ের টাকা') + f" #{p.sale.invoice_number}",
            'detail': p.sale.customer_name,
        })
    for v in VendorPayment.objects.filter(method=account).select_related('bill'):
        rows.append({'at': v.created_at, 'amount': -v.amount, 'title': f"ভেন্ডর পেমেন্ট (বিল #{v.bill_id})", 'detail': v.bill.vendor_name})
    for e in Expense.objects.filter(account=account).select_related('category'):
        rows.append({'at': e.date, 'amount': -e.amount, 'title': f"খরচ: {e.category.name}", 'detail': e.note})
    for m in MoneyMovement.objects.filter(from_account=account) | MoneyMovement.objects.filter(to_account=account):
        sign = 1 if m.to_account == account else -1
        if m.kind == 'transfer':
            title = f"ট্রান্সফার: {ACCOUNT_LABELS[m.from_account]} → {ACCOUNT_LABELS[m.to_account]}"
        else:
            title = 'ব্যালেন্স সমন্বয়'
        rows.append({'at': m.created_at, 'amount': sign * m.amount, 'title': title, 'detail': m.note})

    rows.sort(key=lambda r: r['at'], reverse=True)
    balance = account_balance(account)
    for row in rows:
        row['balance_after'] = balance
        balance -= row['amount']
    return {'account': account, 'label': ACCOUNT_LABELS[account], 'balance': account_balance(account), 'rows': rows[:limit]}
