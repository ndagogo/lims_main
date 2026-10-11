import os
from datetime import timedelta

from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

from .models import User


def _limit():
    return int(os.getenv('LOGIN_FAILURE_LIMIT', '5'))


def _lock_minutes():
    return int(os.getenv('LOGIN_LOCK_MINUTES', '15'))


class LockoutBackend(ModelBackend):
    """Count failed passwords and lock the account. Successful sign-in clears the count."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        row = User.objects.filter(username=username).first() if username else None
        if row and row.locked_until and row.locked_until <= timezone.now():
            row.failed_login_count = 0
            row.locked_until = None
            row.save(update_fields=['failed_login_count', 'locked_until'])
        if row and row.locked_until and row.locked_until > timezone.now():
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            if row and row.is_active:
                row.failed_login_count += 1
                if row.failed_login_count >= _limit():
                    row.locked_until = timezone.now() + timedelta(minutes=_lock_minutes())
                row.save(update_fields=['failed_login_count', 'locked_until'])
            return None
        user.failed_login_count = 0
        user.locked_until = None
        user.save(update_fields=['failed_login_count', 'locked_until'])
        return user
