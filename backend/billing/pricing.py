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
    """Channel OFF forces all clients OFF; when channel ON, client toggle applies for B2B."""
    channel = get_registration_channel(centre, channel_code)
    channel_on = True if channel is None else bool(channel.show_test_prices)
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


def ensure_credit_wallet_covers(*, client: OrganisationClient, amount: Decimal):
    if client is None or client.client_type_code != 'CREDIT':
        return
    balance = client.credit_wallet_balance or Decimal('0.00')
    if balance < amount:
        raise ValidationError(
            'Hey Wallet Balance is low for this operation! '
            'Please contact the administrator to top-up credit wallet and try again'
        )


@transaction.atomic
def debit_credit_wallet(*, client: OrganisationClient, amount: Decimal, visit=None, invoice=None, actor=None, note=''):
    if client is None or client.client_type_code != 'CREDIT' or amount <= 0:
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
    from patients.models import CreditWalletLedger
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
