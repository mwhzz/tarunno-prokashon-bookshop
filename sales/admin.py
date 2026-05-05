from django.contrib import admin
from .models import Sale, SaleItem

class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'customer_name', 'total', 'paid_amount', 'due_amount', 'status', 'created_at']
    list_filter = ['status', 'payment_method']
    search_fields = ['invoice_number', 'customer_name']
    inlines = [SaleItemInline]
