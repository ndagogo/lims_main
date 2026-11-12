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
)


def get_organization_profile():
    """Return the singleton organization profile, creating defaults if missing.

    Falls back to in-memory defaults when the table is not migrated yet so a
    missed production migrate cannot take down every page that renders branding.
    """
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from core.models import OrganizationProfile
        profile, _created = OrganizationProfile.objects.get_or_create(pk=1)
    except (OperationalError, ProgrammingError):
        return _DEFAULTS
    cache.set(_CACHE_KEY, profile, timeout=60)
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
