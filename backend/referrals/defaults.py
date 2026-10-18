"""Per-centre DEFAULT DOCTOR placeholder for unknown referring physicians."""
from decimal import Decimal

from django.db import transaction

from .models import ReferralDoctor


DEFAULT_FIRST_NAME = 'DEFAULT'
DEFAULT_LAST_NAME = 'DOCTOR'


def default_doctor_code(centre) -> str:
    return f'{centre.code}-DEFAULT'


@transaction.atomic
def ensure_default_doctor(centre) -> ReferralDoctor:
    """Return the centre DEFAULT DOCTOR, creating it if missing."""
    existing = (
        ReferralDoctor.objects.select_for_update()
        .filter(centre=centre, is_default_placeholder=True)
        .first()
    )
    if existing:
        updates = []
        if not existing.is_active:
            existing.is_active = True
            updates.append('is_active')
        if existing.appear_on_reports:
            existing.appear_on_reports = False
            updates.append('appear_on_reports')
        if updates:
            updates.append('updated_at')
            existing.save(update_fields=updates)
        existing.centres.add(centre)
        return existing
    code = default_doctor_code(centre)
    doctor, created = ReferralDoctor.objects.get_or_create(
        doctor_code=code,
        defaults={
            'centre': centre,
            'title': '',
            'first_name': DEFAULT_FIRST_NAME,
            'last_name': DEFAULT_LAST_NAME,
            'facility_name': centre.name,
            'commission_type': 'PERCENT',
            'commission_value': Decimal('0'),
            'is_active': True,
            'is_default_placeholder': True,
            'appear_on_reports': False,
            'notes': 'System placeholder when referring physician is unknown at registration.',
        },
    )
    if not created and not doctor.is_default_placeholder:
        # Reserved code collided with a real doctor — force a unique placeholder code.
        doctor = ReferralDoctor.objects.create(
            centre=centre,
            doctor_code=f'{centre.code}-DEFAULT-SYS',
            first_name=DEFAULT_FIRST_NAME,
            last_name=DEFAULT_LAST_NAME,
            facility_name=centre.name,
            commission_type='PERCENT',
            commission_value=Decimal('0'),
            is_active=True,
            is_default_placeholder=True,
            appear_on_reports=False,
            notes='System placeholder when referring physician is unknown at registration.',
        )
    elif not doctor.is_default_placeholder:
        doctor.is_default_placeholder = True
        doctor.commission_value = Decimal('0')
        doctor.is_active = True
        doctor.save(update_fields=['is_default_placeholder', 'commission_value', 'is_active', 'updated_at'])
    if doctor.appear_on_reports:
        doctor.appear_on_reports = False
        doctor.save(update_fields=['appear_on_reports', 'updated_at'])
    doctor.centres.add(centre)
    return doctor
