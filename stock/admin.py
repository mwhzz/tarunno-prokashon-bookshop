from django.contrib import admin
from .models import StockEntry, StockSummary

@admin.register(StockSummary)
class StockSummaryAdmin(admin.ModelAdmin):
    list_display = ['book', 'quantity', 'last_updated']
    search_fields = ['book__title']

@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ['book', 'quantity', 'source', 'created_at']
    list_filter = ['source']
