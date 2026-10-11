"""Commission accrual follows a fully paid invoice and reverses when that payment is undone."""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction


def commission_amount(doctor, invoice_total):
    total = Decimal(invoice_total or 0)
    if doctor.commission_type == 'PERCENT':
        return (total * doctor.commission_value / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return Decimal(doctor.commission_value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def sync_commission(invoice, user=None):
    from .models import CommissionLedgerEntry, ReferralAttribution

    attribution = ReferralAttribution.objects.filter(visit_id=invoice.visit_id).select_related('doctor').first()
    if attribution is None or not attribution.doctor.is_active:
        return None
    doctor = attribution.doctor
    with transaction.atomic():
        locked = invoice.__class__.objects.select_for_update().get(pk=invoice.pk)
        accruals = CommissionLedgerEntry.objects.select_for_update().filter(invoice=locked, entry_type='ACCRUAL').order_by('-created_at')
        open_accrual = None
        for entry in accruals:
            if not CommissionLedgerEntry.objects.filter(source_entry=entry, entry_type='REVERSAL').exists():
                open_accrual = entry
                break
        if locked.status == 'PAID' and open_accrual is None:
            amount = commission_amount(doctor, locked.total)
            if amount <= 0:
                return None
            return CommissionLedgerEntry.objects.create(
                doctor=doctor, invoice=locked, entry_type='ACCRUAL', amount=amount,
                description=f'Commission on {locked.invoice_number}', created_by=user,
            )
        if locked.status != 'PAID' and open_accrual is not None:
            return CommissionLedgerEntry.objects.create(
                doctor=doctor, invoice=locked, entry_type='REVERSAL', amount=-open_accrual.amount,
                description=f'Reversal of commission on {locked.invoice_number}',
                source_entry=open_accrual, created_by=user,
            )
    return None
