"""Tenant business identity used across UI, printouts, and document numbers."""
from __future__ import annotations

from types import SimpleNamespace

from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError

_CACHE_KEY = 'core.organization_profile.v1'

_DEFAULTS = SimpleNamespace(
    pk=1,
    legal_name='Health Focus Diagnostics Nigeria',
    short_name='HFDN',
    document_prefix='HFDN',
    tagline='LIS · RIS',
    ops_alert_email='',
    idle_timeout_minutes=10,
    is_fallback=True,
)


def organization_db_ready():
    """True when OrganizationProfile table/columns match the current model."""
    try:
        from core.models import OrganizationProfile
        OrganizationProfile.objects.filter(pk=1).values_list('pk', flat=True).first()
        return True
    except (OperationalError, ProgrammingError):
        return False


def get_organization_profile():
    """Return the singleton organization profile, creating defaults if missing.

    Falls back to in-memory defaults when migrations are pending so branding
    pages do not 500. Writes must use save_organization_profile().
    """
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from core.models import OrganizationProfile
        profile, _created = OrganizationProfile.objects.get_or_create(pk=1)
        # Mark as a real row so callers can distinguish from the fallback.
        profile.is_fallback = False
    except (OperationalError, ProgrammingError):
        return _DEFAULTS
    cache.set(_CACHE_KEY, profile, timeout=60)
    return profile


def save_organization_profile(**fields):
    """Persist organization fields. Raises ProgrammingError/OperationalError if not migrated."""
    from core.models import OrganizationProfile
    profile, _created = OrganizationProfile.objects.get_or_create(pk=1)
    for key, value in fields.items():
        setattr(profile, key, value)
    profile.save()
    clear_organization_cache()
    return profile


def clear_organization_cache():
    cache.delete(_CACHE_KEY)


def organization_legal_name():
    return get_organization_profile().legal_name


def organization_short_name():
    return get_organization_profile().short_name


def organization_tagline():
    return get_organization_profile().tagline or 'LIS · RIS'


def document_prefix():
    return get_organization_profile().document_prefix or 'ORG'


def idle_timeout_minutes():
    """Staff inactivity logout minutes (0 = disabled). Default 10."""
    try:
        value = int(getattr(get_organization_profile(), 'idle_timeout_minutes', 10) or 0)
    except (TypeError, ValueError):
        return 10
    return max(0, min(value, 24 * 60))
