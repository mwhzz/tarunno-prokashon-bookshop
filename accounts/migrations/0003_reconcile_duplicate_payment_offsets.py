from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


# Audited production entries: staff explicitly recorded these withdrawals to
# cancel duplicate due-payment cash receipts. Migration 0002 removed the duplicate
# receipts; these compensating withdrawals must now be reversed as well.
# Match all source details and payment references, never a note keyword alone.
OFFSETS = [
    (57, '2026-08-04', 'adjustment_out', '5600.00',
     'software error er karone 2 bar payment dekhaise', ['pay_23']),
    (96, '2026-08-08', 'adjustment_out', '5472.00',
     'একি বিল 2বার দেখাচ্ছে একটি বিলের মাইনাস দেখাচ্ছি', ['pay_44']),
    (139, '2026-08-12', 'cash_out', '3178.00',
     'ডাবল পেমেন্ট দেখাচ্ছে তাই ক্যাশ আউট দেখিয়েছি', ['pay_64', 'pay_65', 'pay_67', 'pay_68', 'pay_69']),
    (225, '2026-08-20', 'cash_out', '170.00',
     'ডাবল পেমেন্ট', ['pay_125', 'pay_126']),
    (312, '2026-09-02', 'cash_out', '8643.00',
     'ডাবল পেমেন্টে উঠেছে', ['pay_174', 'pay_175', 'pay_176']),
    (480, '2026-10-03', 'cash_out', '759.00',
     'ডাবল পেমেন্ট দেখাচ্চে', ['pay_316']),
    (492, '2026-10-05', 'cash_out', '2632.70',
     'ডাভল সেল উঠেছে', ['pay_325']),
]


def reconcile_offsets(apps, schema_editor):
    Cash = apps.get_model('accounts', 'CashTransaction')
    Daily = apps.get_model('accounts', 'DailyCash')
    alias = schema_editor.connection.alias
    affected = set()
    reversals = []
    for pk, date, kind, amount, note, references in OFFSETS:
        original = Cash.objects.using(alias).filter(
            pk=pk, daily_cash__date=date, transaction_type=kind,
            amount=Decimal(amount), note=note, reference_id__isnull=True,
        ).first()
        repair_ref = f'cash_offset_repair_20261010_{pk}'
        if not original or Cash.objects.using(alias).filter(reference_id=repair_ref).exists():
            continue
        receipts = Cash.objects.using(alias).filter(
            daily_cash_id=original.daily_cash_id, transaction_type='sale',
            reference_id__in=references,
        )
        if set(receipts.values_list('reference_id', flat=True)) != set(references):
            continue
        legacy_refs = [ref.replace('pay_', 'sale_payment_', 1) for ref in references]
        if Cash.objects.using(alias).filter(reference_id__in=legacy_refs).exists():
            continue
        reversals.append(Cash(
            daily_cash_id=original.daily_cash_id, transaction_type='adjustment_in',
            amount=original.amount, reference_id=repair_ref,
            note=f'হিসাব সংশোধন: ডুপ্লিকেট পেমেন্ট বাতিল হয়েছে; শুধু সেটি সমন্বয়ের ক্যাশ আউট #{pk} (৳{amount}) ফেরত যোগ। মূল এন্ট্রি অক্ষত।',
        ))
        affected.add(original.daily_cash_id)
    # Keep the original entries, with an explicit, repeat-safe reversal trail.
    Cash.objects.using(alias).bulk_create(reversals)
    previous_old = previous_new = None
    for day in Daily.objects.using(alias).order_by('date').iterator():
        old_closing = day.closing_balance
        carried = previous_old is not None and previous_old != previous_new and day.opening_balance == previous_old
        if carried:
            day.opening_balance = previous_new
        if day.pk in affected or carried:
            rows = Cash.objects.using(alias).filter(daily_cash_id=day.pk)
            incoming = rows.filter(transaction_type__in=['sale', 'cash_in', 'adjustment_in']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            outgoing = rows.filter(transaction_type__in=['expense', 'cash_out', 'purchase', 'adjustment_out']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            day.closing_balance = day.opening_balance + incoming - outgoing
            day.save(using=alias, update_fields=['opening_balance', 'closing_balance'])
        previous_old, previous_new = old_closing, day.closing_balance


class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_remove_duplicate_due_cash')]
    operations = [migrations.RunPython(reconcile_offsets, migrations.RunPython.noop)]
