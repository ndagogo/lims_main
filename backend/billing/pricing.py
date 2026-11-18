"""Resolve tariff, receipt price display, and credit-wallet rules for registration."""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from patients.models import CHANNEL_CHOICES, OrganisationClient, RegistrationChannel


CREDIT_CHANNELS = frozenset({'CREDIT_CLIENT'})
ORG_CHANNELS = frozenset({'CREDIT_CLIENT', 'ON_PAYMENT_CLIENT'})
LEGACY_ORG_CHANNELS = frozenset({'CORPORATE', 'HMO'})


def normalize_channel(code: str) -> str:
    code = (code or 'SELF').strip().upper()
    if code in dict(CHANNEL_CHOICES):
        return code
    return 'SELF'


def channel_needs_client(code: str) -> bool:
    return normalize_channel(code) in ORG_CHANNELS | LEGACY_ORG_CHANNELS


def client_type_for_channel(code: str) -> str | None:
    code = normalize_channel(code)
    if code == 'CREDIT_CLIENT':
        return 'CREDIT'
    if code == 'ON_PAYMENT_CLIENT':
        return 'ON_PAYMENT'
    if code == 'CORPORATE':
        return 'CREDIT'
    if code == 'HMO':
        return 'ON_PAYMENT'
    return None


def get_registration_channel(centre, channel_code: str):
    code = normalize_channel(channel_code)
    if code in LEGACY_ORG_CHANNELS:
        # Map legacy visit channels onto the new B2B channel configs when present.
        mapped = 'CREDIT_CLIENT' if code == 'CORPORATE' else 'ON_PAYMENT_CLIENT'
        row = RegistrationChannel.objects.filter(centre=centre, code=mapped, is_active=True).select_related('tariff').first()
        if row:
            return row
    return RegistrationChannel.objects.filter(centre=centre, code=code, is_active=True).select_related('tariff').first()


def resolve_tariff(*, centre, channel_code: str, client: OrganisationClient | None = None, fallback_tariff=None):
    """Return the active tariff that prices this registration."""
    from laboratory.models import Tariff

    code = normalize_channel(channel_code)
    if client is not None and client.tariff_id and client.tariff and client.tariff.is_active:
        return client.tariff
    channel = get_registration_channel(centre, code)
    if channel is not None and channel.tariff_id and channel.tariff and channel.tariff.is_active:
        return channel.tariff
    if fallback_tariff is not None:
        if isinstance(fallback_tariff, Tariff):
            tariff = fallback_tariff
        else:
            tariff = Tariff.objects.filter(pk=fallback_tariff, is_active=True).first()
        if tariff is not None:
            return tariff
    raise ValidationError(
        'You can not proceed , contact admin to setup the price. Thanks'
    )


def effective_show_test_prices(*, centre, channel_code: str, client: OrganisationClient | None = None) -> bool:
    """OFF by default. Channel OFF forces all clients OFF; when channel ON, client toggle applies for B2B."""
    channel = get_registration_channel(centre, channel_code)
    channel_on = False if channel is None else bool(channel.show_test_prices)
    if not channel_on:
        return False
    if client is not None:
        return bool(client.show_test_prices)
    return True


def visit_shows_test_prices(visit) -> bool:
    return effective_show_test_prices(
        centre=visit.centre,
        channel_code=visit.referral_source,
        client=visit.client,
    )


def receipt_shows_line_prices(visit) -> bool:
    """
    Whether receipt/invoice printouts may show per-test prices.

    Prefer the registration snapshot on the visit so reprints match the original
    print. Fall back to the live channel/client rule when the snapshot is missing.
    """
    if visit is None:
        return False
    if hasattr(visit, 'show_test_prices') and visit.show_test_prices is not None:
        return bool(visit.show_test_prices)
    try:
        return visit_shows_test_prices(visit)
    except Exception:
        return False


def ensure_credit_wallet_covers(*, client: OrganisationClient, amount: Decimal):
    if client is None or client.client_type_code != 'CREDIT':
        return
    balance = client.credit_wallet_balance or Decimal('0.00')
    if balance < amount:
        raise ValidationError(
            'Hey Wallet Balance is low for this operation! '
            'Please contact the administrator to top-up credit wallet and try again'
        )


def _client_type_code(client: OrganisationClient | None) -> str:
    if client is None:
        return ''
    code = (client.client_type_code or '').strip().upper()
    if code:
        return code
    if getattr(client, 'client_type_id', None) and client.client_type_id:
        return (getattr(client.client_type, 'code', '') or '').strip().upper()
    return ''


def visit_uses_credit_wallet(visit) -> bool:
    """True when this visit is billed against a credit client's wallet.

    Key off the organisation client type (CREDIT). Channel is not required —
    legacy CORPORATE visits and any paid credit-client invoice must still debit.
    """
    if visit is None or not getattr(visit, 'client_id', None):
        return False
    return _client_type_code(getattr(visit, 'client', None)) == 'CREDIT'


@transaction.atomic
def debit_credit_wallet(*, client: OrganisationClient, amount: Decimal, visit=None, invoice=None, actor=None, note=''):
    """Deduct wallet balance. Idempotent per invoice (one DEBIT ledger row)."""
    if client is None or client.client_type_code != 'CREDIT' or amount <= 0:
        return None
    from patients.models import CreditWalletLedger
    if invoice is not None and CreditWalletLedger.objects.filter(invoice=invoice, entry_type='DEBIT').exists():
        return None
    locked = OrganisationClient.objects.select_for_update().get(pk=client.pk)
    if (locked.credit_wallet_balance or Decimal('0.00')) < amount:
        raise ValidationError(
            'Hey Wallet Balance is low for this operation! '
            'Please contact the administrator to top-up credit wallet and try again'
        )
    OrganisationClient.objects.filter(pk=locked.pk).update(
        credit_wallet_balance=F('credit_wallet_balance') - amount,
        credit_wallet_used=F('credit_wallet_used') + amount,
        updated_at=timezone.now(),
    )
    locked.refresh_from_db()
    return CreditWalletLedger.objects.create(
        client=locked,
        entry_type='DEBIT',
        amount=amount,
        balance_after=locked.credit_wallet_balance,
        visit=visit,
        invoice=invoice,
        created_by=actor,
        note=note or f'Visit {getattr(visit, "visit_id", "")}'.strip(),
    )


@transaction.atomic
def restore_credit_wallet(*, client: OrganisationClient, amount: Decimal, visit=None, invoice=None, actor=None, note=''):
    """Return wallet funds after a paid credit invoice leaves PAID (e.g. payment reverse)."""
    if client is None or client.client_type_code != 'CREDIT' or amount <= 0:
        return None
    from patients.models import CreditWalletLedger
    if invoice is not None and CreditWalletLedger.objects.filter(
        invoice=invoice, entry_type='ADJUSTMENT', reference='WALLET_RESTORE',
    ).exists():
        return None
    locked = OrganisationClient.objects.select_for_update().get(pk=client.pk)
    OrganisationClient.objects.filter(pk=locked.pk).update(
        credit_wallet_balance=F('credit_wallet_balance') + amount,
        credit_wallet_used=F('credit_wallet_used') - amount,
        updated_at=timezone.now(),
    )
    locked.refresh_from_db()
    used = locked.credit_wallet_used or Decimal('0.00')
    if used < 0:
        OrganisationClient.objects.filter(pk=locked.pk).update(credit_wallet_used=Decimal('0.00'))
        locked.refresh_from_db()
    return CreditWalletLedger.objects.create(
        client=locked,
        entry_type='ADJUSTMENT',
        amount=amount,
        balance_after=locked.credit_wallet_balance,
        visit=visit,
        invoice=invoice,
        created_by=actor,
        reference='WALLET_RESTORE',
        note=note or 'Wallet restored after invoice left Paid status',
    )


def _wallet_actor_from_invoice(invoice, *, for_restore=False):
    """Staff who accepted payment (or who reversed it) for wallet ledger attribution."""
    payments = invoice.payments.select_related('received_by', 'reversed_by').order_by('-created_at')
    if for_restore:
        reversed_pay = payments.filter(status='REVERSED').order_by('-reversed_at', '-updated_at').first()
        if reversed_pay and reversed_pay.reversed_by_id:
            return reversed_pay.reversed_by
    confirmed = payments.filter(status='CONFIRMED').first()
    if confirmed and confirmed.received_by_id:
        return confirmed.received_by
    any_pay = payments.first()
    if any_pay and any_pay.received_by_id:
        return any_pay.received_by
    return None


def apply_credit_wallet_for_invoice_status(*, invoice, previously_paid: bool, actor=None):
    """
    Debit credit-client wallet when invoice is PAID (idempotent via ledger).
    Restore the debit if a previously paid credit invoice leaves PAID.

    Debit is keyed on current PAID status + missing DEBIT ledger (not only the
    first transition). That repairs payments that became Paid before the debit
    hook ran, and survives a second recalculate.
    """
    from patients.models import CreditWalletLedger, Visit

    visit_id = getattr(invoice, 'visit_id', None)
    if not visit_id:
        return None
    visit = Visit.objects.select_related('client', 'client__client_type').filter(pk=visit_id).first()
    if visit is None or not visit_uses_credit_wallet(visit):
        return None
    client = visit.client
    amount = invoice.total or Decimal('0.00')
    if invoice.status == 'PAID':
        if CreditWalletLedger.objects.filter(invoice_id=invoice.pk, entry_type='DEBIT').exists():
            return None
        wallet_actor = actor or _wallet_actor_from_invoice(invoice, for_restore=False)
        return debit_credit_wallet(
            client=client,
            amount=amount,
            visit=visit,
            invoice=invoice,
            actor=wallet_actor,
            note=f'Wallet debit on Paid invoice {invoice.invoice_number}',
        )
    if previously_paid and invoice.status != 'PAID':
        debit = CreditWalletLedger.objects.filter(invoice_id=invoice.pk, entry_type='DEBIT').order_by('created_at').first()
        restore_amount = debit.amount if debit is not None else amount
        wallet_actor = actor or _wallet_actor_from_invoice(invoice, for_restore=True)
        return restore_credit_wallet(
            client=client,
            amount=restore_amount,
            visit=visit,
            invoice=invoice,
            actor=wallet_actor,
            note=f'Wallet restore for invoice {invoice.invoice_number} ({invoice.status})',
        )
    return None


def reconcile_paid_credit_wallet_debits(*, limit: int = 500):
    """Backfill wallet debits for Paid credit-client invoices that missed a ledger row."""
    from patients.models import CreditWalletLedger
    from billing.models import Invoice

    rows = Invoice.objects.filter(
        status='PAID',
        visit__client_id__isnull=False,
        visit__client__client_type_code='CREDIT',
    ).select_related('visit', 'visit__client', 'visit__client__client_type').order_by('-created_at')[:limit]
    applied = []
    skipped = []
    for invoice in rows:
        if CreditWalletLedger.objects.filter(invoice_id=invoice.pk, entry_type='DEBIT').exists():
            continue
        try:
            entry = apply_credit_wallet_for_invoice_status(invoice=invoice, previously_paid=False)
        except ValidationError as exc:
            skipped.append({
                'invoice': invoice.invoice_number,
                'visit': invoice.visit.visit_id,
                'client': invoice.visit.client.name,
                'amount': str(invoice.total),
                'error': '; '.join(getattr(exc, 'messages', [str(exc)])),
            })
            continue
        if entry is not None:
            applied.append({
                'invoice': invoice.invoice_number,
                'visit': invoice.visit.visit_id,
                'client': invoice.visit.client.name,
                'amount': str(entry.amount),
            })
    return {'applied': applied, 'skipped': skipped}


@transaction.atomic
def top_up_credit_wallet(*, client: OrganisationClient, amount: Decimal, actor, reference='', note=''):
    if amount <= 0:
        raise ValidationError('Top-up amount must be greater than zero.')
    if client.client_type_code != 'CREDIT':
        raise ValidationError('Only credit clients have a credit wallet.')
    locked = OrganisationClient.objects.select_for_update().get(pk=client.pk)
    OrganisationClient.objects.filter(pk=locked.pk).update(
        credit_wallet_balance=F('credit_wallet_balance') + amount,
        credit_limit=F('credit_limit') + amount,
        updated_at=timezone.now(),
    )
    locked.refresh_from_db()
    from patients.models import CreditWalletLedger
    return CreditWalletLedger.objects.create(
        client=locked,
        entry_type='TOP_UP',
        amount=amount,
        balance_after=locked.credit_wallet_balance,
        created_by=actor,
        reference=reference,
        note=note,
    )
