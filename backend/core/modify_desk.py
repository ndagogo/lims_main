"""Modified approvals desk: visit lookup context, history, and exports."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import ResultLedger, UserCentreAccess
from core.paging import PAGE_SIZES, paginate
from core.result_control import (
    REASON_LABEL,
    user_can_modify_laboratory,
    user_can_modify_radiology,
    visit_results,
)


def _centre_ids(user):
    if user.is_superuser:
        return None
    return list(
        UserCentreAccess.objects.filter(
            user=user, centre__is_active=True, can_view=True,
        ).values_list('centre_id', flat=True)
    )


def _patient_label(patient):
    parts = [patient.title, patient.first_name, patient.last_name]
    return ' '.join(part for part in parts if part).strip() or patient.uhid


def _history_filters(request):
    history_q = (request.GET.get('history_q') or '').strip()
    history_service = (request.GET.get('history_service') or '').upper()
    if history_service not in ('RADIOLOGY', 'LABORATORY'):
        history_service = ''
    history_from = parse_date(request.GET.get('history_from') or '')
    history_to = parse_date(request.GET.get('history_to') or '')
    return history_q, history_service, history_from, history_to


def _history_queryset(user, request):
    centre_ids = _centre_ids(user)
    history_q, history_service, history_from, history_to = _history_filters(request)
    rows = ResultLedger.objects.select_related(
        'actor', 'visit', 'visit__patient', 'visit__centre',
    ).filter(kind='MODIFY').order_by('-created_at')
    if centre_ids is not None:
        rows = rows.filter(visit__centre_id__in=centre_ids)
    if history_q:
        rows = rows.filter(
            Q(visit__visit_id__icontains=history_q)
            | Q(summary__icontains=history_q)
            | Q(reason__icontains=history_q)
            | Q(actor__username__icontains=history_q)
            | Q(actor__first_name__icontains=history_q)
            | Q(actor__last_name__icontains=history_q)
            | Q(visit__patient__first_name__icontains=history_q)
            | Q(visit__patient__last_name__icontains=history_q)
        )
    if history_service:
        rows = rows.filter(service=history_service)
    if history_from:
        rows = rows.filter(created_at__date__gte=history_from)
    if history_to:
        rows = rows.filter(created_at__date__lte=history_to)
    return rows, history_q, history_service, history_from, history_to


def build_modify_context(request, *, visit_id='', visit=None, reports=None, results=None, missing=False):
    user = request.user
    centre_ids = _centre_ids(user)
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())

    base = ResultLedger.objects.filter(kind='MODIFY')
    if centre_ids is not None:
        base = base.filter(visit__centre_id__in=centre_ids)

    kpis = {
        'today': base.filter(created_at__date=today).count(),
        'week': base.filter(created_at__date__gte=week_start).count(),
        'radiology': base.filter(service='RADIOLOGY').count(),
        'laboratory': base.filter(service='LABORATORY').count(),
    }

    history_qs, history_q, history_service, history_from, history_to = _history_queryset(user, request)
    history = paginate(request, history_qs, 'hpage')

    if visit is None and visit_id:
        visit, reports, results = visit_results(user, visit_id)
        missing = visit is None
    reports = reports or []
    results = results or []

    history_query = urlencode({
        key: value for key, value in {
            'history_q': history_q,
            'history_service': history_service,
            'history_from': history_from.isoformat() if history_from else '',
            'history_to': history_to.isoformat() if history_to else '',
            'visit_id': visit_id,
            'per': request.GET.get('per') or '',
        }.items() if value
    })

    return {
        'visit_id': visit_id,
        'visit': visit,
        'reports': reports,
        'results': results,
        'missing': missing,
        'reason_label': REASON_LABEL,
        'can_modify_radiology': user_can_modify_radiology(user),
        'can_modify_laboratory': user_can_modify_laboratory(user),
        'kpis': kpis,
        'history': history,
        'history_q': history_q,
        'history_service': history_service,
        'history_from': history_from.isoformat() if history_from else '',
        'history_to': history_to.isoformat() if history_to else '',
        'history_query': history_query,
        'history_count': history.paginator.count,
        'page_sizes': PAGE_SIZES,
    }


def modify_history_export_rows(request):
    history_qs, history_q, history_service, history_from, history_to = _history_queryset(request.user, request)
    rows = []
    for row in history_qs[:5000]:
        actor = row.actor.get_full_name() if row.actor_id else ''
        if not actor and row.actor_id:
            actor = row.actor.username
        patient = ''
        if row.visit_id and row.visit.patient_id:
            patient = _patient_label(row.visit.patient)
        rows.append([
            timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
            actor,
            row.visit.visit_id if row.visit_id else '',
            patient,
            row.get_service_display(),
            row.summary or '',
            row.reason or '',
        ])
    return {
        'rows': rows,
        'filters': {
            'q': history_q,
            'service': history_service,
            'from': history_from.isoformat() if history_from else '',
            'to': history_to.isoformat() if history_to else '',
        },
        'count': len(rows),
    }
