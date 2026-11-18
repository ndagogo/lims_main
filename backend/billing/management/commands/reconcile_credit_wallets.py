"""Backfill credit-wallet debits for Paid invoices that missed a ledger entry."""
from django.core.management.base import BaseCommand

from billing.pricing import reconcile_paid_credit_wallet_debits


class Command(BaseCommand):
    help = 'Debit credit-client wallets for Paid invoices that have no DEBIT ledger row yet.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        result = reconcile_paid_credit_wallet_debits(limit=options['limit'])
        applied = result.get('applied') or []
        skipped = result.get('skipped') or []
        if not applied and not skipped:
            self.stdout.write(self.style.SUCCESS('No missing credit-wallet debits found.'))
            return
        for row in applied:
            self.stdout.write(
                f"Debited {row['amount']} from {row['client']} "
                f"for {row['invoice']} ({row['visit']})"
            )
        for row in skipped:
            self.stdout.write(self.style.WARNING(
                f"Skipped {row['invoice']} ({row['client']}): {row['error']}"
            ))
        self.stdout.write(self.style.SUCCESS(
            f'Reconciled {len(applied)} invoice(s); skipped {len(skipped)}.'
        ))
