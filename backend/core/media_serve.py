"""Serve uploaded images in production.

Django's debug static() helper adds no routes when DEBUG is False, so logos
and signatures saved on Railway never reach the browser. Patient documents
and other clinical files stay on their own permissioned download views.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

# Login page and letterhead. No sign-in required.
PUBLIC_PREFIXES = ('centre-logos/',)
# Shown on signed-in staff screens via <img>.
STAFF_PREFIXES = ('avatars/', 'signatures/')


@require_GET
def serve_uploaded_media(request, path):
    if not (settings.DEBUG or getattr(settings, 'SERVE_MEDIA', False)):
        raise Http404()
    relative = (path or '').replace('\\', '/').lstrip('/')
    if not relative or '..' in Path(relative).parts:
        raise Http404()
    public = relative.startswith(PUBLIC_PREFIXES)
    staff_only = relative.startswith(STAFF_PREFIXES)
    if not public and not staff_only:
        raise Http404()
    if staff_only and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / relative).resolve()
    if root not in target.parents or not target.is_file():
        raise Http404()
    return FileResponse(target.open('rb'))
