import urllib.request

from django.core.files.base import ContentFile
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
    image_url = serializers.URLField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Book
        fields = [
            'id', 'title', 'product_code', 'author', 'publisher', 'isbn',
            'edition', 'group', 'group_name', 'image', 'image_url', 'book_type',
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

    def create(self, validated_data):
        image_url = validated_data.pop('image_url', '').strip()
        book = super().create(validated_data)
        self._save_image_from_url(book, image_url)
        return book

    def update(self, instance, validated_data):
        image_url = validated_data.pop('image_url', '').strip()
        book = super().update(instance, validated_data)
        self._save_image_from_url(book, image_url)
        return book

    def _save_image_from_url(self, book, image_url):
        if not image_url:
            return

        try:
            req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                image_data = response.read()
        except Exception as exc:
            raise serializers.ValidationError({'image_url': f'Image download failed: {exc}'})

        ext = image_url.split('?')[0].rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            ext = 'jpg'
        book.image.save(f'book_{book.id}.{ext}', ContentFile(image_data), save=True)


class BookListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for lists"""
    group_name = serializers.CharField(source='group.name', read_only=True)
    current_stock = serializers.IntegerField(read_only=True)
    shop_quantity = serializers.IntegerField(source='stock_summary.shop_quantity', read_only=True, default=0)
    godown_quantity = serializers.IntegerField(source='stock_summary.godown_quantity', read_only=True, default=0)

    class Meta:
        model = Book
        fields = [
            'id', 'title', 'product_code', 'author', 'mrp', 'selling_price',
            'commission', 'discount', 'discount_type',
            'current_stock', 'shop_quantity', 'godown_quantity', 'group_name',
            'is_active', 'image',
        ]
