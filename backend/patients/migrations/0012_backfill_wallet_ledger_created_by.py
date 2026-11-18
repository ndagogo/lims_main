# Backfill credit-wallet ledger "By" from the payment cashier / reversing staff.

from django.db import migrations
from django.db.models import Q


def backfill_ledger_created_by(apps, schema_editor):
    CreditWalletLedger = apps.get_model('patients', 'CreditWalletLedger')
    Payment = apps.get_model('billing', 'Payment')

    rows = CreditWalletLedger.objects.filter(
        created_by__isnull=True,
        invoice_id__isnull=False,
    ).filter(Q(entry_type='DEBIT') | Q(entry_type='ADJUSTMENT', reference='WALLET_RESTORE'))
    for entry in rows.iterator():
        actor_id = None
        if entry.entry_type == 'DEBIT':
            pay = (
                Payment.objects.filter(invoice_id=entry.invoice_id, status='CONFIRMED', received_by_id__isnull=False)
                .order_by('-created_at')
                .first()
            )
            if pay is None:
                pay = (
                    Payment.objects.filter(invoice_id=entry.invoice_id, received_by_id__isnull=False)
                    .order_by('-created_at')
                    .first()
                )
            actor_id = pay.received_by_id if pay else None
        else:
            pay = (
                Payment.objects.filter(invoice_id=entry.invoice_id, status='REVERSED', reversed_by_id__isnull=False)
                .order_by('-reversed_at', '-updated_at')
                .first()
            )
            actor_id = pay.reversed_by_id if pay else None
            if actor_id is None:
                pay = (
                    Payment.objects.filter(invoice_id=entry.invoice_id, received_by_id__isnull=False)
                    .order_by('-created_at')
                    .first()
                )
                actor_id = pay.received_by_id if pay else None
        if actor_id:
            CreditWalletLedger.objects.filter(pk=entry.pk).update(created_by_id=actor_id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0011_centre_phones_banks_receipt_footer_and_price_defaults'),
        ('billing', '0007_centre_phones_banks_receipt_footer_and_price_defaults'),
    ]

    operations = [
        migrations.RunPython(backfill_ledger_created_by, noop_reverse),
    ]
