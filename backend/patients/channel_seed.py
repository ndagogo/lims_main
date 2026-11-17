"""Ensure registration channels and a default Self tariff exist per centre."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import Centre
from laboratory.models import Tariff
from patients.models import RegistrationChannel


CHANNEL_SPECS = [
    ('SELF', 'Self (B2C)', True),
    ('DOCTOR', 'Permanent doctors (B2D)', True),
    ('CREDIT_CLIENT', 'Credit clients (B2B)', False),
    ('ON_PAYMENT_CLIENT', 'On payment clients (B2B)', False),
]


def ensure_default_client_types():
    from patients.models import ClientType
    defaults = [
        ('CREDIT', 'Credit', False, 1),
        ('ON_PAYMENT', 'On Payment', False, 2),
        ('DEFAULT', 'Default', True, 3),
    ]
    for code, name, show, order in defaults:
        ClientType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'show_test_prices_default': show,
                'is_system': True,
                'sort_order': order,
                'is_active': True,
            },
        )


def _self_tariff_for_centre(centre: Centre) -> Tariff:
    """Create or reuse the centre's default Self / walk-in tariff."""
    name = f'{centre.code}_SELF'
    tariff = Tariff.objects.filter(name__iexact=name).first()
    if tariff is None:
        tariff = Tariff.objects.filter(centre=centre, name__icontains='SELF').order_by('pk').first()
    if tariff is None:
        tariff = Tariff(
            name=name,
            centre=centre,
            effective_from=timezone.localdate(),
            status='APPROVAL',
            is_active=True,
            version=1,
        )
        tariff.save()
    else:
        changed = False
        if not tariff.centre_id:
            tariff.centre = centre
            changed = True
        if tariff.status != 'APPROVAL':
            tariff.status = 'APPROVAL'
            changed = True
        if not tariff.is_active:
            tariff.is_active = True
            changed = True
        if changed:
            tariff.save()
    return tariff


@transaction.atomic
def ensure_centre_channels(centre: Centre, *, seed_self_tariff: bool = True) -> dict:
    """Create the four registration channels and map Self to a default tariff."""
    created_channels = 0
    for code, name, show in CHANNEL_SPECS:
        _row, created = RegistrationChannel.objects.get_or_create(
            centre=centre,
            code=code,
            defaults={'name': name, 'show_test_prices': show, 'is_active': True},
        )
        if created:
            created_channels += 1

    self_channel = RegistrationChannel.objects.select_related('tariff').get(centre=centre, code='SELF')
    doctor_channel = RegistrationChannel.objects.select_related('tariff').get(centre=centre, code='DOCTOR')
    tariff = None
    mapped = False
    if seed_self_tariff:
        tariff = _self_tariff_for_centre(centre)
        if self_channel.tariff_id is None:
            self_channel.tariff = tariff
            self_channel.name = self_channel.name or 'Self (B2C)'
            self_channel.save(update_fields=['tariff', 'name', 'updated_at'])
            mapped = True
        # Permanent Doctors can share the Self tariff until an admin sets a dedicated one.
        if doctor_channel.tariff_id is None:
            doctor_channel.tariff = self_channel.tariff or tariff
            doctor_channel.save(update_fields=['tariff', 'updated_at'])

    return {
        'centre': centre.code,
        'channels_created': created_channels,
        'self_tariff': getattr(self_channel.tariff or tariff, 'name', None),
        'self_mapped': mapped,
    }


def seed_all_centres(*, seed_self_tariff: bool = True) -> list[dict]:
    ensure_default_client_types()
    results = []
    for centre in Centre.objects.filter(is_active=True).order_by('code'):
        results.append(ensure_centre_channels(centre, seed_self_tariff=seed_self_tariff))
    return results
