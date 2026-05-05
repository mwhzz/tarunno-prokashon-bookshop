from django.db import models
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
    def get_for_today(cls):
        today = timezone.now().date()
        obj, created = cls.objects.get_or_create(date=today)
        if created:
            # আগের দিনের ক্লোজিং ব্যালেন্স খুঁজুন
            last = cls.objects.filter(date__lt=today).order_by('-date').first()
            if last:
                obj.opening_balance = last.closing_balance
                obj.save()
        return obj

    def update_closing_balance(self):
        # ওপেনিং + সব জমা - সব খরচ = ক্লোজিং
        total_in = self.transactions.filter(
            transaction_type__in=['sale', 'cash_in', 'adjustment_in']
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        total_out = self.transactions.filter(
            transaction_type__in=['expense', 'cash_out', 'adjustment_out']
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        self.closing_balance = self.opening_balance + total_in - total_out
        self.save()

class CashTransaction(models.Model):
    """ক্যাশ লেজার এন্ট্রি"""
    TYPE_CHOICES = [
        ('sale', 'Sale (বিক্রয়)'),
        ('cash_in', 'Cash In (মালিকের জমা/অন্যান্য)'),
        ('expense', 'Expense (খরচ)'),
        ('cash_out', 'Cash Out (টাকা উত্তোলন)'),
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
