"""Email detailed server errors to vendor/ops without exposing them to clients."""
from __future__ import annotations

import logging
import traceback

from django.conf import settings
from django.core.mail import send_mail
from django.views.debug import ExceptionReporter

logger = logging.getLogger(__name__)


def alert_recipient_emails():
    """Railway ERROR_ALERT_EMAIL / CRITICAL_ALERT_EMAIL, plus optional org profile field."""
    emails = []
    for raw in (
        getattr(settings, 'ERROR_ALERT_EMAIL', ''),
        getattr(settings, 'CRITICAL_ALERT_EMAIL', ''),
    ):
        for part in str(raw or '').split(','):
            address = part.strip()
            if address and address not in emails:
                emails.append(address)
    try:
        from core.organization import get_organization_profile
        profile = get_organization_profile()
        extra = (getattr(profile, 'ops_alert_email', '') or '').strip()
        if extra and extra not in emails:
            emails.append(extra)
    except Exception:  # noqa: BLE001 — never block error handling for branding lookup
        pass
    return emails


def send_error_alert(*, subject, body, html_body=None):
    recipients = alert_recipient_emails()
    if not recipients:
        logger.error('Server error with no ERROR_ALERT_EMAIL configured.\n%s', body)
        return False
    from_email = getattr(settings, 'SERVER_EMAIL', None) or settings.DEFAULT_FROM_EMAIL
    try:
        send_mail(
            subject=subject[:200],
            message=body,
            from_email=from_email,
            recipient_list=recipients,
            fail_silently=False,
            html_message=html_body,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception('Failed to send critical error alert email')
        return False


def report_request_exception(request, exc_info):
    """Build and email a full exception report for an unhandled request error."""
    exc_type, exc_value, tb = exc_info
    reporter = ExceptionReporter(request, exc_type, exc_value, tb)
    path = getattr(request, 'path', '')
    method = getattr(request, 'method', '')
    user = getattr(request, 'user', None)
    username = ''
    if user is not None and getattr(user, 'is_authenticated', False):
        username = getattr(user, 'username', '') or str(user.pk)
    host = request.get_host() if hasattr(request, 'get_host') else ''
    brand = getattr(settings, 'ERROR_ALERT_SUBJECT_PREFIX', '[LIMS]')
    subject = f'{brand} ERROR {method} {path}'
    text = reporter.get_traceback_text()
    header = (
        f'A critical error occurred on the live LIMS.\n'
        f'Clients were shown a safe message only — details below are for the vendor.\n\n'
        f'URL: {method} https://{host}{path}\n'
        f'User: {username or "anonymous"}\n'
        f'Remote IP: {request.META.get("REMOTE_ADDR", "")}\n\n'
    )
    send_error_alert(subject=subject, body=header + text, html_body=reporter.get_traceback_html())


class VendorAlertEmailHandler(logging.Handler):
    """Logging handler that emails ops when django.request logs a 500."""

    def emit(self, record):
        if getattr(settings, 'DEBUG', False):
            return
        if not alert_recipient_emails():
            self.handleError(record)
            return
        request = getattr(record, 'request', None)
        subject_prefix = getattr(settings, 'ERROR_ALERT_SUBJECT_PREFIX', '[LIMS]')
        try:
            if request is not None and record.exc_info:
                report_request_exception(request, record.exc_info)
                return
            body = self.format(record)
            if record.exc_info:
                body += '\n\n' + ''.join(traceback.format_exception(*record.exc_info))
            send_error_alert(
                subject=f'{subject_prefix} {record.getMessage()}'[:200],
                body=body,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)
