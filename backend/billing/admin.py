from django.contrib import admin
from .models import Invoice, InvoiceItem, Payment, DiscountRequest, BillAmendment, Refund

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ('line_total',)

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number','visit','subtotal','discount_amount','total','status','created_at')
    search_fields = ('invoice_number','visit__visit_id','visit__patient__uhid','visit__patient__phone_number')
    list_filter = ('status','visit__centre')
    readonly_fields = ('invoice_number','subtotal','total','created_at','updated_at')
    inlines = [InvoiceItemInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('receipt_number','invoice','amount','method','status','received_by','created_at')
    search_fields = ('receipt_number','invoice__invoice_number','reference')
    list_filter = ('method','status')
    readonly_fields = ('receipt_number','created_at','updated_at')

@admin.register(DiscountRequest)
class DiscountRequestAdmin(admin.ModelAdmin):
    list_display = ('invoice','discount_type','requested_value','status','requested_by','reviewed_by','created_at')
    list_filter = ('status','discount_type')
    readonly_fields = ('requested_by','reviewed_by','applied_amount','created_at','updated_at')

admin.site.register(InvoiceItem)


@admin.register(BillAmendment)
class BillAmendmentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'action', 'amount_delta', 'authorised_by', 'created_at')
    list_filter = ('action',)
    search_fields = ('invoice__invoice_number', 'description', 'reason')


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ('payment', 'amount', 'status', 'authorised_by', 'completed_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('payment__receipt_number', 'reason')
