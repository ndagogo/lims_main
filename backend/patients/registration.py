"""Registration form values shared by a new patient and a returning visit."""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from core.models import SequenceCounter
from referrals.models import ReferralAttribution, ReferralDoctor, ReferralInstitution

from .models import CHANNEL_CHOICES, HOW_HEARD_CHOICES, OrganisationClient, RACE_CHOICES, SEX_CHOICES, STATE_CHOICES, TITLE_CHOICES


PHONE_CODES = ['+234', '+233', '+44', '+1', '+27', '+91']
PHONE_DIGIT_RE = re.compile(r'^\d{10}$')


def _clean(value, limit):
    return (value or '').strip()[:limit]


def normalize_phone_digits(value):
    """Keep only digits so users can type spaces or dashes."""
    return re.sub(r'\D', '', value or '')


def validate_local_phone(value, *, field_label='Mobile'):
    digits = normalize_phone_digits(value)
    if not PHONE_DIGIT_RE.fullmatch(digits):
        raise ValidationError(f'{field_label} must be exactly 10 digits (Nigerian number after +234).')
    return digits


def validate_optional_email(value):
    email = _clean(value, 254)
    if not email:
        return ''
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationError('Enter a valid email address.') from exc
    return email


def _checked(post, name):
    return (post.get(name) or '').strip().lower() in {'1', 'on', 'true', 'yes'}


GUARDIAN_RELATIONSHIPS = ('Parent', 'Mother', 'Father', 'Guardian', 'Other')


def patient_is_minor(date_of_birth, today=None):
    if not date_of_birth:
        return False
    from django.utils import timezone
    today = today or timezone.localdate()
    years = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    return years < 18


def apply_guardian(patient, post):
    """Store guardian details. A patient under 18 cannot be saved without them."""
    if 'guardian_name' in post:
        patient.guardian_name = _clean(post.get('guardian_name'), 120)
    if 'guardian_relationship' in post:
        relationship = _clean(post.get('guardian_relationship'), 40)
        patient.guardian_relationship = relationship if relationship in GUARDIAN_RELATIONSHIPS else ''
    if 'guardian_phone' in post:
        raw = (post.get('guardian_phone') or '').strip()
        patient.guardian_phone = validate_local_phone(raw, field_label='Guardian mobile') if raw else ''
    if patient_is_minor(patient.date_of_birth):
        if not (patient.guardian_name and patient.guardian_relationship and patient.guardian_phone):
            raise ValidationError('A patient under 18 needs a guardian name, relationship, and phone.')
    patient.save(update_fields=['guardian_name', 'guardian_relationship', 'guardian_phone', 'updated_at'])
    return patient


def notification_prefs(post, *, patient=None):
    """Read channel consent checkboxes. If none are ticked, SMS remains the default."""
    prefs = {
        'notify_sms': _checked(post, 'notify_sms'),
        'notify_email': _checked(post, 'notify_email'),
        'notify_whatsapp': _checked(post, 'notify_whatsapp'),
    }
    if not any(prefs.values()):
        prefs['notify_sms'] = True
    if 'email' in post:
        email = validate_optional_email(post.get('email'))
    else:
        email = (getattr(patient, 'email', '') or '').strip()
    if prefs['notify_email'] and not email:
        raise ValidationError('Enter an email address when Email notifications are selected.')
    if 'phone_number' in post:
        phone = post.get('phone_number')
    else:
        phone = getattr(patient, 'phone_number', '') or ''
    if (prefs['notify_sms'] or prefs['notify_whatsapp']) and not normalize_phone_digits(phone or ''):
        raise ValidationError('A mobile number is required for SMS or WhatsApp notifications.')
    return prefs


def apply_notification_prefs(patient, post):
    prefs = notification_prefs(post, patient=patient)
    updates = dict(prefs)
    if 'email' in post:
        updates['email'] = validate_optional_email(post.get('email'))
    for key, value in updates.items():
        setattr(patient, key, value)
    patient.save(update_fields=[*updates.keys(), 'updated_at'])
    return patient


def patient_values(post):
    code = _clean(post.get('phone_country_code'), 8) or '+234'
    if code not in PHONE_CODES:
        code = '+234'
    title = _clean(post.get('title'), 20)
    sex = _clean(post.get('sex'), 20)
    race = _clean(post.get('race'), 20)
    state = _clean(post.get('state'), 40)
    alt = _clean(post.get('alt_phone'), 30)
    if alt:
        alt = validate_local_phone(alt, field_label='Alternate mobile')
    prefs = notification_prefs(post)
    return {
        'title': title if title in dict(TITLE_CHOICES) else '',
        'other_names': _clean(post.get('other_names'), 100),
        'phone_country_code': code,
        'alt_phone': alt,
        'email': validate_optional_email(post.get('email')),
        'notify_sms': prefs['notify_sms'],
        'notify_email': prefs['notify_email'],
        'notify_whatsapp': prefs['notify_whatsapp'],
        'sex': sex if sex in dict(SEX_CHOICES) else '',
        'race': race if race in dict(RACE_CHOICES) else '',
        'address': _clean(post.get('address'), 500),
        'city': _clean(post.get('city'), 80),
        'state': state if state in dict(STATE_CHOICES) else '',
        'health_card_number': _clean(post.get('health_card_number'), 40),
        'passport_number': _clean(post.get('passport_number'), 40),
    }


def apply_visit_details(visit, post):
    channel = _clean(post.get('referral_source'), 30) or 'SELF'
    if channel not in dict(CHANNEL_CHOICES):
        channel = 'SELF'
    heard = _clean(post.get('how_heard'), 20)
    visit.referral_source = channel
    visit.clinical_history = _clean(post.get('clinical_history'), 2000)
    visit.comments = _clean(post.get('comments'), 2000)
    visit.trf_number = _clean(post.get('trf_number'), 40)
    visit.reference_1 = _clean(post.get('reference_1'), 40)
    visit.reference_2 = _clean(post.get('reference_2'), 40)
    visit.how_heard = heard if heard in dict(HOW_HEARD_CHOICES) else ''
    if channel in ('CORPORATE', 'HMO'):
        client = OrganisationClient.objects.filter(pk=post.get('client') or None, centre=visit.centre, kind=channel, is_active=True).first()
        if client is None:
            label = 'an HMO' if channel == 'HMO' else 'a corporate client'
            raise ValidationError(f'Select {label} for this centre.')
        visit.client = client
        visit.client_name = client.name
        visit.client_employee_number = _clean(post.get('client_employee_number'), 40)
    else:
        visit.client = None
        visit.client_name = ''
        visit.client_employee_number = ''
    visit.save()
    if channel == 'DOCTOR':
        _attach_referral(visit, post)
    return visit


def _attach_referral(visit, post):
    from referrals.defaults import ensure_default_doctor

    unknown = _checked(post, 'referring_doctor_unknown')
    if unknown:
        doctor = ensure_default_doctor(visit.centre)
        institutions = []
        note = _clean(post.get('referral_note'), 255) or 'Referring doctor unknown at registration'
    else:
        from referrals.services import doctors_for_centre
        doctor = doctors_for_centre(visit.centre).filter(pk=post.get('referral_doctor') or None).first()
        if doctor is None:
            raise ValidationError(
                'Choose a referring doctor who works with this centre, '
                'or tick “Referring doctor unknown” to assign DEFAULT DOCTOR.'
            )
        chosen = [value for value in post.getlist('referral_institutions') if value]
        available = doctor.institutions.filter(is_active=True)
        institutions = list(available.filter(pk__in=chosen))
        if available.exists() and not institutions:
            raise ValidationError('Select one or more referral institutions for this doctor.')
        if len(institutions) != len(set(chosen)):
            raise ValidationError('Each referral institution must belong to the selected doctor.')
        note = _clean(post.get('referral_note'), 255)
    attribution, _created = ReferralAttribution.objects.get_or_create(
        visit=visit, defaults={'doctor': doctor, 'recorded_by': visit.registered_by},
    )
    attribution.doctor = doctor
    attribution.recorded_by = visit.registered_by
    attribution.note = note
    attribution.save()
    attribution.institutions.set(institutions)


def create_doctor(centre, post):
    first = _clean(post.get('doctor_first_name'), 100)
    last = _clean(post.get('doctor_last_name'), 100)
    if not first or not last:
        raise ValidationError('A referring doctor needs a first name and a last name.')
    title = _clean(post.get('doctor_title'), 30)
    with transaction.atomic():
        counter, _created = SequenceCounter.objects.select_for_update().get_or_create(key=f'referral-doctor-{centre.code}')
        counter.value += 1
        counter.save(update_fields=['value'])
        code = f'{centre.code}-DR{counter.value:04d}'
        doctor = ReferralDoctor.objects.create(
            centre=centre, doctor_code=code, title=title, first_name=first, last_name=last,
            phone=_clean(post.get('doctor_phone'), 30), commission_type='PERCENT', commission_value=Decimal('0'),
        )
        doctor.centres.add(centre)
    names = [part.strip() for part in (post.get('institution_names') or '').split(',') if part.strip()]
    for name in names:
        ReferralInstitution.objects.get_or_create(doctor=doctor, name=name[:180])
    return doctor


def add_institution(centre, post):
    from referrals.services import doctors_for_centre
    doctor = doctors_for_centre(centre, include_placeholders=True).filter(pk=post.get('doctor') or None).first()
    name = _clean(post.get('institution_name'), 180)
    if doctor is None or not name:
        raise ValidationError('Choose the doctor and enter the institution name.')
    institution, created = ReferralInstitution.objects.get_or_create(doctor=doctor, name=name)
    if not created and not institution.is_active:
        institution.is_active = True
        institution.save(update_fields=['is_active', 'updated_at'])
    return institution
