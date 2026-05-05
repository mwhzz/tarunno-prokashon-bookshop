from django.contrib import admin
from .models import Book, Group

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name']

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ['title', 'author', 'group', 'purchase_price', 'selling_price', 'is_active']
    list_filter = ['group', 'is_active']
    search_fields = ['title', 'author', 'isbn']
