from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from books.models import Book
from stock.models import StockEntry, StockSummary

from .models import PurchaseBill, PurchaseBillItem, Vendor, VendorPayment


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = ['id', 'name', 'phone', 'address', 'vendor_code', 'opening_due', 'total_due', 'created_at']
        read_only_fields = ['vendor_code']

    def create(self, validated_data):
        vendor = super().create(validated_data)
        if vendor.opening_due:
            vendor.total_due = vendor.opening_due
            vendor.save(update_fields=['total_due'])
        return vendor


class PurchaseBillItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseBillItem
        fields = [
            'id', 'book', 'book_title', 'quantity', 'mrp',
            'unit_price', 'commission', 'selling_price', 'total',
        ]
        read_only_fields = ['total']


class PurchaseBillItemCreateSerializer(serializers.Serializer):
    """একটা বিলের একটা লাইন — book_id দিলে পুরনো বই, না দিলে book_title দিয়ে নতুন বই তৈরি হবে।"""
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.all(), required=False, allow_null=True)
    book_title = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    author = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    quantity = serializers.IntegerField(min_value=1, default=1)
    mrp = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    commission = serializers.DecimalField(max_digits=5, decimal_places=2, default=0, min_value=0, max_value=100)
    selling_price = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)

    def validate(self, data):
        if not data.get('book') and not data.get('book_title'):
            raise serializers.ValidationError("বই সিলেক্ট করুন অথবা নতুন বইয়ের নাম লিখুন।")
        return data


class VendorPaymentSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source='get_method_display', read_only=True)

    class Meta:
        model = VendorPayment
        fields = ['id', 'amount', 'method', 'method_display', 'note', 'created_at']


class PurchaseBillSerializer(serializers.ModelSerializer):
    items = PurchaseBillItemSerializer(many=True, read_only=True)
    payments = VendorPaymentSerializer(many=True, read_only=True)
    vendor_code = serializers.CharField(source='vendor.vendor_code', read_only=True, default=None)
    vendor_phone = serializers.CharField(source='vendor.phone', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')

    class Meta:
        model = PurchaseBill
        fields = [
            'id', 'vendor', 'vendor_name', 'vendor_code', 'vendor_phone', 'memo_number',
            'subtotal', 'discount', 'total', 'paid_amount', 'due_amount',
            'account_name', 'location', 'status', 'status_display', 'purchase_date', 'note',
            'created_by_name', 'created_at', 'items', 'payments',
        ]


class PurchaseBillCreateSerializer(serializers.Serializer):
    """ভেন্ডরের কাছ থেকে একাধিক বই একসাথে কেনার বিল তৈরির জন্য"""
    LOCATION_CHOICES = [('godown', 'Godown'), ('shop', 'Shop')]

    vendor = serializers.PrimaryKeyRelatedField(queryset=Vendor.objects.all(), required=False, allow_null=True)
    vendor_name = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    memo_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0, min_value=0)
    account_name = serializers.ChoiceField(choices=PurchaseBill.ACCOUNT_CHOICES, default='cash')
    purchase_date = serializers.DateField(required=False)
    location = serializers.ChoiceField(choices=LOCATION_CHOICES, default='godown')
    note = serializers.CharField(required=False, allow_blank=True, default='')
    items = PurchaseBillItemCreateSerializer(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("কমপক্ষে একটি বই লাগবে।")
        return items

    def validate(self, data):
        if not data.get('vendor') and not data.get('vendor_name'):
            raise serializers.ValidationError("ভেন্ডর সিলেক্ট করুন অথবা নতুন ভেন্ডরের নাম লিখুন।")
        return data

    @transaction.atomic
    def create(self, validated_data):
        from accounts.models import CashTransaction, DailyCash

        items_data = validated_data.pop('items')
        request = self.context.get('request')

        vendor = validated_data.pop('vendor', None)
        vendor_name = validated_data.pop('vendor_name', '')
        if not vendor:
            vendor, _created = Vendor.objects.get_or_create(name=vendor_name)
        if not vendor_name:
            vendor_name = vendor.name

        subtotal = sum(item['unit_price'] * item['quantity'] for item in items_data)
        discount = validated_data.get('discount', 0)
        total = subtotal - discount

        bill = PurchaseBill.objects.create(
            vendor=vendor,
            vendor_name=vendor_name,
            memo_number=validated_data.get('memo_number', ''),
            subtotal=subtotal,
            discount=discount,
            total=total,
            paid_amount=validated_data.get('paid_amount', 0),
            account_name=validated_data.get('account_name', 'cash'),
            location=validated_data.get('location', 'godown'),
            purchase_date=validated_data.get('purchase_date') or timezone.now().date(),
            note=validated_data.get('note', ''),
            created_by=request.user if request and request.user.is_authenticated else None,
        )

        for item_data in items_data:
            book = item_data.get('book')
            if not book:
                book = Book.objects.create(
                    title=item_data['book_title'],
                    author=item_data.get('author', ''),
                    publisher=vendor_name,
                    source='external',
                    mrp=item_data.get('mrp', 0),
                    purchase_price=item_data['unit_price'],
                    selling_price=item_data.get('selling_price', 0),
                    commission=item_data.get('commission', 0),
                    notes=f"ক্রয় বিল থেকে যোগ করা হয়েছে (Purchase Bill #{bill.id})",
                )
            else:
                book.mrp = item_data.get('mrp', book.mrp)
                book.purchase_price = item_data['unit_price']
                if item_data.get('selling_price'):
                    book.selling_price = item_data['selling_price']
                if item_data.get('commission'):
                    book.commission = item_data['commission']
                book.save(update_fields=['mrp', 'purchase_price', 'selling_price', 'commission'])

            qty = item_data['quantity']
            PurchaseBillItem.objects.create(
                bill=bill,
                book=book,
                book_title=book.title,
                quantity=qty,
                mrp=item_data.get('mrp', 0),
                unit_price=item_data['unit_price'],
                commission=item_data.get('commission', 0),
                selling_price=item_data.get('selling_price', 0),
            )

            StockEntry.objects.create(
                book=book,
                quantity=qty,
                source='purchase',
                location=bill.location,
                purchase_price=item_data['unit_price'],
                supplier_name=vendor_name,
                reference_id=bill.id,
                note=f"Purchase Bill #{bill.id}",
            )
            StockSummary.update_stock(book, qty, location=bill.location)

        vendor.total_due += bill.due_amount
        vendor.save(update_fields=['total_due'])

        # নগদ ছাড়া অন্য মাধ্যমে (bank ইত্যাদি) দেওয়া টাকা হাতের ক্যাশ লেজারে গণনা করা যাবে না
        if bill.paid_amount > 0 and bill.account_name == 'cash':
            daily_cash = DailyCash.get_for_today()
            CashTransaction.objects.create(
                daily_cash=daily_cash,
                transaction_type='purchase',
                amount=bill.paid_amount,
                note=f"ক্রয় বিল পেমেন্ট: {vendor_name} — {bill.books_summary()} (Bill #{bill.id})",
                reference_id=f"pbill_{bill.id}",
            )

        return bill
