from django.contrib import admin

from .models import ReagentSku, StockMovement


@admin.register(ReagentSku)
class ReagentSkuAdmin(admin.ModelAdmin):
    list_display = ('sku_code', 'name', 'centre', 'unit', 'reorder_level', 'is_active')
    list_filter = ('centre', 'is_active')
    search_fields = ('sku_code', 'name')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('sku', 'movement_type', 'quantity', 'lot_number', 'expires_on', 'created_at')
    list_filter = ('movement_type', 'sku__centre')
    search_fields = ('sku__sku_code', 'lot_number')
