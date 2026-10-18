"""Notification helpers: consented channels, templates, and event sends.

Ordinary messages carry the minimum needed to act. They never include passwords
or clinical result values. Result access is a short-lived portal link.
"""
import logging
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.template import Context, Template

from .models import NotificationOutbox, NotificationTemplate
from .services import queue_message

logger = logging.getLogger(__name__)

EVENT_REGISTRATION_SUCCESS = 'registration-success'
EVENT_REFERRAL_RECEIVED = 'referral-received'
EVENT_PAYMENT_RECEIPT = 'payment-receipt'
EVENT_DISCOUNT_REQUESTED = 'discount-requested'
EVENT_DISCOUNT_DECISION = 'discount-decision'
EVENT_RECOLLECTION = 'sample-recollection'
EVENT_RESULT_AUTHENTICATED = 'result-authenticated'
EVENT_RESULT_READY = 'result-ready'
EVENT_WITHDRAWAL_REQUEST = 'referral-withdrawal'
EVENT_PAYOUT_SETTLED = 'referral-payout'
EVENT_INTEGRATION_FAILURE = 'integration-failure'

CHANNEL_PREFS = (
    ('SMS', 'notify_sms'),
    ('EMAIL', 'notify_email'),
    ('WHATSAPP', 'notify_whatsapp'),
)

ALLOWED_TEMPLATE_VARIABLES = frozenset({
    'patient_name', 'centre_name', 'visit_id', 'result_link',
    'lab_name', 'uhid', 'portal_url', 'test_name', 'sample_id',
    'invoice_number', 'receipt_number', 'amount', 'doctor_name',
    'centre_phone', 'patient_first_name', 'centre_code', 'reason',
    'instrument_name',
})
FORBIDDEN_TEMPLATE_VARIABLES = frozenset({
    'password', 'result_value', 'narrative', 'findings', 'diagnosis', 'clinical_history',
})
_VARIABLE = re.compile(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)')
_TAG_NAME = re.compile(r'\{%\s*(?:if|elif)\s+(?:not\s+)?([a-zA-Z_][a-zA-Z0-9_]*)')

DEFAULT_BODIES = {
    EVENT_REGISTRATION_SUCCESS: (
        'Hi {{ patient_name }}, your visit {{ visit_id }} is registered at {{ centre_name }}. '
        'Sign in at {{ portal_url }} when you want to follow this visit.'
    ),
    EVENT_REFERRAL_RECEIVED: (
        'A patient was registered at {{ centre_name }} on visit {{ visit_id }}. '
        'Open the physician portal for the referral. This message does not include clinical results.'
    ),
    EVENT_PAYMENT_RECEIPT: (
        'Hi {{ patient_name }}, {{ centre_name }} received payment for visit {{ visit_id }}. '
        'Receipt {{ receipt_number }} is available in the patient portal.'
    ),
    EVENT_DISCOUNT_REQUESTED: (
        'A discount request needs review for visit {{ visit_id }} at {{ centre_name }} '
        '(invoice {{ invoice_number }}).'
    ),
    EVENT_DISCOUNT_DECISION: (
        'The discount request for visit {{ visit_id }} at {{ centre_name }} '
        '(invoice {{ invoice_number }}) has been updated. Open Accounts to see the decision.'
    ),
    EVENT_RECOLLECTION: (
        'Hi {{ patient_name }}, {{ centre_name }} needs another sample for visit {{ visit_id }}. '
        'Please contact the centre. Reason: {{ reason }}.'
    ),
    EVENT_RESULT_AUTHENTICATED: (
        'A result for visit {{ visit_id }} at {{ centre_name }} has been authenticated '
        'and is waiting for authorised release. This message does not include the result.'
    ),
    EVENT_RESULT_READY: (
        'Hi {{ patient_name }}, a result for visit {{ visit_id }} at {{ centre_name }} is ready. '
        'View it with this secure link: {{ result_link }}'
    ),
    EVENT_WITHDRAWAL_REQUEST: (
        'A referral payout request needs review at {{ centre_name }} for Dr {{ doctor_name }}.'
    ),
    EVENT_PAYOUT_SETTLED: (
        'Your referral payout from {{ centre_name }} has been settled. '
        'Sign in to the physician portal to see the ledger.'
    ),
    EVENT_INTEGRATION_FAILURE: (
        'Instrument {{ instrument_name }} at {{ centre_name }} failed to process a message. '
        'Review it in the integration queue. The instrument payload is not included here.'
    ),
}

DEFAULT_SUBJECTS = {
    EVENT_REGISTRATION_SUCCESS: 'Registration confirmed — {{ centre_name }}',
    EVENT_REFERRAL_RECEIVED: 'Referral received — {{ centre_name }}',
    EVENT_PAYMENT_RECEIPT: 'Payment receipt — {{ centre_name }}',
    EVENT_DISCOUNT_REQUESTED: 'Discount request — {{ visit_id }}',
    EVENT_DISCOUNT_DECISION: 'Discount decision — {{ visit_id }}',
    EVENT_RECOLLECTION: 'Sample recollection — {{ centre_name }}',
    EVENT_RESULT_AUTHENTICATED: 'Result authenticated — {{ visit_id }}',
    EVENT_RESULT_READY: 'Your result is ready — {{ centre_name }}',
    EVENT_WITHDRAWAL_REQUEST: 'Referral withdrawal request — {{ centre_name }}',
    EVENT_PAYOUT_SETTLED: 'Referral payout settled — {{ centre_name }}',
    EVENT_INTEGRATION_FAILURE: 'Integration failure — {{ centre_name }}',
}


def validate_template_text(text):
    """Reject unknown, password, or clinical-result variables before a template is published."""
    text = text or ''
    lowered = text.lower()
    if 'password' in lowered:
        raise ValidationError('Templates must not contain passwords.')
    names = set(_VARIABLE.findall(text)) | set(_TAG_NAME.findall(text))
    forbidden = names & FORBIDDEN_TEMPLATE_VARIABLES
    if forbidden:
        raise ValidationError(f'Templates must not include sensitive variables: {", ".join(sorted(forbidden))}.')
    unknown = names - ALLOWED_TEMPLATE_VARIABLES
    if unknown:
        allowed = ', '.join(sorted(ALLOWED_TEMPLATE_VARIABLES))
        raise ValidationError(f'Unknown template variable(s): {", ".join(sorted(unknown))}. Allowed: {allowed}.')


def _render(text, context):
    if not text:
        return ''
    return Template(text).render(Context(context, autoescape=False)).strip()


def build_patient_context(visit, extra=None):
    patient = visit.patient
    centre = visit.centre
    portal = (getattr(settings, 'PUBLIC_APP_URL', '') or getattr(settings, 'PATIENT_PORTAL_URL', '') or '').strip().rstrip('/')
    context = {
        'patient_name': f'{patient.first_name} {patient.last_name}'.strip(),
        'patient_first_name': patient.first_name,
        'uhid': patient.uhid,
        'lab_name': centre.name,
        'centre_name': centre.name,
        'centre_code': centre.code,
        'centre_phone': centre.phone or '',
        'visit_id': visit.visit_id,
        'portal_url': f'{portal}/portals/patient/login/' if portal else '',
        'result_link': '',
        'test_name': '',
        'sample_id': '',
        'invoice_number': '',
        'receipt_number': '',
        'amount': '',
        'doctor_name': '',
        'reason': '',
        'instrument_name': '',
    }
    if extra:
        context.update(extra)
    return context


def issue_result_link(patient):
    from portals.models import PortalAccessToken
    token = PortalAccessToken.issue('PATIENT', patient=patient, hours=2)
    base = (getattr(settings, 'PUBLIC_APP_URL', '') or '').strip().rstrip('/')
    return f'{base}/portals/patient/access/{token.token}/'


def patient_recipient(patient, channel):
    if channel == 'EMAIL':
        return (patient.email or '').strip()
    code = (patient.phone_country_code or '').strip() or '+234'
    local = (patient.phone_number or '').strip()
    if not local:
        return ''
    if local.startswith('+'):
        return local
    return f'{code}{local}'


def template_key(event_key, channel):
    return f'{event_key}-{channel.lower()}'


def render_event_message(event_key, channel, context):
    row = NotificationTemplate.objects.filter(
        key=template_key(event_key, channel), is_active=True,
    ).first()
    if row:
        subject = _render(row.subject_template, context)
        body = _render(row.body_template, context)
        return subject, body
    subject = _render(DEFAULT_SUBJECTS.get(event_key, 'HFDN notification'), context)
    body = _render(DEFAULT_BODIES.get(event_key, ''), context)
    return subject, body


def notify_patient(*, event_key, visit, user=None, idempotency_key=None, extra=None):
    """Queue one message per consented channel that has a usable recipient."""
    patient = visit.patient
    context = build_patient_context(visit, extra)
    if event_key == EVENT_RESULT_READY and not context.get('result_link'):
        context['result_link'] = issue_result_link(patient)
    queued = []
    for channel, pref_field in CHANNEL_PREFS:
        if not getattr(patient, pref_field, False):
            continue
        recipient = patient_recipient(patient, channel)
        if not recipient:
            continue
        subject, body = render_event_message(event_key, channel, context)
        if not body:
            continue
        key = f'{idempotency_key}:{channel}' if idempotency_key else ''
        note = queue_message(
            centre=visit.centre,
            channel=channel,
            recipient=recipient,
            body=body,
            subject=subject[:200],
            visit=visit,
            user=user,
            idempotency_key=key,
        )
        if note:
            queued.append(note)
    return queued


def notify_address(*, centre, channel, recipient, event_key, context, user=None, visit=None, idempotency_key=''):
    if not recipient:
        return None
    subject, body = render_event_message(event_key, channel, context)
    if not body:
        return None
    return queue_message(
        centre=centre, channel=channel, recipient=recipient, body=body,
        subject=subject[:200], visit=visit, user=user, idempotency_key=idempotency_key,
    )


def notify_staff(*, centre, subject, body, roles=None, user=None, event_key=None, context=None, idempotency_key=''):
    """Queue email alerts to staff with matching roles at a centre."""
    from django.contrib.auth import get_user_model
    from core.models import UserCentreAccess
    User = get_user_model()
    role_names = tuple(roles or ('Accounts', 'Centre Administrator', 'Platform Administrator'))
    access_ids = UserCentreAccess.objects.filter(centre=centre, can_view=True).values_list('user_id', flat=True)
    recipients = User.objects.filter(id__in=access_ids, is_active=True, groups__name__in=role_names).distinct()
    queued = []
    for staff in recipients:
        email = (staff.email or '').strip()
        if not email:
            continue
        if event_key and context is not None:
            note = notify_address(
                centre=centre, channel='EMAIL', recipient=email, event_key=event_key, context=context,
                user=user, idempotency_key=f'{idempotency_key}:{staff.pk}' if idempotency_key else '',
            )
        else:
            note = queue_message(
                centre=centre, channel='EMAIL', recipient=email, body=body, subject=(subject or '')[:200], user=user,
                idempotency_key=f'{idempotency_key}:{staff.pk}' if idempotency_key else '',
            )
        if note:
            queued.append(note)
    return queued


def _safe(label, func):
    try:
        return func()
    except Exception:
        logger.exception('Notification %s was not queued', label)
        return []


def notify_referral_received(visit, user=None):
    def send():
        attribution = getattr(visit, 'referral_attribution', None)
        if attribution is None or attribution.doctor.is_default_placeholder:
            return []
        doctor = attribution.doctor
        context = build_patient_context(visit, {
            'doctor_name': f'{doctor.first_name} {doctor.last_name}'.strip(),
        })
        channel = 'EMAIL' if (doctor.email or '').strip() else 'SMS'
        recipient = (doctor.email or '').strip() or (doctor.phone or '').strip()
        note = notify_address(
            centre=visit.centre, channel=channel, recipient=recipient,
            event_key=EVENT_REFERRAL_RECEIVED, context=context, user=user, visit=visit,
            idempotency_key=f'referral-received:{visit.pk}',
        )
        return [note] if note else []
    return _safe('referral-received', send)


def notify_payment_receipt(payment, user=None):
    def send():
        invoice = payment.invoice
        visit = invoice.visit
        return notify_patient(
            event_key=EVENT_PAYMENT_RECEIPT, visit=visit, user=user,
            idempotency_key=f'payment-receipt:{payment.pk}',
            extra={
                'invoice_number': invoice.invoice_number,
                'receipt_number': payment.receipt_number,
                'amount': str(payment.amount),
            },
        )
    return _safe('payment-receipt', send)


def notify_discount_approvers(discount, user=None):
    invoice = discount.invoice
    visit = invoice.visit
    context = build_patient_context(visit, {'invoice_number': invoice.invoice_number})
    return notify_staff(
        centre=visit.centre, subject='', body='',
        roles=('Accounts', 'Centre Administrator'), user=user,
        event_key=EVENT_DISCOUNT_REQUESTED, context=context,
        idempotency_key=f'discount-requested:{discount.pk}',
    )


def notify_discount_decision(discount, user=None):
    def send():
        invoice = discount.invoice
        visit = invoice.visit
        requester = discount.requested_by
        email = (getattr(requester, 'email', '') or '').strip()
        context = build_patient_context(visit, {'invoice_number': invoice.invoice_number})
        note = notify_address(
            centre=visit.centre, channel='EMAIL', recipient=email,
            event_key=EVENT_DISCOUNT_DECISION, context=context, user=user, visit=visit,
            idempotency_key=f'discount-decision:{discount.pk}',
        )
        return [note] if note else []
    return _safe('discount-decision', send)


def notify_recollection_staff(visit, user=None, *, sample_id='', reason=''):
    context = build_patient_context(visit, {'sample_id': sample_id, 'reason': reason, 'test_name': ''})
    return notify_staff(
        centre=visit.centre, subject='', body='',
        roles=('Phlebotomist', 'Lab Manager'), user=user,
        event_key=EVENT_RECOLLECTION, context=context,
        idempotency_key=f'recollection-staff:{sample_id}',
    )


def notify_result_authenticated(result, user=None):
    def send():
        visit = result.sample.order.visit
        context = build_patient_context(visit, {
            'test_name': result.sample.order.test.name,
            'sample_id': result.sample.sample_id,
        })
        return notify_staff(
            centre=visit.centre, subject='', body='',
            roles=('Lab Manager', 'Laboratory Scientist'), user=user,
            event_key=EVENT_RESULT_AUTHENTICATED, context=context,
            idempotency_key=f'result-authenticated:{result.pk}',
        )
    return _safe('result-authenticated', send)


def notify_withdrawal_request(withdrawal, user=None):
    def send():
        doctor = withdrawal.doctor
        context = {
            'centre_name': doctor.centre.name,
            'doctor_name': f'{doctor.first_name} {doctor.last_name}'.strip(),
            'amount': str(withdrawal.amount),
            'visit_id': '', 'patient_name': '', 'result_link': '',
        }
        return notify_staff(
            centre=doctor.centre, subject='', body='',
            roles=('Accounts', 'Centre Administrator'), user=user,
            event_key=EVENT_WITHDRAWAL_REQUEST, context=context,
            idempotency_key=f'withdrawal:{withdrawal.pk}',
        )
    return _safe('withdrawal', send)


def notify_payout_settled(withdrawal, user=None):
    def send():
        doctor = withdrawal.doctor
        context = {
            'centre_name': doctor.centre.name,
            'doctor_name': f'{doctor.first_name} {doctor.last_name}'.strip(),
            'amount': str(withdrawal.amount),
            'visit_id': '', 'patient_name': '', 'result_link': '', 'portal_url': '',
        }
        channel = 'EMAIL' if (doctor.email or '').strip() else 'SMS'
        recipient = (doctor.email or '').strip() or (doctor.phone or '').strip()
        note = notify_address(
            centre=doctor.centre, channel=channel, recipient=recipient,
            event_key=EVENT_PAYOUT_SETTLED, context=context, user=user,
            idempotency_key=f'payout:{withdrawal.pk}',
        )
        return [note] if note else []
    return _safe('payout', send)


def notify_integration_failure(message, user=None):
    def send():
        centre = message.analyzer.centre
        context = {
            'centre_name': centre.name,
            'instrument_name': message.analyzer.name,
            'visit_id': '', 'patient_name': '', 'result_link': '',
        }
        return notify_staff(
            centre=centre, subject='', body='',
            roles=('Platform Administrator', 'Centre Administrator'), user=user,
            event_key=EVENT_INTEGRATION_FAILURE, context=context,
            idempotency_key=f'integration:{message.pk}',
        )
    return _safe('integration-failure', send)
