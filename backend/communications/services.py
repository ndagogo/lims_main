"""Hand queued notifications to a configured provider. Nothing here releases a clinical result."""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import NotificationOutbox

logger = logging.getLogger(__name__)


class ProviderNotConfigured(Exception):
    pass


def queue_message(*, centre, channel, recipient, body, subject='', visit=None, user=None, idempotency_key=''):
    if not recipient or not str(recipient).strip():
        return None
    key = (idempotency_key or '')[:120]
    if key:
        existing = NotificationOutbox.objects.filter(idempotency_key=key, channel=channel).first()
        if existing:
            return existing
    note = NotificationOutbox.objects.create(
        centre=centre, visit=visit, channel=channel, recipient=str(recipient).strip()[:255],
        subject=(subject or '')[:200], body=body, created_by=user, status='QUEUED',
        idempotency_key=key,
    )
    return dispatch_one(note)


def dispatch_pending(limit=50):
    notes = NotificationOutbox.objects.filter(status='QUEUED', attempts__lt=5).order_by('created_at')[:limit]
    return [dispatch_one(note) for note in notes]


def dispatch_one(note):
    if note.status == 'SENT' or note.attempts >= 5:
        if note.attempts >= 5 and note.status != 'SENT':
            note.status = 'FAILED'
            note.save(update_fields=['status', 'updated_at'])
        return note
    note.attempts += 1
    note.status = 'SENDING'
    note.save(update_fields=['attempts', 'status', 'updated_at'])
    try:
        provider_id = _handoff(note)
    except ProviderNotConfigured as exc:
        note.status = 'QUEUED'
        note.last_error = str(exc)[:1000]
        note.save(update_fields=['status', 'last_error', 'updated_at'])
        return note
    except Exception as exc:
        note.status = 'FAILED' if note.attempts >= 5 else 'QUEUED'
        note.last_error = type(exc).__name__[:1000]
        note.save(update_fields=['status', 'last_error', 'updated_at'])
        logger.warning('Notification %s was not accepted (%s)', note.pk, type(exc).__name__)
        return note
    note.status = 'SENT'
    note.provider_message_id = str(provider_id)[:200]
    note.sent_at = timezone.now()
    note.last_error = ''
    note.save(update_fields=['status', 'provider_message_id', 'sent_at', 'last_error', 'updated_at'])
    return note


def _handoff(note):
    if note.channel == 'EMAIL':
        if settings.EMAIL_HOST or 'console' in settings.EMAIL_BACKEND or 'locmem' in settings.EMAIL_BACKEND:
            send_mail(note.subject or 'HFDN notification', note.body, settings.DEFAULT_FROM_EMAIL, [note.recipient], fail_silently=False)
            return f'email:{note.pk}'
        if settings.NOTIFICATION_LOG_PROVIDER:
            logger.info('Email notification %s handed to the log provider', note.pk)
            return f'log:{note.pk}'
        raise ProviderNotConfigured('Email provider is not configured.')
    if note.channel == 'SMS':
        if settings.SMS_PROVIDER_URL:
            return _post(settings.SMS_PROVIDER_URL, settings.SMS_PROVIDER_TOKEN, note)
        if settings.NOTIFICATION_LOG_PROVIDER:
            logger.info('SMS notification %s handed to the log provider', note.pk)
            return f'log:{note.pk}'
        raise ProviderNotConfigured('SMS provider URL is not configured.')
    if note.channel == 'WHATSAPP':
        if settings.WHATSAPP_PROVIDER_URL:
            return _post(settings.WHATSAPP_PROVIDER_URL, settings.WHATSAPP_PROVIDER_TOKEN, note)
        if settings.NOTIFICATION_LOG_PROVIDER:
            logger.info('WhatsApp notification %s handed to the log provider', note.pk)
            return f'log:{note.pk}'
        raise ProviderNotConfigured('WhatsApp provider URL is not configured.')
    raise ProviderNotConfigured('Unknown notification channel.')


def _post(url, token, note):
    payload = json.dumps({'id': str(note.pk), 'to': note.recipient, 'subject': note.subject, 'body': note.body}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError(f'Provider returned HTTP {response.status}')
            return f'http:{response.status}:{note.pk}'
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Provider returned HTTP {exc.code}') from exc
