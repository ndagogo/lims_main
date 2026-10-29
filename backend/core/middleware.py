from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from .models import AuditEvent

class AuditContextMiddleware(MiddlewareMixin):
    """Record request metadata for authenticated write operations, never request bodies."""
    SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
    def process_request(self, request):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        # Only trust proxy headers when deployment explicitly enables it and the app is behind a trusted proxy.
        request.audit_ip = (forwarded.split(',')[0].strip() if settings.AUDIT_TRUST_X_FORWARDED_FOR and forwarded else request.META.get('REMOTE_ADDR'))
        request.audit_browser = request.META.get('HTTP_USER_AGENT', '')[:255]
        # Coarse network location only (country/region headers). Precise GPS is not collected.
        location = ''
        for header in (
            'HTTP_CF_IPCOUNTRY', 'HTTP_X_GEO_COUNTRY', 'HTTP_X_APPENGINE_COUNTRY',
            'HTTP_CLOUDFRONT_VIEWER_COUNTRY', 'HTTP_X_COUNTRY_CODE',
        ):
            value = (request.META.get(header) or '').strip().upper()
            if value and value not in ('XX', 'T1', 'ZZ'):
                location = value[:120]
                break
        request.audit_location = location
        return None
    def process_response(self, request, response):
        if request.method not in self.SAFE_METHODS and getattr(request, 'user', None) is not None and request.user.is_authenticated:
            try:
                AuditEvent.objects.create(
                    actor=request.user,
                    action=f'HTTP_{request.method}',
                    category='SYSTEM',
                    object_type='HTTP_REQUEST',
                    path=request.path[:500],
                    method=request.method,
                    ip_address=request.audit_ip,
                    browser=request.audit_browser,
                    location_hint=getattr(request, 'audit_location', '') or '',
                    department=(getattr(request.user, 'department', '') or '')[:120],
                    detail={'status_code': response.status_code},
                )
            except Exception:
                # Audit persistence failures should be visible through application monitoring;
                # don't leak request data or break the response path here.
                pass
        return response


class MustChangePasswordMiddleware:
    """Block the app until a flagged account sets a new password."""
    ALLOWED = (
        '/login/', '/logout/', '/account/password/', '/admin/login/', '/admin/logout/',
        '/api/auth/login/', '/api/auth/password/change/', '/api/auth/me/',
        '/api/auth/token/refresh/', '/healthz', '/static/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and getattr(user, 'must_change_password', False):
            if not any(request.path.startswith(prefix) for prefix in self.ALLOWED):
                if request.path.startswith('/api/'):
                    return JsonResponse({'detail': 'Password change required.', 'code': 'password_change_required'}, status=403)
                return redirect('password-change')
        return self.get_response(request)
