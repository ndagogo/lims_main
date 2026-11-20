"""Enterprise stock ledger helpers for consumables."""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import ReagentSku, StockMovement


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def on_hand(sku: ReagentSku) -> Decimal:
    total = sku.movements.aggregate(s=Sum('quantity'))['s']
    return total or Decimal('0')


@transaction.atomic
def receive_stock(
    *,
    sku: ReagentSku,
    quantity,
    user=None,
    lot_number: str = '',
    expires_on=None,
    note: str = '',
) -> StockMovement:
    qty = _as_decimal(quantity)
    if qty <= 0:
        raise ValidationError('Receipt quantity must be positive.')
    if not sku.is_active:
        raise ValidationError(f'Consumable {sku.sku_code} is inactive.')
    return StockMovement.objects.create(
        sku=sku,
        quantity=qty,
        movement_type='RECEIPT',
        lot_number=(lot_number or '')[:80],
        expires_on=expires_on,
        note=note or f'Stock receipt for {sku.sku_code}',
        recorded_by=user,
    )


@transaction.atomic
def issue_stock(
    *,
    sku: ReagentSku,
    quantity,
    user=None,
    note: str = '',
    reference_type: str = '',
    reference_id: str = '',
    allow_negative: bool = False,
) -> StockMovement:
    """
    Deduct stock for clinical usage. Locks the SKU row and validates on-hand
    unless allow_negative is True (admin override / adjustment path).
    """
    qty = abs(_as_decimal(quantity))
    if qty <= 0:
        raise ValidationError('Issue quantity must be positive.')

    locked = ReagentSku.objects.select_for_update().get(pk=sku.pk)
    if not locked.is_active:
        raise ValidationError(f'Consumable {locked.sku_code} is inactive.')

    balance = on_hand(locked)
    if not allow_negative and balance < qty:
        raise ValidationError(
            f'Insufficient stock for {locked.sku_code} ({locked.name}): '
            f'on hand {balance} {locked.unit}, requested {qty}.'
        )

    return StockMovement.objects.create(
        sku=locked,
        quantity=-qty,
        movement_type='ISSUE',
        note=note or f'Issue {qty} {locked.unit} of {locked.sku_code}',
        recorded_by=user,
        reference_type=(reference_type or '')[:40],
        reference_id=str(reference_id or '')[:64],
    )


@transaction.atomic
def adjust_stock(
    *,
    sku: ReagentSku,
    quantity,
    user=None,
    note: str = '',
    movement_type: str = 'ADJUST',
) -> StockMovement:
    """Signed adjustment: positive increases stock, negative decreases."""
    qty = _as_decimal(quantity)
    if qty == 0:
        raise ValidationError('Adjustment quantity cannot be zero.')
    locked = ReagentSku.objects.select_for_update().get(pk=sku.pk)
    if movement_type == 'EXPIRE' and qty > 0:
        qty = -qty
    return StockMovement.objects.create(
        sku=locked,
        quantity=qty,
        movement_type=movement_type if movement_type in ('ADJUST', 'EXPIRE', 'RETURN') else 'ADJUST',
        note=note or f'Stock {movement_type.lower()} at {timezone.now():%Y-%m-%d %H:%M}',
        recorded_by=user,
    )


def consumables_for_centre(centre, *, department: str | None = None, active_only: bool = True):
    qs = ReagentSku.objects.filter(centre=centre).select_related('centre')
    if department:
        qs = qs.filter(department=department)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by('department', 'name')
