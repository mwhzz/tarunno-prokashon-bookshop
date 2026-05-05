from django.db.models.signals import post_save
from django.dispatch import receiver
from sales.models import Payment
from expenses.models import Expense
from .models import DailyCash, CashTransaction

@receiver(post_save, sender=Payment)
def record_payment_cash(sender, instance, created, **kwargs):
    """ইনভয়েস পেমেন্ট হলে ক্যাশ লেজারে যোগ করুন"""
    if created and instance.method == 'cash':
        daily_cash = DailyCash.get_for_today()
        CashTransaction.objects.create(
            daily_cash=daily_cash,
            transaction_type='sale',
            amount=instance.amount,
            note=f"Sale Payment for #{instance.sale.invoice_number}",
            reference_id=f"pay_{instance.id}"
        )

@receiver(post_save, sender=Expense)
def record_expense_cash(sender, instance, created, **kwargs):
    """খরচ হলে ক্যাশ লেজার থেকে বিয়োগ করুন"""
    if created:
        daily_cash = DailyCash.get_for_today()
        CashTransaction.objects.create(
            daily_cash=daily_cash,
            transaction_type='expense',
            amount=instance.amount,
            note=f"Expense: {instance.category.name} - {instance.note}",
            reference_id=f"exp_{instance.id}"
        )
