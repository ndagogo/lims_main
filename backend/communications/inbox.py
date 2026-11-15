"""In-app notices for staff, patients, physicians, and corporate accounts.

Outbound SMS, email, and WhatsApp stay on NotificationOutbox. This module is
the bell the person sees while they are already in the app.
"""
import logging

from django.db import IntegrityError, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse
from django.utils import timezone

from .models import AppNotification

logger = logging.getLogger(__name__)

PATIENT_COPY = {
    'result-ready': (
        'Your result is ready',
        'A released report is waiting in your portal.',
        '/portals/patient/#results',
    ),
    'payment-receipt': (
        'Receipt ready',
        'A payment receipt is available to download.',
        '/portals/patient/#billing',
    ),
    'registration-success': (
        'Visit registered',
        'Your visit has been registered.',
        '/portals/patient/#visits',
    ),
}


def announce(func):
    """Save a notice without letting a display failure undo the real action."""
    try:
        return func()
    except Exception:
        logger.exception('In-app notice was not saved')
        return None


def safe_path(url):
    url = (url or '').strip()
    if url.startswith('/') and not url.startswith('//') and '\\' not in url:
        return url[:300]
    return ''


def push(*, title, body='', url='', event_key='', idempotency_key='', user=None, patient=None, doctor=None, organisation=None, centre=None):
    """Write one notice for exactly one recipient."""
    recipients = [person for person in (user, patient, doctor, organisation) if person is not None]
    if len(recipients) != 1:
        raise ValueError('An in-app notice needs exactly one recipient.')
    if user is not None and not getattr(user, 'is_active', True):
        return None
    key = (idempotency_key or '').strip()[:160] or None
    if key:
        existing = AppNotification.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return existing
    try:
        with transaction.atomic():
            return AppNotification.objects.create(
                user=user,
                patient=patient,
                doctor=doctor,
                organisation=organisation,
                centre=centre,
                title=(title or 'Update')[:160],
                body=(body or '')[:400],
                url=safe_path(url),
                event_key=(event_key or '')[:80],
                idempotency_key=key,
            )
    except IntegrityError:
        if key:
            return AppNotification.objects.filter(idempotency_key=key).first()
        raise


def push_staff(centre, *, title, body, url, event_key, idempotency_key='', roles=(), perm='', exclude_user=None):
    """Notify active staff who can see this centre and match a role or privilege."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from core.models import UserCentreAccess

    User = get_user_model()
    access_ids = UserCentreAccess.objects.filter(centre=centre, can_view=True).values_list('user_id', flat=True)
    people = User.objects.filter(id__in=access_ids, is_active=True)
    gate = Q()
    if roles:
        gate |= Q(groups__name__in=tuple(roles))
    if perm and '.' in perm:
        app_label, code = perm.split('.', 1)
        gate |= Q(user_permissions__codename=code, user_permissions__content_type__app_label=app_label)
        gate |= Q(groups__permissions__codename=code, groups__permissions__content_type__app_label=app_label)
    if gate:
        people = people.filter(gate)
    if exclude_user is not None and getattr(exclude_user, 'pk', None):
        people = people.exclude(pk=exclude_user.pk)
    saved = []
    for person in people.distinct():
        key = f'{idempotency_key}:{person.pk}' if idempotency_key else ''
        note = push(
            user=person, title=title, body=body, url=url, event_key=event_key,
            idempotency_key=key, centre=centre,
        )
        if note is not None:
            saved.append(note)
    return saved


def _person_name(user):
    if user is None:
        return ''
    return (user.get_full_name() or '').strip() or user.get_username()


def _study_phrase(exam):
    order = exam.order
    patient = order.visit.patient
    name = f'{patient.first_name} {patient.last_name}'.strip() or 'A patient'
    procedure = getattr(order, 'procedure', None)
    if procedure is not None and getattr(procedure, 'name', ''):
        return f'{procedure.name} for {name}'
    if order.modality_id and order.modality.name:
        return f'{order.modality.name} for {name}'
    return name


def record_study_assigned(exam, radiologist, *, substitute=False):
    if radiologist is None:
        return None
    phrase = _study_phrase(exam)
    stamp = timezone.now().strftime('%Y%m%d%H%M')
    if substitute:
        title = 'Substitute reporting'
        body = f'{phrase} was assigned to you to report.'
        event = 'study-substitute'
    else:
        title = 'New study assigned'
        body = f'{phrase} is on your worklist.'
        event = 'study-assigned'
    return push(
        user=radiologist, title=title, body=body, url='/radiology/',
        event_key=event, centre=exam.order.visit.centre,
        idempotency_key=f'{event}:{exam.pk}:{radiologist.pk}:{stamp}',
    )


def record_study_ready(exam):
    radiologist = exam.assigned_radiologist
    if radiologist is None:
        return None
    return push(
        user=radiologist,
        title='Study ready to report',
        body=f'{_study_phrase(exam)} has been uploaded and is waiting for you.',
        url='/radiology/',
        event_key='study-ready',
        centre=exam.order.visit.centre,
        idempotency_key=f'study-ready:{exam.pk}:{radiologist.pk}',
    )


def record_discount_request(discount, actor=None):
    invoice = discount.invoice
    visit = invoice.visit
    if discount.discount_type == 'PERCENT':
        amount = f'{discount.requested_value:g}%'
    else:
        amount = f'{discount.requested_value:g}'
    who = _person_name(discount.requested_by)
    body = f'{who} asked for {amount} on {invoice.invoice_number}.' if who else f'{amount} on {invoice.invoice_number} is waiting for review.'
    return push_staff(
        visit.centre,
        roles=('Accounts', 'Centre Administrator'),
        perm='billing.approve_discount',
        title='Discount request',
        body=body,
        url='/accounts/',
        event_key='discount-requested',
        idempotency_key=f'discount-requested:{discount.pk}',
        exclude_user=actor or discount.requested_by,
    )


def record_discount_decision(discount):
    requester = discount.requested_by
    if requester is None:
        return None
    if discount.status == 'APPROVED':
        verb = 'approved'
    elif discount.status == 'REJECTED':
        verb = 'declined'
    else:
        verb = discount.status.replace('_', ' ').lower()
    return push(
        user=requester,
        title=f'Discount {verb}',
        body=f'{discount.invoice.invoice_number} was {verb}.',
        url='/accounts/',
        event_key='discount-decision',
        centre=discount.invoice.visit.centre,
        idempotency_key=f'discount-decision:{discount.pk}',
    )


def record_patient_event(visit, event_key, idempotency_key=None, extra=None):
    extra = extra or {}
    title, body, url = PATIENT_COPY.get(event_key, (
        'Update',
        'There is a new update in your portal.',
        '/portals/patient/',
    ))
    if event_key == 'payment-receipt' and extra.get('receipt_number'):
        body = f'Receipt {extra["receipt_number"]} is ready to download.'
    elif event_key == 'result-ready' and extra.get('test_name'):
        body = f'{extra["test_name"]} is ready in your portal.'
    key = (idempotency_key or f'{event_key}:{visit.pk}').strip()
    return push(
        patient=visit.patient, title=title, body=body, url=url,
        event_key=event_key, centre=visit.centre,
        idempotency_key=f'inapp:{key}',
    )


def record_referral(visit, doctor):
    if doctor is None or getattr(doctor, 'is_default_placeholder', False):
        return None
    return push(
        doctor=doctor,
        title='New referral',
        body=f'A patient was registered at {visit.centre.name}.',
        url='/portals/physician/',
        event_key='referral-received',
        centre=visit.centre,
        idempotency_key=f'referral-received:{visit.pk}',
    )


def record_corporate_invoice(invoice):
    visit = invoice.visit
    if not visit.client_id:
        return None
    return push(
        organisation=visit.client,
        title='New invoice',
        body=f'{invoice.invoice_number} was issued for visit {visit.visit_id}.',
        url='/portals/corporate/',
        event_key='invoice-issued',
        centre=visit.centre,
        idempotency_key=f'invoice-issued:{invoice.pk}',
    )


def record_corporate_payment(payment):
    invoice = payment.invoice
    visit = invoice.visit
    if not visit.client_id:
        return None
    return push(
        organisation=visit.client,
        title='Payment recorded',
        body=f'{payment.receipt_number} on {invoice.invoice_number}.',
        url='/portals/corporate/',
        event_key='corporate-receipt',
        centre=visit.centre,
        idempotency_key=f'corporate-receipt:{payment.pk}',
    )


def _support_label(row):
    kind = row.request_type.name if row.request_type_id else 'Support'
    return f'{row.reference} · {kind}'


def record_support_opened(row):
    url = reverse('support-case', args=[row.pk])
    title = 'New patient request'
    body = _support_label(row)
    if row.assignee_id:
        return push(
            user=row.assignee, title=title, body=body, url=url,
            event_key='support-opened', centre=row.centre,
            idempotency_key=f'support-opened:{row.pk}:{row.assignee_id}',
        )
    if row.centre_id:
        return push_staff(
            row.centre,
            roles=('Centre Administrator', 'Platform Administrator'),
            perm='portals.patient_support',
            title=title, body=body, url=url,
            event_key='support-opened',
            idempotency_key=f'support-opened:{row.pk}',
        )
    return None


def record_support_assigned(row, assignee):
    if assignee is None:
        return None
    return push(
        user=assignee,
        title='Request assigned to you',
        body=_support_label(row),
        url=reverse('support-case', args=[row.pk]),
        event_key='support-assigned',
        centre=row.centre,
        idempotency_key=f'support-assigned:{row.pk}:{assignee.pk}:{timezone.now().strftime("%Y%m%d%H%M")}',
    )


def record_support_patient_reply(row):
    title = 'Patient replied'
    body = f'{row.reference} has a new reply.'
    url = reverse('support-case', args=[row.pk])
    if row.assignee_id:
        return push(
            user=row.assignee, title=title, body=body, url=url,
            event_key='support-patient-reply', centre=row.centre,
        )
    if row.centre_id:
        return push_staff(
            row.centre,
            roles=('Centre Administrator', 'Platform Administrator'),
            perm='portals.patient_support',
            title=title, body=body, url=url, event_key='support-patient-reply',
        )
    return None


def record_support_for_patient(row, *, title, body):
    if row.patient_id is None:
        return None
    return push(
        patient=row.patient, title=title, body=(body or '')[:400],
        url='/portals/patient/#help', event_key='support-update', centre=row.centre,
    )


def portal_token(request):
    session = getattr(request, 'session', None)
    raw = session.get('portal_token') if session is not None else ''
    if not raw:
        return None
    from portals.models import PortalAccessToken
    token = PortalAccessToken.objects.filter(token=raw, is_active=True).first()
    if token is None or not token.is_valid():
        return None
    return token


def has_audience(request):
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return True
    return portal_token(request) is not None


def notices_for(request):
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return AppNotification.objects.filter(user=user)
    token = portal_token(request)
    if token is None:
        return AppNotification.objects.none()
    if token.patient_id:
        return AppNotification.objects.filter(patient_id=token.patient_id)
    if token.doctor_id:
        return AppNotification.objects.filter(doctor_id=token.doctor_id)
    if token.organisation_id:
        return AppNotification.objects.filter(organisation_id=token.organisation_id)
    return AppNotification.objects.none()


def notification_tray(request):
    empty = {'notice_audience': False, 'notice_unread': 0, 'notice_items': []}
    try:
        if not has_audience(request):
            return empty
        qs = notices_for(request)
        return {
            'notice_audience': True,
            'notice_unread': qs.filter(read_at__isnull=True).count(),
            'notice_items': list(qs[:8]),
        }
    except (OperationalError, ProgrammingError):
        return empty
