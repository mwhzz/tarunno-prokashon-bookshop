from django.db import models
from books.models import Book


class StockEntry(models.Model):
    """প্রতিটি স্টক যোগের রেকর্ড"""
    SOURCE_CHOICES = [
        ('purchase', 'Purchase'),
        ('return', 'Customer Return'),
        ('adjustment', 'Manual Adjustment'),
        ('damage', 'Damage/Expired'),
    ]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='stock_entries')
    quantity = models.IntegerField()  # positive = in, negative = out
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='purchase')
    
    # New fields for Purchase System
    supplier_name = models.CharField(max_length=200, blank=True, help_text="Supplier name if source is purchase")
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="New buy price per unit")
    
    reference_id = models.IntegerField(null=True, blank=True)  # sale or purchase id
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    LOCATION_CHOICES = [
        ('godown', 'Godown (গুদাম)'),
        ('shop', 'Shop (দোকান)'),
    ]
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, default='godown')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        direction = "+" if self.quantity > 0 else ""
        return f"{self.book.title}: {direction}{self.quantity} ({self.get_location_display()})"


class StockSummary(models.Model):
    """প্রতিটি বইয়ের বর্তমান স্টক (ক্যাশড ভ্যালু)"""
    book = models.OneToOneField(Book, on_delete=models.CASCADE, related_name='stock_summary')
    godown_quantity = models.IntegerField(default=0)
    shop_quantity = models.IntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)

    @property
    def quantity(self):
        return self.godown_quantity + self.shop_quantity

    def __str__(self):
        return f"{self.book.title}: Shop={self.shop_quantity}, Godown={self.godown_quantity}"

    @classmethod
    def update_stock(cls, book, quantity_change, location='godown'):
        """স্টক আপডেট করুন (atomic)"""
        from django.db import transaction
        with transaction.atomic():
            summary, created = cls.objects.select_for_update().get_or_create(
                book=book, defaults={'godown_quantity': 0, 'shop_quantity': 0}
            )
            if location == 'godown':
                summary.godown_quantity += quantity_change
            elif location == 'shop':
                summary.shop_quantity += quantity_change
            summary.save()
            return summary


class StockTransfer(models.Model):
    """গোডাউন এবং দোকানের মধ্যে স্টক ট্রান্সফার"""
    LOCATION_CHOICES = [
        ('godown', 'Godown (গুদাম)'),
        ('shop', 'Shop (দোকান)'),
    ]
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='transfers')
    quantity = models.PositiveIntegerField()
    from_location = models.CharField(max_length=20, choices=LOCATION_CHOICES)
    to_location = models.CharField(max_length=20, choices=LOCATION_CHOICES)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Transfer {self.quantity} {self.book.title} from {self.from_location} to {self.to_location}"

