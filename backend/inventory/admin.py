from django.contrib import admin

from .models import ReagentSku, StockMovement


@admin.register(ReagentSku)
class ReagentSkuAdmin(admin.ModelAdmin):
    list_display = ('sku_code', 'name', 'department', 'centre', 'unit', 'reorder_level', 'is_active')
    list_filter = ('department', 'centre', 'is_active')
    search_fields = ('sku_code', 'name', 'category', 'description')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('sku', 'movement_type', 'quantity', 'lot_number', 'expires_on', 'reference_type', 'created_at')
    list_filter = ('movement_type', 'sku__department', 'sku__centre')
    search_fields = ('sku__sku_code', 'lot_number', 'reference_id', 'note')
