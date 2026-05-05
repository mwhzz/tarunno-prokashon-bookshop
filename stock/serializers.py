from rest_framework import serializers
from .models import StockEntry, StockSummary, StockTransfer
from books.serializers import BookListSerializer


class StockEntrySerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source='book.title', read_only=True)

    class Meta:
        model = StockEntry
        fields = ['id', 'book', 'book_title', 'quantity', 'source', 'location', 'supplier_name', 'purchase_price', 'note', 'created_at']
        read_only_fields = ['created_at']

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError("Quantity cannot be zero.")
        return value

    def create(self, validated_data):
        entry = super().create(validated_data)
        
        # If it's a purchase and purchase_price is provided, update the book's purchase price
        if entry.source == 'purchase' and entry.purchase_price is not None:
            book = entry.book
            book.purchase_price = entry.purchase_price
            book.save(update_fields=['purchase_price'])

        # StockSummary আপডেট করুন নির্দিষ্ট লোকেশনের জন্য
        StockSummary.update_stock(entry.book, entry.quantity, location=entry.location)
        return entry


class StockSummarySerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source='book.title', read_only=True)
    book_author = serializers.CharField(source='book.author', read_only=True)
    selling_price = serializers.DecimalField(
        source='book.selling_price', max_digits=10, decimal_places=2, read_only=True
    )
    stock_value = serializers.SerializerMethodField()

    class Meta:
        model = StockSummary
        fields = ['id', 'book', 'book_title', 'book_author', 'quantity',
                  'godown_quantity', 'shop_quantity',
                  'selling_price', 'stock_value', 'last_updated']

    def get_stock_value(self, obj):
        return obj.quantity * obj.book.selling_price


class StockTransferSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source='book.title', read_only=True)

    class Meta:
        model = StockTransfer
        fields = ['id', 'book', 'book_title', 'quantity', 'from_location', 'to_location', 'note', 'created_at']
        read_only_fields = ['created_at']

    def validate(self, data):
        if data['from_location'] == data['to_location']:
            raise serializers.ValidationError("Source and destination locations must be different.")
        if data['quantity'] <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        
        # Check stock availability in source location
        try:
            summary = StockSummary.objects.get(book=data['book'])
            if data['from_location'] == 'godown' and summary.godown_quantity < data['quantity']:
                raise serializers.ValidationError("Not enough stock in Godown.")
            if data['from_location'] == 'shop' and summary.shop_quantity < data['quantity']:
                raise serializers.ValidationError("Not enough stock in Shop.")
        except StockSummary.DoesNotExist:
            raise serializers.ValidationError("No stock summary exists for this book.")
            
        return data

    def create(self, validated_data):
        from django.db import transaction
        with transaction.atomic():
            transfer = super().create(validated_data)
            # Decrease from source
            StockSummary.update_stock(transfer.book, -transfer.quantity, location=transfer.from_location)
            # Increase to destination
            StockSummary.update_stock(transfer.book, transfer.quantity, location=transfer.to_location)
            return transfer
