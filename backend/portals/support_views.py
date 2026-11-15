"""Staff support desk for requests raised in the patient portal."""
from urllib.parse import urlencode

from django.contrib import messages
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import Centre
from core.paging import paginate
from portals.models import PortalRequest, SupportRequestType
from portals.support import (
    add_staff_note,
    assign_request,
    change_status,
    export_rows,
    filtered_requests,
    scoped_requests,
    support_staff,
    support_statistics,
    type_code,
    user_can_handle_support,
    user_can_manage_support_types,
)


def _guard(user, check):
    if not check(user):
        raise PermissionDenied('You do not have access to patient support.')


def _query(filters):
    payload = {}
    for key, value in filters.items():
        if key == 'overdue' and value:
            payload['overdue'] = '1'
        elif value:
            payload[key] = value
    return urlencode(payload)


@login_required
def support_desk(request):
    _guard(request.user, user_can_handle_support)
    rows, filters = filtered_requests(request.user, request)
    stats = support_statistics(request.user)
    centres = Centre.objects.filter(is_active=True).order_by('name')
    if not request.user.is_superuser:
        from portals.support import centre_ids_for
        allowed = centre_ids_for(request.user)
        if allowed is not None:
            centres = centres.filter(pk__in=allowed)
    return render(request, 'support/desk.html', {
        'queue': paginate(request, rows, 'page'),
        'filters': filters,
        'filter_query': _query(filters),
        'stats': stats,
        'statuses': PortalRequest.STATUSES,
        'types': SupportRequestType.objects.filter(is_active=True).order_by('sort_order', 'name'),
        'staff': support_staff(),
        'centres': centres,
        'can_manage_types': user_can_manage_support_types(request.user),
        'open_count': stats['open'],
        'now': timezone.now(),
    })


@login_required
def support_export(request):
    _guard(request.user, user_can_handle_support)
    from reporting.exports import tables_export
    rows, filters = export_rows(request.user, request)
    meta = [f'{key}: {value}' for key, value in filters.items() if value]
    fmt = (request.GET.get('fmt') or 'csv').lower()
    if fmt == 'excel':
        fmt = 'xlsx'
    return tables_export(
        'Patient support',
        [{
            'title': 'Patient support',
            'headers': [
                'Reference', 'Opened', 'Status', 'Type', 'Priority', 'Patient', 'UHID',
                'Visit', 'Centre', 'Assignee', 'Due', 'First response', 'Resolved',
            ],
            'rows': rows,
        }],
        fmt=fmt,
        basename='patient-support',
        meta_lines=meta,
    )


@login_required
def support_case(request, pk):
    _guard(request.user, user_can_handle_support)
    row = get_object_or_404(scoped_requests(request.user).prefetch_related('events__actor'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action') or ''
        try:
            if action == 'assign':
                person = support_staff(row.centre_id).filter(pk=request.POST.get('assignee') or None).first()
                assign_request(row, person, request.user, request.POST.get('note') or '')
                messages.success(request, f'{row.reference} assigned.')
            elif action == 'status':
                change_status(row, (request.POST.get('status') or '').upper(), request.user, request.POST.get('note') or '')
                messages.success(request, f'{row.reference} updated.')
            elif action == 'reply':
                add_staff_note(row, request.user, request.POST.get('note') or '', visible=True)
                messages.success(request, 'Reply sent to the patient portal.')
            elif action == 'note':
                add_staff_note(row, request.user, request.POST.get('note') or '', visible=False)
                messages.success(request, 'Internal note added to the timeline.')
            else:
                messages.error(request, 'Choose an action.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        return redirect('support-case', pk=row.pk)
    return render(request, 'support/case.html', {
        'case': row,
        'events': row.events.all(),
        'statuses': PortalRequest.STATUSES,
        'staff': support_staff(row.centre_id),
        'can_manage_types': user_can_manage_support_types(request.user),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def support_types(request):
    _guard(request.user, user_can_manage_support_types)
    centres = Centre.objects.filter(is_active=True).order_by('name')
    if request.method == 'POST':
        action = request.POST.get('action') or 'create'
        try:
            if action == 'deactivate':
                row = get_object_or_404(SupportRequestType, pk=request.POST.get('type'))
                row.is_active = False
                row.save(update_fields=['is_active', 'updated_at'])
                messages.success(request, f'{row.name} is no longer offered to patients.')
            else:
                _save_type(request)
                messages.success(request, 'Request type saved.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        return redirect('support-types')
    return render(request, 'support/types.html', {
        'types': SupportRequestType.objects.select_related('centre', 'default_assignee').order_by('sort_order', 'name'),
        'centres': centres,
        'staff': support_staff(),
    })


def _save_type(request):
    name = ' '.join((request.POST.get('name') or '').split())
    if not name:
        raise ValidationError('Give the request type a name.')
    try:
        hours = int(request.POST.get('response_hours') or 24)
    except (TypeError, ValueError):
        hours = 24
    hours = min(max(hours, 1), 24 * 30)
    centre_id = (request.POST.get('centre') or '').strip()
    centre = Centre.objects.filter(pk=centre_id, is_active=True).first() if centre_id else None
    assignee_id = (request.POST.get('default_assignee') or '').strip()
    assignee = support_staff().filter(pk=assignee_id).first() if assignee_id else None
    if assignee_id and assignee is None:
        raise ValidationError('The default owner needs the patient support privilege.')
    existing = None
    type_id = (request.POST.get('type') or '').strip()
    if type_id:
        existing = get_object_or_404(SupportRequestType, pk=type_id)
    code = type_code(name, exclude_pk=existing.pk if existing else None)
    if existing and existing.code in ('SUPPORT', 'CORRECTION') and code != existing.code:
        code = existing.code
    payload = {
        'name': name[:80],
        'description': (request.POST.get('description') or '').strip()[:240],
        'centre': centre,
        'default_assignee': assignee,
        'response_hours': hours,
        'is_active': request.POST.get('is_active') == '1',
        'code': existing.code if existing and existing.code in ('SUPPORT', 'CORRECTION') else code,
    }
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        existing.save()
        return existing
    payload['created_by'] = request.user
    try:
        payload['sort_order'] = int(request.POST.get('sort_order') or 0)
    except (TypeError, ValueError):
        payload['sort_order'] = 0
    return SupportRequestType.objects.create(**payload)
