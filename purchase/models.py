from django.conf import settings
from django.db import models
from django.utils import timezone


class Vendor(models.Model):
    """যাদের কাছ থেকে বই কেনা হয় (প্রকাশনী/এজেন্ট/ডিলার)"""
    name = models.CharField(max_length=300)
    phone = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    vendor_code = models.CharField(max_length=20, unique=True, blank=True, null=True)

    opening_due = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="শুরুর বকেয়া")
    total_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.vendor_code or self.phone or 'N/A'})"

    def save(self, *args, **kwargs):
        if not self.vendor_code:
            self.vendor_code = self._generate_vendor_code()
        super().save(*args, **kwargs)

    def _generate_vendor_code(self):
        last = Vendor.objects.filter(vendor_code__isnull=False).order_by('-id').first()
        if last and last.vendor_code and last.vendor_code.startswith('V-'):
            try:
                num = int(last.vendor_code.split('-')[1]) + 1
            except (IndexError, ValueError):
                num = 1
        else:
            num = 1
        return f"V-{num:04d}"


class PurchaseBill(models.Model):
    """ভেন্ডরের কাছ থেকে একটি ক্রয় বিল (একাধিক বই থাকতে পারে)"""
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('due', 'Due'),
        ('partial', 'Partial'),
    ]
    ACCOUNT_CHOICES = [
        ('cash', 'Cash'),
        ('bank', 'Bank'),
    ]
    LOCATION_CHOICES = [
        ('godown', 'Godown (গুদাম)'),
        ('shop', 'Shop (দোকান)'),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='bills')
    vendor_name = models.CharField(max_length=300, blank=True)
    memo_number = models.CharField(max_length=50, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    account_name = models.CharField(max_length=20, choices=ACCOUNT_CHOICES, default='cash')
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, default='godown', help_text="বই কোথায় স্টকে যোগ হয়েছে")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='paid')
    purchase_date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_bills'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # পুরনো External Trade রেকর্ড থেকে মাইগ্রেট করা হলে লিংক থাকে (ইতিহাস ট্র্যাক করা এবং
    # migration দুইবার না চালানোর জন্য)
    legacy_external_trade = models.OneToOneField(
        'sales.ExternalTrade', on_delete=models.SET_NULL, null=True, blank=True, related_name='migrated_bill'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Purchase Bill #{self.id} - {self.vendor_name}"

    def books_summary(self, limit=3):
        """ক্যাশ লেজার নোটে দেখানোর জন্য এই বিলের বইগুলোর সংক্ষিপ্ত তালিকা"""
        titles = list(self.items.values_list('book_title', flat=True)[:limit])
        remaining = self.items.count() - len(titles)
        summary = ', '.join(titles)
        if remaining > 0:
            summary += f" +আরও {remaining}টি"
        return summary

    def save(self, *args, **kwargs):
        self.due_amount = self.total - self.paid_amount
        if self.due_amount <= 0:
            self.status = 'paid'
        elif self.paid_amount > 0:
            self.status = 'partial'
        else:
            self.status = 'due'
        super().save(*args, **kwargs)


class VendorPayment(models.Model):
    """একটি ক্রয় বিলের বকেয়ার বিপরীতে জমা দেওয়া পেমেন্টের রেকর্ড (নগদ, বিকাশ, ব্যাংক ইত্যাদি)"""
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('bank', 'Bank'),
    ]
    bill = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.amount} via {self.method} for Bill #{self.bill_id}"


class PurchaseBillItem(models.Model):
    """একটি ক্রয় বিলের প্রতিটি বই"""
    bill = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='items')
    book = models.ForeignKey('books.Book', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_items')
    book_title = models.CharField(max_length=500)

    quantity = models.PositiveIntegerField(default=1)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="প্রতি কপি ক্রয়মূল্য")
    commission = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="বিক্রয় কমিশন %")
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.total = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.book_title} x {self.quantity}"
