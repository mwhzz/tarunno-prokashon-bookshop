from django.contrib import admin

from .models import PurchaseBill, PurchaseBillItem, Vendor


class PurchaseBillItemInline(admin.TabularInline):
    model = PurchaseBillItem
    extra = 0


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ['name', 'vendor_code', 'phone', 'total_due', 'opening_due']
    search_fields = ['name', 'phone', 'vendor_code']


@admin.register(PurchaseBill)
class PurchaseBillAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendor_name', 'total', 'paid_amount', 'due_amount', 'status', 'purchase_date']
    list_filter = ['status', 'account_name', 'location']
    search_fields = ['vendor_name', 'memo_number']
    inlines = [PurchaseBillItemInline]
