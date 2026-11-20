import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.models import Centre, TimeStampedModel


class ConsumableDepartment:
    """Departments that own consumable catalogues (enterprise healthcare practice)."""
    LABORATORY = 'LABORATORY'
    RADIOLOGY = 'RADIOLOGY'
    PHLEBOTOMY = 'PHLEBOTOMY'
    GENERAL = 'GENERAL'
    CHOICES = [
        (LABORATORY, 'Laboratory'),
        (RADIOLOGY, 'Radiology'),
        (PHLEBOTOMY, 'Phlebotomy'),
        (GENERAL, 'General / Shared'),
    ]


class ReagentSku(TimeStampedModel):
    """
    Centre-scoped consumable SKU (reagents, syringes, film, vacutainers, etc.).
    On-hand quantity is the sum of signed stock movements.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='reagent_skus')
    department = models.CharField(
        max_length=20, choices=ConsumableDepartment.CHOICES, default=ConsumableDepartment.LABORATORY,
        db_index=True,
    )
    sku_code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default='')
    category = models.CharField(
        max_length=80, blank=True, default='',
        help_text='Optional grouping e.g. Contrast, Vacutainer, PPE.',
    )
    unit = models.CharField(max_length=20, default='unit')
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [('centre', 'sku_code')]
        verbose_name = 'Consumable SKU'
        verbose_name_plural = 'Consumable SKUs'
        ordering = ['department', 'sku_code']

    def __str__(self):
        return f'{self.sku_code} — {self.name}'

    @property
    def on_hand(self):
        total = self.movements.aggregate(s=models.Sum('quantity'))['s']
        return total or Decimal('0')

    @property
    def is_low(self):
        return self.on_hand <= (self.reorder_level or Decimal('0'))


class StockMovement(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.ForeignKey(ReagentSku, on_delete=models.PROTECT, related_name='movements')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)  # positive = in, negative = out
    movement_type = models.CharField(max_length=20, choices=[
        ('RECEIPT', 'Receipt / restock'),
        ('ISSUE', 'Issue / usage'),
        ('ADJUST', 'Adjustment'),
        ('EXPIRE', 'Expire / wastage'),
        ('RETURN', 'Return to stock'),
    ])
    lot_number = models.CharField(max_length=80, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    # Optional link back to the clinical event that consumed stock.
    reference_type = models.CharField(max_length=40, blank=True, default='')
    reference_id = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Stock movement'
        verbose_name_plural = 'Stock movements'

    def clean(self):
        if self.quantity == 0:
            raise ValidationError({'quantity': 'Quantity cannot be zero.'})
        if self.movement_type in ('ISSUE', 'EXPIRE') and self.quantity > 0:
            self.quantity = -abs(self.quantity)
        if self.movement_type in ('RECEIPT', 'RETURN') and self.quantity < 0:
            self.quantity = abs(self.quantity)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
