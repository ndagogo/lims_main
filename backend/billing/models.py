import uuid
from decimal import Decimal
from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel, SequenceCounter
from patients.models import Visit
from laboratory.models import Test, Tariff, LabOrder

class Invoice(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=30, unique=True, blank=True)
    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='invoices')
    payer_name = models.CharField(max_length=180, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=[('DRAFT','Draft'),('UNPAID','Unpaid'),('PART_PAID','Part paid'),('PAID','Paid'),('CANCELLED','Cancelled')], default='DRAFT')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoices_created')
    cancel_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoices_cancelled')
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [
            ('cancel_invoice', 'Can cancel an invoice'),
            ('approve_discount', 'Can approve invoice discounts'),
        ]
    def save(self, *args, **kwargs):
        if not self.invoice_number:
            with transaction.atomic():
                counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key='invoice-number')
                counter.value += 1; counter.save(update_fields=['value'])
                self.invoice_number = f'HFDN-INV-{counter.value:09d}'
        super().save(*args, **kwargs)
    def recalculate(self):
        if self.status == 'CANCELLED':
            return
        # Query the tables directly so a prefetched invoice cannot keep a stale total.
        subtotal = InvoiceItem.objects.filter(invoice_id=self.pk).aggregate(total=models.Sum('line_total'))['total'] or Decimal('0.00')
        paid = Payment.objects.filter(invoice_id=self.pk, status='CONFIRMED').aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        self.subtotal = subtotal
        self.total = max(Decimal('0.00'), subtotal - (self.discount_amount or Decimal('0.00')))
        self.status = 'PAID' if paid >= self.total and self.total > 0 else ('PART_PAID' if paid > 0 else 'UNPAID')
        self.save(update_fields=['subtotal', 'discount_amount', 'total', 'status', 'updated_at'])
        from referrals.services import sync_commission
        sync_commission(self)

class InvoiceItem(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    lab_order = models.ForeignKey(LabOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_items')
    imaging_order = models.ForeignKey('radiology.ImagingOrder', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_items')
    test = models.ForeignKey(Test, null=True, blank=True, on_delete=models.PROTECT)
    tariff = models.ForeignKey(Tariff, null=True, blank=True, on_delete=models.PROTECT)
    description = models.CharField(max_length=200, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    outsource_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    class Meta: ordering=['created_at']
    def save(self, *args, **kwargs):
        if self.quantity < 1: raise ValidationError('Quantity must be at least one.')
        if not self.test_id and not self.imaging_order_id:
            raise ValidationError('A laboratory test or an imaging order is required.')
        self.line_total = self.unit_price * self.quantity
        if not self.description:
            if self.test_id:
                self.description = self.test.name
            elif self.imaging_order_id and getattr(self.imaging_order, 'procedure_id', None):
                self.description = self.imaging_order.procedure.name
            elif self.imaging_order_id:
                self.description = str(self.imaging_order)
        super().save(*args, **kwargs)
        self.invoice.recalculate()

class Payment(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=[('CASH','Cash'),('POS','POS'),('TRANSFER','Transfer'),('CARD','Card'),('HMO','HMO'),('CREDIT','Credit')])
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    receipt_number = models.CharField(max_length=40, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=[('CONFIRMED','Confirmed'),('REVERSED','Reversed')], default='CONFIRMED')
    reversal_reason = models.TextField(blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='payments_reversed')
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [('reverse_payment', 'Can reverse a confirmed payment')]
    def save(self, *args, **kwargs):
        if self.amount <= 0: raise ValidationError('Payment amount must be greater than zero.')
        if self.pk:
            previous = Payment.objects.filter(pk=self.pk).only('amount', 'method', 'receipt_number', 'status').first()
            if previous and (previous.amount != self.amount or previous.method != self.method or (previous.receipt_number and previous.receipt_number != self.receipt_number)):
                raise ValidationError('A recorded payment cannot be edited. Reverse it and take a new payment.')
            if previous and previous.status == 'REVERSED' and self.status != 'REVERSED':
                raise ValidationError('A reversed payment stays reversed.')
        if not self.receipt_number:
            with transaction.atomic():
                counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key='receipt-number')
                counter.value += 1; counter.save(update_fields=['value'])
                self.receipt_number = f'HFDN-RCP-{counter.value:09d}'
        super().save(*args, **kwargs)
        self.invoice.recalculate()

class DiscountRequest(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='discount_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discounts_requested')
    discount_type = models.CharField(max_length=10, choices=[('PERCENT','Percentage'),('FIXED','Fixed amount')])
    requested_value = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=[('PENDING','Pending'),('APPROVED','Approved'),('REJECTED','Rejected'),('CANCELLED','Cancelled')], default='PENDING')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='discounts_reviewed')
    review_note = models.TextField(blank=True)
    applied_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    def clean(self):
        if self.requested_value <= 0: raise ValidationError('Discount value must be greater than zero.')
        if self.discount_type == 'PERCENT' and self.requested_value > 100: raise ValidationError('Percentage cannot exceed 100.')
