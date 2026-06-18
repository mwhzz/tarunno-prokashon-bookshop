from rest_framework import serializers
from .models import Book, Group


class GroupSerializer(serializers.ModelSerializer):
    book_count = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'book_count', 'created_at']

    def get_book_count(self, obj):
        return obj.books.count()


class BookSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True)
    current_stock = serializers.IntegerField(read_only=True)
    shop_quantity = serializers.IntegerField(source='stock_summary.shop_quantity', read_only=True, default=0)
    godown_quantity = serializers.IntegerField(source='stock_summary.godown_quantity', read_only=True, default=0)

    class Meta:
        model = Book
        fields = [
            'id', 'title', 'product_code', 'author', 'publisher', 'isbn',
            'edition', 'group', 'group_name', 'image', 'book_type',
            'mrp', 'purchase_price', 'selling_price',
            'commission', 'discount', 'discount_type',
            'current_stock', 'shop_quantity', 'godown_quantity',
            'is_active', 'notes',
            'created_at', 'updated_at'
        ]

    def validate_isbn(self, value):
        if value == '':
            return None
        return value

    def validate_product_code(self, value):
        if value == '':
            return None
        return value


class BookListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for lists"""
    group_name = serializers.CharField(source='group.name', read_only=True)
    current_stock = serializers.IntegerField(read_only=True)
    shop_quantity = serializers.IntegerField(source='stock_summary.shop_quantity', read_only=True, default=0)
    godown_quantity = serializers.IntegerField(source='stock_summary.godown_quantity', read_only=True, default=0)

    class Meta:
        model = Book
        fields = ['id', 'title', 'product_code', 'author', 'mrp', 'selling_price', 'current_stock', 'shop_quantity', 'godown_quantity', 'group_name', 'is_active', 'image']
