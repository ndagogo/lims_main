"""Search and update patient demographics with permission + audit."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils.dateparse import parse_date

from core.access import can_modify_patient_data
from core.models import UserCentreAccess
from patients.models import Patient, TITLE_CHOICES, SEX_CHOICES, RACE_CHOICES, STATE_CHOICES
from patients.registration import apply_guardian, patient_values, validate_local_phone, validate_optional_email


DEMOGRAPHIC_FIELDS = (
    'title', 'first_name', 'last_name', 'other_names', 'phone_country_code', 'phone_number',
    'alt_phone', 'email', 'date_of_birth', 'sex', 'race', 'address', 'city', 'state',
    'health_card_number', 'passport_number',
    'guardian_name', 'guardian_relationship', 'guardian_phone',
)


def _visible_patients(user):
    patients = Patient.objects.all()
    if user.is_superuser:
        return patients
    return patients.filter(
        visits__centre__user_access__user=user,
        visits__centre__user_access__can_view=True,
        visits__centre__is_active=True,
    ).distinct()


def search_patients_for_demographics(user, query):
    query = (query or '').strip()
    if not query:
        return Patient.objects.none()
    qs = _visible_patients(user)
    return qs.filter(
        Q(uhid__iexact=query)
        | Q(phone_number__icontains=query)
        | Q(alt_phone__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(visits__visit_id__iexact=query)
        | Q(visits__invoices__invoice_number__iexact=query)
    ).distinct().order_by('last_name', 'first_name')


def apply_demographic_updates(user, patient, post, request=None):
    if not can_modify_patient_data(user):
        raise PermissionDenied('can_modify_patient_data privilege is required.')
    if not user.is_superuser and not _visible_patients(user).filter(pk=patient.pk).exists():
        raise PermissionDenied('That patient is outside your centres.')

    first = (post.get('first_name') or '').strip()
    last = (post.get('last_name') or '').strip()
    if not first or not last:
        raise ValidationError('First name and last name are required.')
    phone = validate_local_phone(post.get('phone_number'), field_label='Mobile')
    validate_optional_email(post.get('email'))
    # Reuse registration field cleaning but do not rewrite notification consents here.
    values = patient_values(post)
    for key in ('notify_sms', 'notify_email', 'notify_whatsapp'):
        values.pop(key, None)
    dob = parse_date(post.get('date_of_birth') or '') if post.get('date_of_birth') else None

    before = {field: getattr(patient, field) for field in DEMOGRAPHIC_FIELDS}
    if before.get('date_of_birth'):
        before['date_of_birth'] = before['date_of_birth'].isoformat()

    patient.first_name = first
    patient.last_name = last
    patient.phone_number = phone
    patient.date_of_birth = dob
    for key, value in values.items():
        setattr(patient, key, value)
    apply_guardian(patient, post)
    patient.save()

    after = {field: getattr(patient, field) for field in DEMOGRAPHIC_FIELDS}
    if after.get('date_of_birth'):
        after['date_of_birth'] = after['date_of_birth'].isoformat()
    changed = {key: {'from': before[key], 'to': after[key]} for key in DEMOGRAPHIC_FIELDS if before[key] != after[key]}
    if not changed:
        raise ValidationError('No demographic fields were changed.')

    centre = None
    access = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True).select_related('centre').first()
    if access:
        centre = access.centre
    visit = patient.visits.select_related('centre').order_by('-created_at').first()
    if visit:
        centre = visit.centre

    from core.audit import record_audit
    previous = {key: changed[key]['from'] for key in changed}
    new_values = {key: changed[key]['to'] for key in changed}
    record_audit(
        request=request,
        actor=user,
        action='PATIENT_DEMOGRAPHICS_UPDATED',
        object_type='Patient',
        object_id=str(patient.pk),
        centre=centre,
        patient_ref=patient.uhid,
        visit_ref=visit.visit_id if visit else '',
        previous_values=previous,
        new_values=new_values,
        detail={'uhid': patient.uhid, 'changes': changed, 'visit_id': visit.visit_id if visit else ''},
        category='CLINICAL',
    )
    return patient, changed


def demographics_form_choices():
    return {
        'titles': TITLE_CHOICES,
        'sexes': SEX_CHOICES,
        'races': RACE_CHOICES,
        'states': STATE_CHOICES,
    }
