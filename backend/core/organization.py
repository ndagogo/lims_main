"""Tenant business identity used across UI, printouts, and document numbers."""
from __future__ import annotations

from django.core.cache import cache

_CACHE_KEY = 'core.organization_profile.v1'


def get_organization_profile():
    """Return the singleton organization profile, creating defaults if missing."""
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    from core.models import OrganizationProfile
    profile, _created = OrganizationProfile.objects.get_or_create(pk=1)
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
