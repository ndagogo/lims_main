"""Email-based staff password reset (token + expiry)."""
from datetime import timedelta

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from core.models import AuditEvent, User

token_generator = PasswordResetTokenGenerator()


def request_password_reset(email, *, request=None):
    user = User.objects.filter(email__iexact=(email or '').strip(), is_active=True).first()
    if not user:
        return False
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = token_generator.make_token(user)
    base = (getattr(settings, 'PUBLIC_APP_URL', '') or '').rstrip('/') or 'http://127.0.0.1:8000'
    link = f'{base}/account/password-reset/confirm/{uid}/{token}/'
    send_mail(
        'Reset your HFDN password',
        f'Use this link within 24 hours to set a new password:\n\n{link}\n\nIf you did not request this, ignore the email.',
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )
    AuditEvent.objects.create(
        actor=user, action='PASSWORD_RESET_REQUESTED', object_type='User', object_id=str(user.pk),
        path=getattr(request, 'path', ''), method='POST',
        ip_address=getattr(request, 'audit_ip', None), browser=getattr(request, 'audit_browser', ''),
    )
    return True


def reset_password_with_token(uidb64, token, new_password):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id, is_active=True)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return None, 'Invalid or expired reset link.'
    if not token_generator.check_token(user, token):
        return None, 'Invalid or expired reset link.'
    if len(new_password or '') < 12:
        return None, 'Password must be at least 12 characters.'
    user.set_password(new_password)
    user.must_change_password = False
    user.last_password_change = timezone.now()
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=['password', 'must_change_password', 'last_password_change', 'failed_login_count', 'locked_until'])
    AuditEvent.objects.create(actor=user, action='PASSWORD_RESET_COMPLETED', object_type='User', object_id=str(user.pk))
    return user, None
