"""Patient notification helpers: preferences, templates, and event sends."""
from django.conf import settings
from django.template import Context, Template

from .models import NotificationOutbox, NotificationTemplate
from .services import queue_message

EVENT_REGISTRATION_SUCCESS = 'registration-success'
EVENT_RESULT_READY = 'result-ready'

CHANNEL_PREFS = (
    ('SMS', 'notify_sms'),
    ('EMAIL', 'notify_email'),
    ('WHATSAPP', 'notify_whatsapp'),
)

DEFAULT_BODIES = {
    EVENT_REGISTRATION_SUCCESS: (
        'Hi {{ patient_name }}, thank you for choosing {{ lab_name }}.\n\n'
        'Your tests have been registered under visit {{ visit_id }} (UHID {{ uhid }}). '
        'We are processing your samples and will notify you as soon as your results are ready.\n\n'
        '{% if portal_url %}You can also sign in at {{ portal_url }} with your patient credentials '
        'to view results when they become available.\n\n{% endif %}'
        'Thank you for trusting {{ lab_name }}.'
    ),
    EVENT_RESULT_READY: (
        'Hi {{ patient_name }}, good news from {{ lab_name }}.\n\n'
        'Your result for {{ test_name }} (sample {{ sample_id }}) is ready. '
        'Visit reference: {{ visit_id }}.\n\n'
        '{% if portal_url %}Sign in at {{ portal_url }} to view your results, '
        'or collect them from the centre.\n\n{% endif %}'
        'Thank you for choosing {{ lab_name }}.'
    ),
}

DEFAULT_SUBJECTS = {
    EVENT_REGISTRATION_SUCCESS: 'Registration confirmed — {{ lab_name }}',
    EVENT_RESULT_READY: 'Your results are ready — {{ lab_name }}',
}


def _render(text, context):
    if not text:
        return ''
    return Template(text).render(Context(context)).strip()


def build_patient_context(visit, extra=None):
    patient = visit.patient
    centre = visit.centre
    portal = (getattr(settings, 'PATIENT_PORTAL_URL', '') or '').strip()
    context = {
        'patient_name': f'{patient.first_name} {patient.last_name}'.strip(),
        'patient_first_name': patient.first_name,
        'uhid': patient.uhid,
        'lab_name': centre.name,
        'centre_code': centre.code,
        'centre_phone': centre.phone or '',
        'visit_id': visit.visit_id,
        'portal_url': portal,
        'test_name': '',
        'sample_id': '',
        'invoice_number': '',
    }
    if extra:
        context.update(extra)
    return context


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
    queued = []
    for channel, pref_field in CHANNEL_PREFS:
        if not getattr(patient, pref_field, False):
            continue
        recipient = patient_recipient(patient, channel)
        if not recipient:
            continue
        subject_base, body = render_event_message(event_key, channel, context)
        if not body:
            continue
        subject = idempotency_key or subject_base
        if idempotency_key and NotificationOutbox.objects.filter(
            subject=idempotency_key, channel=channel, visit=visit,
        ).exists():
            continue
        note = queue_message(
            centre=visit.centre,
            channel=channel,
            recipient=recipient,
            body=body,
            subject=subject[:200],
            visit=visit,
            user=user,
        )
        if note:
            queued.append(note)
    return queued
