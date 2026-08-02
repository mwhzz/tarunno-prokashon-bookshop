from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from stock.models import StockSummary, StockEntry
from .models import Sale, SaleItem, Customer, ExternalTrade
from books.models import Book


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'address', 'customer_code', 'total_due', 'customer_type', 'default_commission', 'created_at']
        read_only_fields = ['customer_code']


class SaleItemSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source='book.title', read_only=True)

    class Meta:
        model = SaleItem
        fields = ['id', 'book', 'book_title', 'quantity', 'unit_price',
                  'cost_price', 'discount', 'total']
        read_only_fields = ['total']


class SaleItemCreateSerializer(serializers.Serializer):
    book = serializers.PrimaryKeyRelatedField(queryset=Book.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)

    def validate(self, data):
        line_total = data['unit_price'] * data['quantity']
        if data.get('discount', 0) > line_total:
            raise serializers.ValidationError("Item discount cannot be greater than item total.")
        return data


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    total_profit = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    realized_profit = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Sale
        fields = [
            'id', 'invoice_number', 'customer_name', 'customer_phone',
            'subtotal', 'discount', 
            'packaging_charge', 'courier_charge', 'transaction_fee',
            'total', 'total_cost', 'paid_amount', 'due_amount',
            'payment_method', 'status', 'note', 'total_profit', 'realized_profit',
            'items', 'created_at'
        ]


class SaleCreateSerializer(serializers.Serializer):
    """POS বিক্রয় তৈরির জন্য"""
    customer_name = serializers.CharField(max_length=200, default='Walk-in Customer')
    customer_phone = serializers.CharField(max_length=20, allow_blank=True, default='')
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    packaging_charge = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    courier_charge = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    transaction_fee = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.ChoiceField(choices=Sale.PAYMENT_CHOICES, default='cash')
    note = serializers.CharField(allow_blank=True, default='')
    sale_type = serializers.ChoiceField(choices=Sale.SALE_TYPE_CHOICES, default='retail')
    items = SaleItemCreateSerializer(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("কমপক্ষে একটি বই লাগবে।")
        return items

    def validate(self, data):
        # স্টক চেক করুন (শুধুমাত্র দোকানের স্টক চেক হবে)
        for item_data in data['items']:
            book = item_data['book']
            qty = item_data['quantity']
            try:
                summary = StockSummary.objects.get(book=book)
                if summary.shop_quantity < qty:
                    raise serializers.ValidationError(
                        f"'{book.title}' এর দোকানের স্টক কম। আছে: {summary.shop_quantity}, চাই: {qty}"
                    )
            except StockSummary.DoesNotExist:
                raise serializers.ValidationError(f"'{book.title}' এর কোনো স্টক নেই।")
        return data

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        customer_phone = validated_data.get('customer_phone')
        customer_name = validated_data.get('customer_name')
        
        customer = None
        previous_due = 0
        
        from .models import Customer
        if customer_phone:
            customer, created = Customer.objects.get_or_create(
                phone=customer_phone,
                defaults={'name': customer_name}
            )
            previous_due = customer.total_due

        # subtotal হিসাব
        subtotal = sum(
            (item['unit_price'] * item['quantity']) - item.get('discount', 0)
            for item in items_data
        )
        total = subtotal - validated_data.get('discount', 0) + \
                validated_data.get('packaging_charge', 0) + \
                validated_data.get('courier_charge', 0) + \
                validated_data.get('transaction_fee', 0)

        sale = Sale.objects.create(
            subtotal=subtotal,
            total=total,
            customer=customer,
            previous_due=previous_due,
            **validated_data
        )

        # পেমেন্ট রেকর্ড তৈরি করুন
        from .models import Payment
        if sale.paid_amount > 0:
            Payment.objects.create(
                sale=sale,
                amount=sale.paid_amount,
                method=sale.payment_method
            )

        # কাস্টমারের ডিউ আপডেট করুন
        if customer:
            customer.total_due += sale.due_amount
            customer.save()

        total_cost = 0
        for item_data in items_data:
            book = item_data['book']
            qty = item_data['quantity']
            cost = book.purchase_price or 0
            total_cost += cost * qty

            SaleItem.objects.create(
                sale=sale,
                book=book,
                quantity=qty,
                unit_price=item_data['unit_price'],
                cost_price=cost,
                discount=item_data.get('discount', 0),
            )

            # স্টক কমান (শুধুমাত্র দোকান থেকে)
            StockEntry.objects.create(
                book=book,
                quantity=-qty,
                source='adjustment', 
                location='shop',
                reference_id=sale.id,
                note=f"Sale Invoice #{sale.invoice_number}"
            )
            StockSummary.update_stock(book, -qty, location='shop')

        # Sale এর মোট কেনা দাম (total_cost) আপডেট করুন
        sale.total_cost = total_cost
        sale.save(update_fields=['total_cost'])

        return sale


class ExternalTradeSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    total_purchase = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_sale = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    profit = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    converted_book_title = serializers.CharField(source='converted_book.title', read_only=True, default=None)

    class Meta:
        model = ExternalTrade
        fields = [
            'id', 'book_title', 'author', 'publisher', 'quantity',
            'body_rate', 'purchase_commission', 'sale_commission',
            'purchase_price', 'selling_price',
            'customer_name', 'customer_phone',
            'status', 'status_display', 'note',
            'total_purchase', 'total_sale', 'profit',
            'converted_book', 'converted_book_title',
            'created_at', 'sold_at',
        ]
        read_only_fields = ['status', 'sold_at', 'converted_book']


class ExternalTradeCreateSerializer(serializers.Serializer):
    """
    বাইরে থেকে বই কিনে সরাসরি বিক্রির রেকর্ড তৈরির জন্য।
    already_sold=False হলে শুধু ক্রয়ের ক্যাশ-আউট রেকর্ড হবে (বিক্রয় পরে সম্পন্ন করা যাবে)।
    """
    book_title = serializers.CharField(max_length=500)
    author = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    publisher = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    quantity = serializers.IntegerField(min_value=1, default=1)
    body_rate = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0, min_value=0)
    purchase_commission = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=0, min_value=0, max_value=100)
    sale_commission = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=0, min_value=0, max_value=100)
    purchase_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    already_sold = serializers.BooleanField(default=False)
    selling_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0, min_value=0)
    customer_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    note = serializers.CharField(required=False, allow_blank=True, default='')

    @transaction.atomic
    def create(self, validated_data):
        from accounts.models import DailyCash, CashTransaction

        already_sold = validated_data.pop('already_sold', False)
        submitted_selling_price = validated_data.pop('selling_price', 0)
        selling_price = submitted_selling_price if already_sold else 0

        trade = ExternalTrade.objects.create(
            status='sold' if already_sold else 'pending',
            selling_price=selling_price,
            sold_at=timezone.now() if already_sold else None,
            **validated_data
        )

        daily_cash = DailyCash.get_for_today()
        CashTransaction.objects.create(
            daily_cash=daily_cash,
            transaction_type='cash_out',
            amount=trade.total_purchase,
            note=f"বাইরের বই ক্রয়: {trade.book_title} x{trade.quantity}",
            reference_id=f"ext_{trade.id}",
        )
        if already_sold and trade.total_sale > 0:
            CashTransaction.objects.create(
                daily_cash=daily_cash,
                transaction_type='cash_in',
                amount=trade.total_sale,
                note=f"বাইরের বই বিক্রয়: {trade.book_title} x{trade.quantity}",
                reference_id=f"ext_{trade.id}",
            )

        return trade
