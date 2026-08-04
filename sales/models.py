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

    @property
    def total_discount(self):
        """POS থেকে বিক্রয় করলে ছাড় সাধারণত প্রতিটা লাইন-আইটেমেই বসানো হয় (Sale.discount
        তখন ০ থাকে, নাহলে total-এ দ্বিগুণ বিয়োগ হয়ে যেত) — তাই প্রিন্ট করা ইনভয়েসে
        "মোট ছাড়" দেখানোর সময় শুধু Sale.discount না, আইটেমগুলোর ছাড়ও যোগ করে দেখাতে হয়।"""
        item_discount = self.items.aggregate(total=Sum('discount'))['total'] or 0
        return self.discount + item_discount

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
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('bank', 'Bank'),
        ('mobile', 'Mobile Banking'),
        ('credit', 'Credit'),
    ]
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="এই পেমেন্টের সাথে দেওয়া বাড়তি ছাড় (ইনভয়েস ক্লোজ করার জন্য)")
    # bাকি বিল (due_list.html) থেকে বকেয়া তোলার সময় bKash/Nagad আলাদা অপশন হিসেবে
    # পাঠানো হয়, তাই এখানে Sale.PAYMENT_CHOICES (যেটা POS-এর মূল বিক্রয়ের পেমেন্ট
    # মাধ্যমের জন্য — cash/bank/mobile/credit) না ব্যবহার করে দুটোরই সুপারসেট রাখা হয়েছে।
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
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


class ExternalTrade(models.Model):
    """
    বাইরে থেকে ক্যাশ দিয়ে বই কিনে সরাসরি গ্রাহকের কাছে বিক্রি করার হিসাব।
    এই বইগুলো নিজের দোকানের স্টক/ইনভেন্টরিতে (Book/StockSummary) যোগ হয় না —
    শুধু ক্যাশ লেজারে টাকার আসা-যাওয়া এবং লাভ-ক্ষতির হিসাব রাখা হয়।
    """
    STATUS_CHOICES = [
        ('pending', 'বিক্রয় বাকি'),
        ('sold', 'বিক্রি সম্পন্ন'),
    ]

    book_title = models.CharField(max_length=500)
    author = models.CharField(max_length=300, blank=True)
    publisher = models.CharField(max_length=300, blank=True, help_text="প্রকাশনীর নাম")
    quantity = models.PositiveIntegerField(default=1)

    # প্রকাশনীর নির্ধারিত বডি রেট (কভার প্রাইস) এবং সেখান থেকে কত % কমিশনে কেনা/বেচা
    # হয়েছে — শুধু হিসাব-নিকাশ ও রিপোর্টিং-এর জন্য রাখা হয়, প্রকৃত ক্যাশ হিসাব সবসময়
    # purchase_price/selling_price থেকেই হয়।
    body_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="প্রকাশনীর বডি রেট/কভার প্রাইস")
    purchase_commission = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="বডি রেট থেকে কত % কমিশনে কেনা হয়েছে")
    sale_commission = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="বডি রেট থেকে কত % কমিশনে বিক্রি হয়েছে/হবে")

    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="প্রতি কপি ক্রয়মূল্য (নগদ)")
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="প্রতি কপি বিক্রয়মূল্য")

    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sold_at = models.DateTimeField(null=True, blank=True)

    # যদি পরে সিদ্ধান্ত নেওয়া হয় এই বইটি স্থায়ীভাবে নিজের ক্যাটালগ/স্টকে রাখা হবে —
    # এই ট্রেড রেকর্ডটি তখনও আলাদা ইতিহাস হিসেবে থেকে যায়, শুধু কোন Book হিসেবে
    # যোগ হলো তার লিঙ্ক রাখা হয় যাতে দুইবার যোগ না হয়।
    converted_book = models.ForeignKey(
        'books.Book', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='external_trade_origins'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.book_title} x{self.quantity} ({self.get_status_display()})"

    @property
    def total_purchase(self):
        return self.purchase_price * self.quantity

    @property
    def total_sale(self):
        return self.selling_price * self.quantity

    @property
    def profit(self):
        return self.total_sale - self.total_purchase


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
