"""Client-safe HTTP error pages. Technical details go to ERROR_ALERT_EMAIL only."""
from django.shortcuts import render


def _safe_brand(request):
    try:
        from core.organization import organization_short_name
        return organization_short_name()
    except Exception:  # noqa: BLE001
        return 'LIMS'


def server_error(request, template_name='500.html'):
    """Unhandled server error — friendly page for users; email has the traceback."""
    return render(
        request,
        template_name,
        {
            'org_short_name': _safe_brand(request),
            'support_hint': 'Contact your vendor / system administrator.',
        },
        status=500,
    )


def permission_denied(request, exception=None, template_name='403.html'):
    return render(
        request,
        template_name,
        {
            'org_short_name': _safe_brand(request),
            'exception': exception,
        },
        status=403,
    )


def page_not_found(request, exception=None, template_name='404.html'):
    return render(
        request,
        template_name,
        {
            'org_short_name': _safe_brand(request),
            'exception': exception,
        },
        status=404,
    )


def bad_request(request, exception=None, template_name='400.html'):
    return render(
        request,
        template_name,
        {
            'org_short_name': _safe_brand(request),
            'exception': exception,
        },
        status=400,
    )
