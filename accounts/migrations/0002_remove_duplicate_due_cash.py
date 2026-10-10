from decimal import Decimal
from django.db import migrations
from django.db.models import Sum


def repair_duplicate_cash(apps, schema_editor):
    Cash = apps.get_model('accounts', 'CashTransaction')
    Daily = apps.get_model('accounts', 'DailyCash')
    Payment = apps.get_model('sales', 'Payment')
    alias = schema_editor.connection.alias
    affected = set()
    for extra in Cash.objects.using(alias).filter(reference_id__startswith='sale_payment_').iterator():
        payment_id = extra.reference_id.removeprefix('sale_payment_')
        if not payment_id.isdigit():
            continue
        payment = Payment.objects.using(alias).filter(pk=int(payment_id), method='cash', amount=extra.amount).first()
        if not payment or extra.transaction_type != 'sale':
            continue
        canonical = Cash.objects.using(alias).filter(
            reference_id=f'pay_{payment_id}', transaction_type='sale',
            amount=extra.amount, daily_cash_id=extra.daily_cash_id,
        ).first()
        if canonical:
            Cash.objects.using(alias).filter(pk=canonical.pk).update(note=extra.note)
            affected.add(extra.daily_cash_id)
            extra.delete(using=alias)
    if not affected:
        return
    # Correct affected closings and automatically carried openings only; preserve
    # independent opening balances that differ from the preceding day's closing.
    previous_old = previous_new = None
    for day in Daily.objects.using(alias).order_by('date').iterator():
        old_closing = day.closing_balance
        carry_changed = previous_old is not None and previous_old != previous_new and day.opening_balance == previous_old
        if carry_changed:
            day.opening_balance = previous_new
        if day.pk in affected or carry_changed:
            rows = Cash.objects.using(alias).filter(daily_cash_id=day.pk)
            incoming = rows.filter(transaction_type__in=['sale', 'cash_in', 'adjustment_in']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            outgoing = rows.filter(transaction_type__in=['expense', 'cash_out', 'purchase', 'adjustment_out']).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            day.closing_balance = day.opening_balance + incoming - outgoing
            day.save(using=alias, update_fields=['opening_balance', 'closing_balance'])
        previous_old, previous_new = old_closing, day.closing_balance


class Migration(migrations.Migration):
    dependencies = [('accounts', '0001_initial'), ('sales', '0015_payment_discount_alter_payment_method')]
    operations = [migrations.RunPython(repair_duplicate_cash, migrations.RunPython.noop)]
