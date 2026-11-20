"""Clinical usage → stock deduction (radiology Items used, future lab draws, etc.)."""
from __future__ import annotations

from django.core.exceptions import ValidationError

from inventory.models import ConsumableDepartment, ReagentSku
from inventory.services import issue_stock, on_hand


INSUFFICIENT_RESTOCK_MSG = (
    '{name} is insufficient. Contact admin for restock.'
)


def resolve_sku(*, centre, department: str, sku_id=None, name: str = '', require_sku: bool = False):
    """Prefer explicit SKU id; optionally fall back to active centre+department name match."""
    if sku_id:
        try:
            sku = ReagentSku.objects.get(pk=sku_id, centre=centre, is_active=True)
        except (ReagentSku.DoesNotExist, ValueError, TypeError):
            raise ValidationError(
                f'Unknown consumable. Only items created in inventory can be recorded.'
            )
        if department and sku.department not in (department, ConsumableDepartment.GENERAL):
            raise ValidationError(
                f'{sku.sku_code} belongs to {sku.get_department_display()}, not {department}.'
            )
        return sku
    name = (name or '').strip()
    if require_sku:
        if name:
            raise ValidationError(
                f'"{name}" is not linked to inventory. Only consumables created in inventory can be used.'
            )
        return None
    if not name:
        return None
    qs = ReagentSku.objects.filter(centre=centre, is_active=True, name__iexact=name)
    if department:
        qs = qs.filter(department__in=[department, ConsumableDepartment.GENERAL])
    return qs.order_by('sku_code').first()


def apply_supply_deductions(
    *,
    centre,
    department: str,
    supplies,
    user,
    reference_type: str,
    reference_id,
    require_sku: bool = False,
):
    """
    For each supply row {'name', 'quantity', optional 'sku'/'sku_id'}:
    issue stock when a catalogue SKU is resolved.
    When require_sku=True (radiology Items used), free-text items are rejected and
    insufficient stock raises a restock alert. Qty may equal on-hand (uses remaining).
    Returns list of (sku_or_None, name, quantity, movement_or_None).
    """
    results = []
    for row in supplies:
        if isinstance(row, dict):
            name = str(row.get('name') or row.get('item_name') or '').strip()
            qty = int(row.get('quantity') or 0)
            sku_id = row.get('sku') or row.get('sku_id') or ''
        else:
            name = str(row[0]).strip() if row else ''
            qty = int(row[1]) if len(row) > 1 else 0
            sku_id = row[2] if len(row) > 2 else ''
        if qty <= 0:
            continue
        if require_sku and not sku_id:
            raise ValidationError(
                'Only consumables created in inventory can be recorded. Contact admin to set up stock items.'
            )
        sku = resolve_sku(
            centre=centre,
            department=department,
            sku_id=sku_id or None,
            name=name,
            require_sku=require_sku,
        )
        if sku is None:
            if require_sku:
                raise ValidationError(
                    'Only consumables created in inventory can be recorded. Contact admin to set up stock items.'
                )
            results.append((None, name[:80], qty, None))
            continue

        display = sku.name or name or sku.sku_code
        balance = on_hand(sku)
        if balance < qty:
            raise ValidationError(INSUFFICIENT_RESTOCK_MSG.format(name=display))

        try:
            movement = issue_stock(
                sku=sku,
                quantity=qty,
                user=user,
                note=f'{department} usage: {display} × {qty}',
                reference_type=reference_type,
                reference_id=str(reference_id),
            )
        except ValidationError as exc:
            # Race / concurrent issue: surface the restock wording.
            detail = '; '.join(getattr(exc, 'messages', None) or [str(exc)])
            if 'Insufficient' in detail or 'insufficient' in detail:
                raise ValidationError(INSUFFICIENT_RESTOCK_MSG.format(name=display)) from exc
            raise
        results.append((sku, (name or display)[:80], qty, movement))
    return results
