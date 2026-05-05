from django.db import models


class Group(models.Model):
    """বইয়ের ক্যাটাগরি / বিষয় / সিরিজ"""
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Book(models.Model):
    """বইয়ের মূল তথ্য"""
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='books'
    )
    title = models.CharField(max_length=500)
    product_code = models.CharField(max_length=100, unique=True, blank=True, null=True, help_text="Product Code")
    author = models.CharField(max_length=300, blank=True)
    publisher = models.CharField(max_length=300, blank=True)
    isbn = models.CharField(max_length=20, blank=True, unique=True, null=True)
    edition = models.CharField(max_length=50, blank=True)
    
    image = models.ImageField(upload_to='books/', blank=True, null=True)
    BOOK_TYPE_CHOICES = [
        ('single', 'Single Book'),
        ('series', 'Set (Series)'),
    ]
    book_type = models.CharField(max_length=20, choices=BOOK_TYPE_CHOICES, default='single')

    # মূল্য
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="বইয়ের গায়ে থাকা দাম")
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0) # Buy Price
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0) # Selling Price
    
    # কমিশন এবং ডিসকাউন্ট
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    DISCOUNT_TYPE_CHOICES = [
        ('amount', 'টাকা'),
        ('percentage', '%'),
    ]
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='amount')

    # অতিরিক্ত
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    @property
    def current_stock(self):
        from stock.models import StockSummary
        try:
            return self.stock_summary.quantity
        except StockSummary.DoesNotExist:
            return 0
