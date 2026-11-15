from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .inbox import has_audience, notices_for, portal_token, safe_path
from .models import AppNotification


def notification_inbox(request):
    audience = has_audience(request)
    qs = notices_for(request) if audience else AppNotification.objects.none()
    unread_only = request.GET.get('unread') == '1'
    rows = qs.filter(read_at__isnull=True) if unread_only else qs
    token = None if getattr(request.user, 'is_authenticated', False) else portal_token(request)
    return render(request, 'notifications/inbox.html', {
        'audience': audience,
        'rows': list(rows[:40]),
        'unread_only': unread_only,
        'unread_total': qs.filter(read_at__isnull=True).count() if audience else 0,
        'portal_chrome': token.portal.lower() if token is not None else '',
    })


def notification_feed(request):
    if not has_audience(request):
        return JsonResponse({'unread': 0})
    unread = notices_for(request).filter(read_at__isnull=True).count()
    return JsonResponse({'unread': unread})


@require_POST
def notification_open(request, pk):
    note = notices_for(request).filter(pk=pk).first()
    if note is None:
        raise Http404
    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=['read_at', 'updated_at'])
    return redirect(safe_path(note.url) or 'notification-inbox')


@require_POST
def notification_read_all(request):
    if has_audience(request):
        notices_for(request).filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect(safe_path(request.POST.get('next')) or 'notification-inbox')
