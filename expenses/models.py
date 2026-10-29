from django.db import models

class ExpenseCategory(models.Model):
    """ব্যয়ের খাত (যেমন: ভাড়া, বিদ্যুৎ বিল)"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Expense(models.Model):
    """ব্যয়ের হিসাব"""
    category = models.ForeignKey(ExpenseCategory, on_delete=models.CASCADE, related_name='expenses')
    ACCOUNT_CHOICES = [
        ('cash', 'Cash'),
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('bank', 'Bank'),
    ]
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.CharField(max_length=20, choices=ACCOUNT_CHOICES, default='cash', help_text="কোন অ্যাকাউন্ট থেকে খরচ হয়েছে")
    note = models.TextField(blank=True)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.category.name} - {self.amount}"
