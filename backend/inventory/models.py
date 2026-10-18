import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import Centre, TimeStampedModel


class ReagentSku(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='reagent_skus')
    sku_code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    unit = models.CharField(max_length=20, default='unit')
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [('centre', 'sku_code')]

    def __str__(self):
        return f'{self.sku_code} — {self.name}'

    @property
    def on_hand(self):
        total = self.movements.aggregate(s=models.Sum('quantity'))['s']
        return total or Decimal('0')


class StockMovement(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.ForeignKey(ReagentSku, on_delete=models.PROTECT, related_name='movements')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)  # positive = in, negative = out
    movement_type = models.CharField(max_length=20, choices=[
        ('RECEIPT', 'Receipt'), ('ISSUE', 'Issue'), ('ADJUST', 'Adjustment'), ('EXPIRE', 'Expire'),
    ])
    lot_number = models.CharField(max_length=80, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
