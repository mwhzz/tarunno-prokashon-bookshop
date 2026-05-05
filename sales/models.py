from django.db import models
from books.models import Book
from django.db.models import Sum


class Customer(models.Model):
    """গ্রাহকের তথ্য এবং ব্যালেন্স"""
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    total_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="শুরুর বকেয়া")
    
    # গ্রাহক কোড (যেমন: TP-001)
    customer_code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    
    TYPE_CHOICES = [
        ('retail', 'খুচরা (Retail)'),
        ('wholesale', 'পাইকারি (Wholesale)'),
    ]
    customer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='retail')
    default_commission = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Default discount %")
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.customer_code or self.phone})"

    def save(self, *args, **kwargs):
        if not self.customer_code and self.customer_type == 'wholesale':
            self.customer_code = self._generate_customer_code()
        super().save(*args, **kwargs)

    def _generate_customer_code(self):
        last = Customer.objects.filter(customer_type='wholesale', customer_code__isnull=False).order_by('-id').first()
        if last and last.customer_code and last.customer_code.startswith('C-'):
            try:
                num = int(last.customer_code.split('-')[1]) + 1
            except: num = 1
        else:
            num = 1
        return f"C-{num:04d}"


class Sale(models.Model):
    """একটি বিক্রয় ইনভয়েস"""
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('due', 'Due'),
        ('partial', 'Partial'),
    ]
    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('bank', 'Bank Transfer'),
        ('mobile', 'Mobile Banking'),
        ('credit', 'Credit'),
    ]

    invoice_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    customer_name = models.CharField(max_length=200, blank=True, default='Walk-in Customer')
    customer_phone = models.CharField(max_length=20, blank=True)
    
    SALE_TYPE_CHOICES = [
        ('retail', 'Retail'),
        ('wholesale', 'Wholesale'),
    ]
    sale_type = models.CharField(max_length=20, choices=SALE_TYPE_CHOICES, default='retail')
    
    # পূর্বের বাকি (Previous Due)
    previous_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # হিসাব
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # অতিরিক্ত চার্জ (Extra Charges)
    packaging_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    courier_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    transaction_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Total buy price of all items")
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='paid')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Invoice #{self.invoice_number} - {self.customer_name}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self._generate_invoice_number()
        # due হিসাব
        self.due_amount = self.total - self.paid_amount
        if self.due_amount <= 0:
            self.status = 'paid'
        elif self.paid_amount > 0:
            self.status = 'partial'
        else:
            self.status = 'due'
        super().save(*args, **kwargs)

    def _generate_invoice_number(self):
        from django.utils import timezone
        today = timezone.now()
        prefix = f"INV-{today.strftime('%Y%m%d')}"
        last = Sale.objects.filter(
            invoice_number__startswith=prefix
        ).order_by('-invoice_number').first()
        if last:
            num = int(last.invoice_number.split('-')[-1]) + 1
        else:
            num = 1
        return f"{prefix}-{num:04d}"

    @property
    def total_profit(self):
        """সর্বমোট সম্ভাব্য লাভ (পুরো বিল পরিশোধ হলে)"""
        return self.total - self.total_cost

    @property
    def realized_profit(self):
        """আদায়কৃত লাভ (পেইড অ্যামাউন্ট অনুযায়ী)"""
        if self.total <= 0: return 0
        ratio = self.paid_amount / self.total
        return self.total_profit * ratio


class Payment(models.Model):
    """ইনভয়েসের পেমেন্ট রেকর্ড (নগদ, বিকাশ, ব্যাংক ইত্যাদি)"""
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Sale.PAYMENT_CHOICES, default='cash')
    transaction_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.amount} via {self.method} for #{self.sale.invoice_number}"


class SaleReturn(models.Model):
    """কাস্টমার বই ফেরত দিলে তার রেকর্ড"""
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='returns')
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Return: {self.book.title} x {self.quantity} for Invoice #{self.sale.invoice_number}"


class SaleItem(models.Model):
    """একটি ইনভয়েসের প্রতিটি বই"""
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='sale_items')
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.total = (self.unit_price * self.quantity) - self.discount
        super().save(*args, **kwargs)

    @property
    def profit(self):
        return self.total - (self.cost_price * self.quantity)

    def __str__(self):
        return f"{self.book.title} x {self.quantity}"
