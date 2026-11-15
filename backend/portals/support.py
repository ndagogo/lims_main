"""Patient support desk: open a request, route it, and keep the timeline."""
from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from core.models import SequenceCounter, UserCentreAccess
from portals.models import PortalRequest, SupportEvent, SupportRequestType

User = get_user_model()
OPEN = PortalRequest.OPEN_STATUSES


def user_can_handle_support(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if user.has_perm('portals.patient_support'):
        return True
    return user.groups.filter(name__in=('Centre Administrator', 'Platform Administrator')).exists()


def user_can_manage_support_types(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if user.has_perm('portals.manage_support_types'):
        return True
    return user.groups.filter(name__in=('Centre Administrator', 'Platform Administrator')).exists()


def centre_ids_for(user):
    if user.is_superuser:
        return None
    return list(
        UserCentreAccess.objects.filter(
            user=user, centre__is_active=True, can_view=True,
        ).values_list('centre_id', flat=True)
    )


def sees_centre(user, centre_id):
    if centre_id is None or user.is_superuser:
        return True
    allowed = centre_ids_for(user)
    return allowed is None or centre_id in allowed


def support_staff(centre_id=None):
    """Active users who hold the patient_support privilege."""
    people = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True)
        | Q(user_permissions__codename='patient_support', user_permissions__content_type__app_label='portals')
        | Q(groups__permissions__codename='patient_support', groups__permissions__content_type__app_label='portals')
    ).distinct().order_by('first_name', 'last_name', 'username')
    if centre_id is None:
        return people
    return people.filter(
        Q(is_superuser=True) | Q(centre_access__centre_id=centre_id, centre_access__can_view=True)
    ).distinct()


def scoped_requests(user):
    qs = PortalRequest.objects.select_related(
        'patient', 'visit', 'centre', 'request_type', 'assignee',
    )
    centre_ids = centre_ids_for(user)
    if centre_ids is None:
        return qs
    return qs.filter(Q(centre_id__in=centre_ids) | Q(centre__isnull=True))


def types_for_patient(patient):
    from patients.models import Visit
    centre_ids = Visit.objects.filter(patient=patient).values_list('centre_id', flat=True)
    return SupportRequestType.objects.filter(is_active=True).filter(
        Q(centre__isnull=True) | Q(centre_id__in=centre_ids)
    ).select_related('centre')


def _next_reference():
    with transaction.atomic():
        counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key='support-request')
        counter.value += 1
        counter.save(update_fields=['value'])
        return f'SUP-{counter.value:06d}'


def _event(request_row, *, kind, actor=None, note='', from_status='', to_status='', visible=False):
    return SupportEvent.objects.create(
        request=request_row, actor=actor, kind=kind, note=(note or '')[:4000],
        from_status=from_status or '', to_status=to_status or '', visible_to_patient=visible,
    )


def open_patient_request(patient, request_type, message, visit=None):
    centre = visit.centre if visit is not None else None
    if centre is None:
        latest = patient.visits.select_related('centre').order_by('-created_at').first()
        centre = latest.centre if latest is not None else None
    hours = request_type.response_hours or 24
    now = timezone.now()
    assignee = None
    status = 'OPEN'
    owner = request_type.default_assignee
    if owner and owner.is_active and sees_centre(owner, getattr(centre, 'pk', None)) and (
        owner.is_superuser or owner.has_perm('portals.patient_support')
    ):
        assignee = owner
        status = 'ASSIGNED'
    row = PortalRequest.objects.create(
        reference=_next_reference(),
        patient=patient,
        visit=visit,
        centre=centre,
        request_type=request_type,
        kind=(request_type.code or '')[:40],
        message=message[:4000],
        status=status,
        assignee=assignee,
        due_at=now + timedelta(hours=hours),
    )
    _event(
        row, kind='OPENED', note=message, to_status='OPEN', visible=True,
    )
    if assignee is not None:
        _event(
            row, kind='ASSIGNED', actor=assignee,
            note=f'Routed to {assignee.get_full_name() or assignee.username}.',
            from_status='OPEN', to_status='ASSIGNED', visible=True,
        )
    from communications.inbox import announce, record_support_opened
    announce(lambda: record_support_opened(row))
    return row


def add_patient_reply(row, message):
    text = (message or '').strip()
    if not text:
        from django.core.exceptions import ValidationError
        raise ValidationError('Write a reply before sending.')
    if not row.is_open:
        from django.core.exceptions import ValidationError
        raise ValidationError('This request is already closed.')
    previous = row.status
    if row.status == 'WAITING':
        row.status = 'IN_PROGRESS' if row.assignee_id else 'OPEN'
        row.save(update_fields=['status', 'updated_at'])
    _event(
        row, kind='PATIENT_REPLY', note=text, from_status=previous, to_status=row.status, visible=True,
    )
    from communications.inbox import announce, record_support_patient_reply
    announce(lambda: record_support_patient_reply(row))
    return row


def assign_request(row, assignee, actor, note=''):
    if assignee is None:
        from django.core.exceptions import ValidationError
        raise ValidationError('Choose a support staff member.')
    if not (assignee.is_superuser or assignee.has_perm('portals.patient_support')):
        from django.core.exceptions import ValidationError
        raise ValidationError('That person does not have the patient support privilege.')
    if not sees_centre(assignee, row.centre_id):
        from django.core.exceptions import ValidationError
        raise ValidationError('That person cannot see this centre.')
    previous = row.status
    row.assignee = assignee
    if row.status == 'OPEN':
        row.status = 'ASSIGNED'
    row.save(update_fields=['assignee', 'status', 'updated_at'])
    label = assignee.get_full_name() or assignee.username
    _event(
        row, kind='ASSIGNED', actor=actor,
        note=(note or f'Assigned to {label}.').strip(),
        from_status=previous, to_status=row.status, visible=True,
    )
    if actor is None or actor.pk != assignee.pk:
        from communications.inbox import announce, record_support_assigned
        announce(lambda: record_support_assigned(row, assignee))
    return row


def change_status(row, status, actor, note='', *, visible=True):
    allowed = dict(PortalRequest.STATUSES)
    if status not in allowed:
        from django.core.exceptions import ValidationError
        raise ValidationError('Choose a valid status.')
    text = (note or '').strip()
    if status in ('RESOLVED', 'CLOSED', 'WAITING') and not text:
        from django.core.exceptions import ValidationError
        raise ValidationError('Add a note so the timeline shows why the status changed.')
    previous = row.status
    now = timezone.now()
    row.status = status
    fields = ['status', 'updated_at']
    if status == 'RESOLVED' and row.resolved_at is None:
        row.resolved_at = now
        fields.append('resolved_at')
    if status == 'CLOSED':
        row.closed_at = now
        fields.append('closed_at')
        if row.resolved_at is None:
            row.resolved_at = now
            fields.append('resolved_at')
    if status in OPEN and row.closed_at:
        row.closed_at = None
        fields.append('closed_at')
    row.save(update_fields=fields)
    kind = 'RESOLVED' if status == 'RESOLVED' else 'CLOSED' if status == 'CLOSED' else 'STATUS'
    show = visible or status in ('RESOLVED', 'CLOSED', 'WAITING')
    _event(
        row, kind=kind, actor=actor, note=text or f'Status set to {allowed[status]}.',
        from_status=previous, to_status=status, visible=show,
    )
    if show:
        from communications.inbox import announce, record_support_for_patient
        label = {'RESOLVED': 'Request resolved', 'CLOSED': 'Request closed', 'WAITING': 'A reply is needed'}.get(status, 'Request updated')
        announce(lambda: record_support_for_patient(row, title=label, body=text or f'{row.reference} was updated.'))
    return row


def add_staff_note(row, actor, note, *, visible=False):
    text = (note or '').strip()
    if not text:
        from django.core.exceptions import ValidationError
        raise ValidationError('Write a note before saving.')
    now = timezone.now()
    if visible and row.first_response_at is None:
        row.first_response_at = now
        if row.status in ('OPEN', 'ASSIGNED'):
            row.status = 'IN_PROGRESS'
        row.save(update_fields=['first_response_at', 'status', 'updated_at'])
    _event(
        row, kind='REPLY' if visible else 'NOTE', actor=actor, note=text,
        to_status=row.status, visible=visible,
    )
    if visible:
        from communications.inbox import announce, record_support_for_patient
        announce(lambda: record_support_for_patient(row, title='Support reply', body=text))
    return row


def type_code(name, *, exclude_pk=None):
    base = re.sub(r'[^A-Z0-9]+', '_', (name or '').upper()).strip('_')[:40] or 'REQUEST'
    code = base
    n = 2
    while SupportRequestType.objects.filter(code=code).exclude(pk=exclude_pk).exists():
        suffix = f'_{n}'
        code = f'{base[:40 - len(suffix)]}{suffix}'
        n += 1
    return code


def filtered_requests(user, request):
    from django.utils.dateparse import parse_date
    qs = scoped_requests(user)
    q = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').upper()
    type_id = (request.GET.get('type') or '').strip()
    assignee = (request.GET.get('assignee') or '').strip()
    centre = (request.GET.get('centre') or '').strip()
    overdue = request.GET.get('overdue') == '1'
    start = parse_date(request.GET.get('from') or '')
    end = parse_date(request.GET.get('to') or '')
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(message__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__uhid__icontains=q)
            | Q(visit__visit_id__icontains=q)
        )
    if status in dict(PortalRequest.STATUSES):
        qs = qs.filter(status=status)
    if type_id:
        qs = qs.filter(request_type_id=type_id)
    if assignee == 'me':
        qs = qs.filter(assignee=user)
    elif assignee == 'none':
        qs = qs.filter(assignee__isnull=True)
    elif assignee:
        qs = qs.filter(assignee_id=assignee)
    if centre:
        qs = qs.filter(centre_id=centre)
    if overdue:
        qs = qs.filter(due_at__lt=timezone.now(), status__in=OPEN)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    return qs.order_by('-created_at'), {
        'q': q, 'status': status if status in dict(PortalRequest.STATUSES) else '',
        'type': type_id, 'assignee': assignee, 'centre': centre,
        'overdue': overdue, 'from': start.isoformat() if start else '', 'to': end.isoformat() if end else '',
    }


def support_statistics(user):
    base = scoped_requests(user)
    now = timezone.now()
    today = timezone.localdate()
    open_rows = base.filter(status__in=OPEN)
    responded = base.exclude(first_response_at=None).annotate(
        delta=ExpressionWrapper(F('first_response_at') - F('created_at'), output_field=DurationField()),
    ).aggregate(avg=Avg('delta'))
    average = responded['avg']
    hours = None
    if average is not None:
        hours = round(average.total_seconds() / 3600, 1)
    by_type = list(
        base.values('request_type__name').annotate(total=Count('id')).order_by('-total')[:8]
    )
    by_owner = list(
        open_rows.values('assignee__first_name', 'assignee__last_name', 'assignee__username').annotate(
            total=Count('id'),
        ).order_by('-total')[:8]
    )
    return {
        'open': open_rows.count(),
        'mine': open_rows.filter(assignee=user).count(),
        'unassigned': base.filter(assignee__isnull=True, status='OPEN').count(),
        'overdue': open_rows.filter(due_at__lt=now).count(),
        'resolved_today': base.filter(resolved_at__date=today).count(),
        'average_response_hours': hours,
        'by_type': by_type,
        'by_owner': by_owner,
    }


def export_rows(user, request):
    qs, filters = filtered_requests(user, request)
    rows = []
    for row in qs[:5000]:
        patient = f'{row.patient.first_name} {row.patient.last_name}'.strip()
        owner = ''
        if row.assignee_id:
            owner = row.assignee.get_full_name() or row.assignee.username
        rows.append([
            row.reference or '',
            timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
            row.get_status_display(),
            row.request_type.name if row.request_type_id else row.kind,
            row.get_priority_display(),
            patient,
            row.patient.uhid,
            row.visit.visit_id if row.visit_id else '',
            row.centre.code if row.centre_id else '',
            owner,
            timezone.localtime(row.due_at).strftime('%Y-%m-%d %H:%M') if row.due_at else '',
            timezone.localtime(row.first_response_at).strftime('%Y-%m-%d %H:%M') if row.first_response_at else '',
            timezone.localtime(row.resolved_at).strftime('%Y-%m-%d %H:%M') if row.resolved_at else '',
        ])
    return rows, filters
