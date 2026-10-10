from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def move_counted_cash_to_opening(apps, schema_editor):
    Daily = apps.get_model('accounts', 'DailyCash')
    Cash = apps.get_model('accounts', 'CashTransaction')
    alias = schema_editor.connection.alias
    source = Cash.objects.using(alias).select_for_update().filter(
        pk=514, daily_cash_id=60, daily_cash__date='2026-10-10',
        transaction_type='adjustment_in', amount=Decimal('1966.67'),
        note__startswith='হাতে গুনে নগদ ৳৩,৫২৩.০০ আছে—মালিকের নিশ্চিত নির্দেশ অনুযায়ী নগদ সমন্বয়',
        reference_id__isnull=True,
    ).first()
    if not source:
        return
    day = Daily.objects.using(alias).select_for_update().get(pk=source.daily_cash_id)
    if day.opening_balance != Decimal('1556.33'):
        raise RuntimeError('Counted-cash opening changed; review before moving the adjustment.')
    original_note = source.note
    source.amount = Decimal('0.00')
    source.reference_id = 'opening_count_repair_20261010_514'
    source.note = ('মালিকের নির্দেশে ওপেনিং ব্যালেন্স ৳৩,৫২৩.০০ করা হয়েছে। '
                   'এই এন্ট্রির আগের ৳১,৯৬৬.৬৭ ওপেনিংয়ে স্থানান্তর; এখানে পুনরায় নগদ যোগ হবে না। '
                   'আগের নোট: ' + original_note)
    source.save(using=alias, update_fields=['amount', 'reference_id', 'note'])
    day.opening_balance = Decimal('3523.00')
    previous_old = previous_new = None
    for current in Daily.objects.using(alias).filter(date__gte=day.date).order_by('date'):
        old = current.closing_balance
        if current.pk == day.pk:
            current.opening_balance = day.opening_balance
        elif previous_old != previous_new and current.opening_balance == previous_old:
            current.opening_balance = previous_new
        else:
            break
        rows = Cash.objects.using(alias).filter(daily_cash_id=current.pk)
        incoming = rows.filter(transaction_type__in=['sale', 'cash_in', 'adjustment_in']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        outgoing = rows.filter(transaction_type__in=['expense', 'cash_out', 'purchase', 'adjustment_out']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        current.closing_balance = current.opening_balance + incoming - outgoing
        current.save(using=alias, update_fields=['opening_balance', 'closing_balance'])
        previous_old, previous_new = old, current.closing_balance


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_reconcile_duplicate_payment_offsets')]
    operations = [migrations.RunPython(move_counted_cash_to_opening, migrations.RunPython.noop)]
