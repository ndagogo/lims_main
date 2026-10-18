from django.contrib.auth import authenticate, get_user_model
from django.utils import timezone

User = get_user_model()
LOCKED = 'Your account has been temporarily lock. Please contact your system administrator for assiatnce'


def attempt_login(username, password):
    """Return (user, error). Lockout counting lives in LockoutBackend."""
    username = (username or '').strip()
    row = User.objects.filter(username=username).first()
    if row and row.locked_until and row.locked_until > timezone.now():
        return None, LOCKED
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        if row:
            row.refresh_from_db()
            if row.locked_until and row.locked_until > timezone.now():
                return None, LOCKED
        return None, 'Invalid credentials.'
    return user, None
