"""Radiology desk rules: who sees a study, exam duration, and accept-window expiry."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from radiology.models import RadiologistAcceptRecord, RadiologyDeskSetting, RadiologyExam

SUPPLY_PRESETS = ('Syringe', 'Film', 'CD')
EXAM_IN_PROCESS = (
    'Sorry you currently have an exam in process. Check your list and complete the exam. Thank You'
)


def accept_window():
    row, _created = RadiologyDeskSetting.objects.get_or_create(pk=1, defaults={'accept_minutes': 30})
    return row


def radiographer_active_exam(user):
    """The in-progress exam this radiographer started, if they already have one."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return (
        RadiologyExam.objects.filter(status='IN_PROGRESS')
        .filter(Q(started_by=user) | Q(assigned_radiographer=user))
        .order_by('-started_at')
        .first()
    )


def is_radiographer(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return user.groups.filter(name='Radiographer').exists()


def is_reporting_radiologist(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=('Radiologist', 'Platform Administrator')).exists()


def exam_elapsed_seconds(exam, now=None):
    if not exam.started_at:
        return None
    end = exam.ended_at or now or timezone.now()
    return max(0, int((end - exam.started_at).total_seconds()))


def exam_minutes(exam, now=None):
    seconds = exam_elapsed_seconds(exam, now)
    if seconds is None:
        return None
    return seconds // 60


def exam_duration_label(exam, now=None):
    seconds = exam_elapsed_seconds(exam, now)
    if seconds is None:
        return ''
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f'{minutes} min {secs} s'
    if minutes:
        return f'{minutes} min'
    return f'{secs} s'


def can_reassign_radiologist(exam, now=None):
    """The accept window has elapsed and the study has not been accepted."""
    if exam.accepted_at:
        return False
    if not exam.assigned_radiologist_id:
        return True
    if not exam.radiologist_assigned_at:
        return False
    now = now or timezone.now()
    window = accept_window()
    elapsed = int((now - exam.radiologist_assigned_at).total_seconds() // 60)
    return elapsed >= window.accept_minutes


def performing_radiographer_id(exam):
    """The radiographer who carried out the exam."""
    return exam.started_by_id or exam.assigned_radiographer_id or exam.ended_by_id


def user_may_reassign(user, exam):
    """Only the radiographer who performed the exam may send it to another radiologist."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    performer = performing_radiographer_id(exam)
    return bool(performer and user.pk == performer and is_radiographer(user))


def record_missed_acceptance(exam):
    """Keep one missed-accept row for this assignment so performance is not double-counted."""
    if exam.accepted_at or not exam.assigned_radiologist_id or not exam.radiologist_assigned_at:
        return None
    if RadiologistAcceptRecord.objects.filter(
        exam=exam, radiologist_id=exam.assigned_radiologist_id,
        assigned_at=exam.radiologist_assigned_at, outcome='MISSED',
    ).exists():
        return None
    window = accept_window()
    name = exam.assigned_radiologist.get_username()
    return RadiologistAcceptRecord.objects.create(
        exam=exam,
        radiologist=exam.assigned_radiologist,
        assigned_at=exam.radiologist_assigned_at,
        closed_at=timezone.now(),
        window_minutes=window.accept_minutes,
        outcome='MISSED',
        note=f'{name} did not accept within {window.accept_minutes} minutes.',
    )


def record_accepted_study(exam, user):
    radiologist = exam.assigned_radiologist or user
    assigned_at = exam.radiologist_assigned_at or exam.accepted_at or timezone.now()
    if RadiologistAcceptRecord.objects.filter(
        exam=exam, radiologist=radiologist, assigned_at=assigned_at, outcome='ACCEPTED',
    ).exists():
        return None
    window = accept_window()
    return RadiologistAcceptRecord.objects.create(
        exam=exam,
        radiologist=radiologist,
        assigned_at=assigned_at,
        closed_at=exam.accepted_at or timezone.now(),
        window_minutes=window.accept_minutes,
        outcome='ACCEPTED',
        note=f'{radiologist.get_username()} accepted the study.',
    )


def release_expired_assignments():
    """Unaccepted studies past the platform window return to the radiographer who performed them."""
    window = accept_window()
    cutoff = timezone.now() - timedelta(minutes=window.accept_minutes)
    expired = RadiologyExam.objects.filter(
        assigned_radiologist__isnull=False,
        accepted_at__isnull=True,
        radiologist_assigned_at__isnull=False,
        radiologist_assigned_at__lt=cutoff,
        pacs_uploaded_at__isnull=False,
    ).select_related('assigned_radiologist')
    released = 0
    for exam in expired:
        name = exam.assigned_radiologist.get_username() if exam.assigned_radiologist_id else ''
        record_missed_acceptance(exam)
        exam.assigned_radiologist = None
        exam.radiologist_assigned_at = None
        exam.returned_at = timezone.now()
        exam.save(update_fields=['assigned_radiologist', 'radiologist_assigned_at', 'returned_at', 'updated_at'])
        from radiology.activity import record_exam_activity
        record_exam_activity(
            exam, action='RETURNED_FOR_REASSIGNMENT', user=None,
            from_status=exam.status, to_status=exam.status,
            note=f'Accept window of {window.accept_minutes} min elapsed for {name}. Returned to the performing radiographer.',
        )
        released += 1
    return released


def _performance_filters(params):
    from django.utils.dateparse import parse_date

    query = (params.get('q') or '').strip()
    outcome = (params.get('outcome') or '').strip().upper()
    if outcome not in ('ACCEPTED', 'MISSED'):
        outcome = ''
    radiologist_id = (params.get('radiologist') or '').strip()
    date_from = parse_date((params.get('date_from') or '').strip() or '')
    date_to = parse_date((params.get('date_to') or '').strip() or '')
    return {
        'q': query,
        'outcome': outcome,
        'radiologist': radiologist_id,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        '_date_from': date_from,
        '_date_to': date_to,
    }


def filtered_accept_records(params):
    """Accept/miss records with search and filter applied."""
    filters = _performance_filters(params)
    rows = RadiologistAcceptRecord.objects.select_related(
        'radiologist', 'exam__order__visit', 'exam__order__procedure', 'exam__order__modality',
    )
    if filters['outcome']:
        rows = rows.filter(outcome=filters['outcome'])
    if filters['radiologist']:
        rows = rows.filter(radiologist_id=filters['radiologist'])
    if filters['_date_from']:
        rows = rows.filter(closed_at__date__gte=filters['_date_from'])
    if filters['_date_to']:
        rows = rows.filter(closed_at__date__lte=filters['_date_to'])
    query = filters['q']
    if query:
        rows = rows.filter(
            Q(exam__order__visit__visit_id__icontains=query)
            | Q(radiologist__username__icontains=query)
            | Q(radiologist__first_name__icontains=query)
            | Q(radiologist__last_name__icontains=query)
            | Q(exam__order__procedure__name__icontains=query)
            | Q(exam__order__modality__name__icontains=query)
            | Q(exam__order__modality__modality_type__icontains=query)
        )
    return rows.order_by('-closed_at'), filters


def radiologist_performance(params=None):
    """Accepted and missed counts, plus filtered activity for the performance page."""
    params = params or {}
    activity, filters = filtered_accept_records(params)
    summary = list(
        activity.values(
            'radiologist_id', 'radiologist__first_name', 'radiologist__last_name', 'radiologist__username',
        ).annotate(
            accepted=Count('id', filter=Q(outcome='ACCEPTED')),
            missed=Count('id', filter=Q(outcome='MISSED')),
        ).order_by('-missed', 'radiologist__username')
    )
    for row in summary:
        row['total'] = row['accepted'] + row['missed']
        name = f"{row['radiologist__first_name']} {row['radiologist__last_name']}".strip()
        row['display_name'] = name or row['radiologist__username'] or 'Unknown'
    # Build a unique radiologist list without DISTINCT ON (SQLite-safe).
    seen = set()
    radiologist_choices = []
    for row in RadiologistAcceptRecord.objects.select_related('radiologist').order_by('radiologist__username'):
        if not row.radiologist_id or row.radiologist_id in seen:
            continue
        seen.add(row.radiologist_id)
        person = row.radiologist
        label = person.get_full_name() or person.username
        radiologist_choices.append({'id': str(person.pk), 'label': label})
    return {
        'summary': summary,
        'activity': activity,
        'filters': filters,
        'radiologist_choices': radiologist_choices,
        'totals': {
            'accepted': activity.filter(outcome='ACCEPTED').count(),
            'missed': activity.filter(outcome='MISSED').count(),
            'total': activity.count(),
        },
    }


def performance_export_rows(activity):
    from django.utils import timezone

    rows = []
    for row in activity[:5000]:
        person = row.radiologist
        name = (person.get_full_name() if person else '') or (person.username if person else 'Unknown')
        study = ''
        visit_id = ''
        try:
            order = row.exam.order
            visit_id = order.visit.visit_id
            study = order.procedure.name if order.procedure_id else order.modality.name
        except Exception:
            pass
        rows.append([
            timezone.localtime(row.closed_at).strftime('%Y-%m-%d %H:%M') if row.closed_at else '',
            name,
            visit_id,
            study,
            'Accepted' if row.outcome == 'ACCEPTED' else 'Not accepted in time',
            row.window_minutes,
        ])
    return rows


def parse_supplies(data):
    """Accept JSON [{name, quantity}] or parallel item_name / quantity lists."""
    raw = data.get('supplies') if hasattr(data, 'get') else None
    rows = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or item.get('item_name') or '').strip()
            qty = item.get('quantity') or 0
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                qty = 0
            if name and qty > 0:
                rows.append((name[:80], qty))
        return rows
    names = data.getlist('item_name') if hasattr(data, 'getlist') else []
    qtys = data.getlist('quantity') if hasattr(data, 'getlist') else []
    for name, qty in zip(names, qtys):
        name = (name or '').strip()
        try:
            qty = int(qty or 0)
        except (TypeError, ValueError):
            qty = 0
        if name and qty > 0:
            rows.append((name[:80], qty))
    return rows
