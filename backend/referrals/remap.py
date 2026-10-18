"""Remap a visit's referring doctor (DEFAULT or any real doctor) to another physician."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from core.access import can_change_referring_doctor
from core.models import AuditEvent
from patients.models import Patient, Visit

from .models import CommissionLedgerEntry, ReferralAttribution, ReferralDoctor
from .services import sync_commission


def _clean(value, limit):
    return (value or '').strip()[:limit]


def _visible_visits(user):
    visits = Visit.objects.select_related(
        'patient', 'centre', 'referral_attribution__doctor',
    ).prefetch_related('referral_attribution__institutions', 'documents')
    if user.is_superuser:
        return visits
    return visits.filter(
        centre__user_access__user=user,
        centre__user_access__can_view=True,
        centre__is_active=True,
    ).distinct()


def _visible_patients(user):
    patients = Patient.objects.all()
    if user.is_superuser:
        return patients
    return patients.filter(
        visits__centre__user_access__user=user,
        visits__centre__user_access__can_view=True,
        visits__centre__is_active=True,
    ).distinct()


def search_patients_for_remap(user, query):
    """Patients who have at least one visit with a referring-doctor attribution."""
    query = (query or '').strip()
    if not query:
        return Patient.objects.none()
    qs = _visible_patients(user).filter(visits__referral_attribution__isnull=False)
    return qs.filter(
        Q(uhid__iexact=query)
        | Q(phone_number__icontains=query)
        | Q(alt_phone__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(visits__visit_id__iexact=query)
        | Q(visits__invoices__invoice_number__iexact=query)
    ).distinct().order_by('last_name', 'first_name')


# Backwards-compatible alias used by older imports/tests.
search_patients_needing_remap = search_patients_for_remap


def attributions_for_patient(user, patient):
    """All centre-visible visits for this patient that already have a referring doctor."""
    return list(
        _visible_visits(user)
        .filter(patient=patient, referral_attribution__isnull=False)
        .prefetch_related('documents')
        .order_by('-created_at')
    )


# Backwards-compatible alias.
default_attributions_for_patient = attributions_for_patient


REQUEST_FORM_MESSAGE = (
    'Sorry you cannot proceed! Upload Patient Request Form And Try Again. Thanks'
)
SAME_DOCTOR_MESSAGE = 'Hey Check well, you cannot remap to same doctor!'
REQUEST_FORM_DOCUMENT_TYPES = ('REGISTRATION', 'REFERRAL')


def visit_has_request_form(visit) -> bool:
    """True when a registration/referral (patient request) form is filed on the visit."""
    return visit.documents.filter(document_type__in=REQUEST_FORM_DOCUMENT_TYPES).exists()


def real_doctors_for_centre(centre):
    from referrals.services import doctors_for_centre
    return (
        doctors_for_centre(centre)
        .prefetch_related('institutions')
        .order_by('last_name', 'first_name')
    )


def _open_accrual(invoice):
    accruals = CommissionLedgerEntry.objects.filter(
        invoice=invoice, entry_type='ACCRUAL',
    ).order_by('-created_at')
    for entry in accruals:
        if not CommissionLedgerEntry.objects.filter(source_entry=entry, entry_type='REVERSAL').exists():
            return entry
    return None


def _resync_commissions_after_remap(visit, new_doctor, user):
    """Reverse open accruals that belong to another doctor, then accrue for the new mapping."""
    for invoice in visit.invoices.filter(status='PAID'):
        open_accrual = _open_accrual(invoice)
        if open_accrual and open_accrual.doctor_id != new_doctor.pk:
            CommissionLedgerEntry.objects.create(
                doctor=open_accrual.doctor,
                invoice=invoice,
                entry_type='REVERSAL',
                amount=-open_accrual.amount,
                description=f'Reversal after referring doctor remap on {invoice.invoice_number}',
                source_entry=open_accrual,
                created_by=user,
            )
        sync_commission(invoice, user=user)


@transaction.atomic
def remap_referring_doctor(user, visit, post):
    """Change the referring doctor on a visit to another active centre doctor."""
    if not can_change_referring_doctor(user):
        raise PermissionDenied('change_referring_doctor privilege is required.')
    if not _visible_visits(user).filter(pk=visit.pk).exists():
        raise PermissionDenied('That visit is outside your centres.')

    try:
        attribution = visit.referral_attribution
    except ReferralAttribution.DoesNotExist as exc:
        raise ValidationError('This visit has no referring-doctor attribution.') from exc

    if not visit_has_request_form(visit):
        raise ValidationError(REQUEST_FORM_MESSAGE)

    from referrals.services import doctors_for_centre
    doctor = doctors_for_centre(visit.centre).filter(pk=post.get('referral_doctor') or None).first()
    if doctor is None:
        raise ValidationError('Choose a referring doctor who works with this centre.')

    prior = attribution.doctor
    if prior.pk == doctor.pk or prior.doctor_code == doctor.doctor_code:
        raise ValidationError(SAME_DOCTOR_MESSAGE)

    chosen = [value for value in post.getlist('referral_institutions') if value]
    available = doctor.institutions.filter(is_active=True)
    institutions = list(available.filter(pk__in=chosen))
    if available.exists() and not institutions:
        raise ValidationError('Select one or more referral institutions for this doctor.')
    if len(institutions) != len(set(chosen)):
        raise ValidationError('Each referral institution must belong to the selected doctor.')

    prior_institutions = list(attribution.institutions.all())
    prior_note = attribution.note
    prior_institution_names = [row.name for row in prior_institutions]
    new_institution_names = [row.name for row in institutions]
    note = _clean(post.get('referral_note'), 255)
    if not note:
        note = f'Remapped from {prior.doctor_code} to {doctor.doctor_code}'

    attribution.doctor = doctor
    attribution.note = note
    attribution.recorded_by = user
    attribution.save(update_fields=['doctor', 'note', 'recorded_by', 'updated_at'])
    attribution.institutions.set(institutions)

    patient = visit.patient
    from_payload = {
        'doctor_id': str(prior.pk),
        'doctor_code': prior.doctor_code,
        'doctor_name': f'{prior.title} {prior.first_name} {prior.last_name}'.strip(),
        'is_default_placeholder': bool(prior.is_default_placeholder),
        'institutions': prior_institution_names,
        'note': prior_note,
    }
    to_payload = {
        'doctor_id': str(doctor.pk),
        'doctor_code': doctor.doctor_code,
        'doctor_name': f'{doctor.title} {doctor.first_name} {doctor.last_name}'.strip(),
        'is_default_placeholder': False,
        'institutions': new_institution_names,
        'note': note,
    }
    AuditEvent.objects.create(
        actor=user,
        action='REFERRING_DOCTOR_REMAPPED',
        category='CLINICAL',
        object_type='Visit',
        object_id=str(visit.pk),
        centre=visit.centre,
        patient_ref=patient.uhid,
        visit_ref=visit.visit_id,
        previous_values=from_payload,
        new_values=to_payload,
        reason=note,
        detail={
            'visit_id': visit.visit_id,
            'uhid': patient.uhid,
            'patient_name': f'{patient.first_name} {patient.last_name}'.strip(),
            'centre_code': visit.centre.code,
            'remapped_by': user.get_username(),
            'from': from_payload,
            'to': to_payload,
        },
    )

    _resync_commissions_after_remap(visit, doctor, user)
    return attribution


def remap_history(user, *, query='', patient=None, date_from=None, date_to=None):
    """Centre-scoped REFERRING_DOCTOR_REMAPPED events, newest first."""
    from django.db.models import CharField
    from django.db.models.functions import Cast

    rows = AuditEvent.objects.filter(action='REFERRING_DOCTOR_REMAPPED').select_related(
        'actor', 'centre',
    )
    if not user.is_superuser:
        centre_ids = list(
            user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
        )
        rows = rows.filter(Q(centre_id__in=centre_ids) | Q(centre__isnull=True))
    if patient is not None:
        visit_ids = [str(pk) for pk in patient.visits.values_list('pk', flat=True)[:200]]
        rows = rows.filter(
            Q(patient_ref=patient.uhid)
            | Q(object_id__in=visit_ids)
        )
    if date_from:
        rows = rows.filter(created_at__date__gte=date_from)
    if date_to:
        rows = rows.filter(created_at__date__lte=date_to)
    needle = (query or '').strip()
    if needle:
        # Cast JSON to text so search works on SQLite as well as Postgres.
        rows = rows.annotate(
            _detail_text=Cast('detail', CharField()),
            _prev_text=Cast('previous_values', CharField()),
            _new_text=Cast('new_values', CharField()),
        ).filter(
            Q(patient_ref__icontains=needle)
            | Q(visit_ref__icontains=needle)
            | Q(actor__username__icontains=needle)
            | Q(reason__icontains=needle)
            | Q(_detail_text__icontains=needle)
            | Q(_prev_text__icontains=needle)
            | Q(_new_text__icontains=needle)
        )
    return rows.order_by('-created_at')
