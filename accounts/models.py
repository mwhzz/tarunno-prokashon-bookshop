from django.db import models, transaction
from django.utils import timezone
from django.db.models import Sum

class DailyCash(models.Model):
    """প্রতিদিনের ক্যাশ ব্যালেন্স রেকর্ড"""
    date = models.DateField(unique=True, default=timezone.now)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_closed = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Cash for {self.date}"

    @classmethod
    @transaction.atomic
    def get_for_today(cls):
        today = timezone.localdate()
        last = cls.objects.filter(date__lt=today).order_by('-date').first()
        opening = last.closing_balance if last else 0
        obj, _ = cls.objects.get_or_create(
            date=today, defaults={'opening_balance': opening, 'closing_balance': opening},
        )
        return obj

    @transaction.atomic
    def update_closing_balance(self):
        # A correction to an earlier day must reach subsequent carried balances.
        # Read the stored opening: a transaction can hold an outdated related object.
        days = list(type(self).objects.select_for_update().filter(date__gte=self.date).order_by('date'))
        previous_old = previous_new = None
        for index, day in enumerate(days):
            old_closing = day.closing_balance
            if index:
                if day.opening_balance != previous_old:
                    break  # Preserve an independently entered opening balance.
                day.opening_balance = previous_new
            total_in = day.transactions.filter(
                transaction_type__in=['sale', 'cash_in', 'adjustment_in'],
            ).aggregate(total=Sum('amount'))['total'] or 0
            total_out = day.transactions.filter(
                transaction_type__in=['expense', 'cash_out', 'purchase', 'adjustment_out'],
            ).aggregate(total=Sum('amount'))['total'] or 0
            day.closing_balance = day.opening_balance + total_in - total_out
            type(self).objects.filter(pk=day.pk).update(
                opening_balance=day.opening_balance, closing_balance=day.closing_balance,
            )
            if not index:
                self.opening_balance, self.closing_balance = day.opening_balance, day.closing_balance
            previous_old, previous_new = old_closing, day.closing_balance
            if previous_old == previous_new:
                break

ACCOUNT_CHOICES = [
    ('cash', 'ক্যাশ'),
    ('bkash', 'বিকাশ'),
    ('nagad', 'নগদ (Nagad)'),
    ('bank', 'ব্যাংক'),
    ('mobile', 'মোবাইল ব্যাংকিং (পুরনো)'),
]


class MoneyMovement(models.Model):
    """অ্যাকাউন্টের মধ্যে টাকা সরানো বা ব্যালেন্স সমন্বয়। ক্যাশের অংশ CashTransaction-এও বসে
    (reference_id = money_<id>), যাতে দৈনিক ক্যাশ লেজার মিলে থাকে।"""
    KIND_CHOICES = [
        ('transfer', 'ট্রান্সফার'),
        ('adjustment', 'ব্যালেন্স সমন্বয়'),
        ('collection', 'পুরনো বাকি আদায়'),
    ]
    request_id = models.UUIDField(unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    from_account = models.CharField(max_length=20, choices=ACCOUNT_CHOICES, blank=True)
    to_account = models.CharField(max_length=20, choices=ACCOUNT_CHOICES, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="ট্রান্সফারের চার্জ — খরচ হিসেবে বসে")
    fee_expense = models.OneToOneField('expenses.Expense', on_delete=models.SET_NULL, null=True, blank=True, related_name='money_movement')
    note = models.TextField(blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_kind_display()} {self.from_account or '-'} → {self.to_account or '-'} ৳{self.amount}"


class CashTransaction(models.Model):
    """ক্যাশ লেজার এন্ট্রি"""
    TYPE_CHOICES = [
        ('sale', 'Sale (বিক্রয়)'),
        ('cash_in', 'Cash In (মালিকের জমা/অন্যান্য)'),
        ('expense', 'Expense (খরচ)'),
        ('cash_out', 'Cash Out (টাকা উত্তোলন)'),
        ('purchase', 'Purchase Payment (ক্রয় বিল পেমেন্ট)'),
        ('adjustment_in', 'Adjustment (+)'),
        ('adjustment_out', 'Adjustment (-)'),
    ]
    daily_cash = models.ForeignKey(DailyCash, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # রেফারেন্স (যেমন: Sale ID বা Expense ID)
    reference_id = models.CharField(max_length=50, blank=True, null=True) 

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # লেনদেনের পর ক্লোজিং ব্যালেন্স আপডেট করুন
        self.daily_cash.update_closing_balance()
