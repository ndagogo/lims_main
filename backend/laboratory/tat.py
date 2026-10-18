"""Turnaround time, unified timelines, pending queues, and escalation alerts."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q
from django.utils import timezone

from laboratory.models import Sample, TatEscalation, TatRule


def _sample_result(sample):
    try:
        return sample.result
    except ObjectDoesNotExist:
        return None

OPEN_STATUSES = [
    'PENDING_COLLECTION', 'COLLECTED', 'BATCHED', 'RECEIVED', 'PROCESSING',
    'RESULT_ENTERED', 'UNDER_REVIEW', 'AUTHENTICATED',
]

# Default responsible role when no workflow step binding is available.
STATUS_ROLE = {
    'PENDING_COLLECTION': 'Phlebotomist',
    'COLLECTED': 'Phlebotomist',
    'BATCHED': 'Laboratory Scientist',
    'RECEIVED': 'Laboratory Scientist',
    'PROCESSING': 'Laboratory Scientist',
    'RESULT_ENTERED': 'Laboratory Scientist',
    'UNDER_REVIEW': 'Laboratory Scientist',
    'AUTHENTICATED': 'Lab Manager',
    'RELEASED': '—',
    'REJECTED': 'Phlebotomist',
    'CANCELLED': '—',
}

STAGE_ORDER = [
    ('registration', 'Registration'),
    ('payment', 'Payment'),
    ('collection', 'Collection'),
    ('batch', 'Batch / dispatch'),
    ('receive', 'Department receive'),
    ('processing', 'Processing'),
    ('result_entry', 'Result entry'),
    ('review', 'First review'),
    ('authenticate', 'Authentication'),
    ('release', 'Controlled release'),
]

DEFAULT_EXPECTED_MINUTES = 24 * 60


def _minutes(delta) -> float:
    if delta is None:
        return 0.0
    return round(delta.total_seconds() / 60.0, 1)


def _hours(minutes: float) -> float:
    return round(minutes / 60.0, 2)


def _actor_name(user) -> str:
    if not user:
        return ''
    return getattr(user, 'get_username', lambda: '')() or str(user)


def _device_label(ip=None, browser='') -> str:
    parts = []
    if ip:
        parts.append(str(ip))
    if browser:
        parts.append((browser or '')[:80])
    return ' · '.join(parts)


def scoped_open_samples(user):
    qs = Sample.objects.filter(status__in=OPEN_STATUSES).select_related(
        'centre', 'order__test', 'order__test__service_group', 'order__visit__patient',
        'collected_by',
    )
    if not user.is_superuser:
        qs = qs.filter(centre__user_access__user=user, centre__user_access__can_view=True).distinct()
    return qs


def resolve_tat_rule(test) -> TatRule | None:
    """Most specific active rule wins: exact test, then service group (+ optional department)."""
    if test is None:
        return None
    exact = TatRule.objects.filter(is_active=True, test=test).order_by('-updated_at').first()
    if exact:
        return exact
    group_id = getattr(test, 'service_group_id', None)
    if not group_id:
        return None
    dept = (getattr(test, 'department', '') or '').strip()
    qs = TatRule.objects.filter(is_active=True, test__isnull=True, service_group_id=group_id)
    if dept:
        matched = qs.filter(department__iexact=dept).order_by('-updated_at').first()
        if matched:
            return matched
    return qs.filter(Q(department='') | Q(department__isnull=True)).order_by('-updated_at').first()


def _payment_at(visit):
    from billing.models import Payment
    pay = (
        Payment.objects.filter(invoice__visit=visit, status='CONFIRMED', invoice__status='PAID')
        .order_by('created_at')
        .select_related('received_by')
        .first()
    )
    return pay


def _activity_first(sample, *actions):
    return sample.activities.filter(action__in=actions).order_by('created_at').first()


def _activity_last(sample, *actions):
    return sample.activities.filter(action__in=actions).order_by('-created_at').first()


def clock_bounds(sample, rule: TatRule | None = None):
    """Return (start, end, start_label, end_label) for TAT calculation."""
    rule = rule or resolve_tat_rule(sample.order.test)
    visit = sample.order.visit
    start_key = rule.clock_start if rule else 'PAYMENT'
    end_key = rule.clock_end if rule else 'RELEASE'

    payment = _payment_at(visit)
    receive = _activity_first(sample, 'DEPARTMENT_RECEIVED')
    result = _sample_result(sample)

    starts = {
        'REGISTRATION': (visit.created_at, visit.registered_by, 'Visit registration'),
        'PAYMENT': (
            payment.created_at if payment else (sample.collected_at or sample.created_at),
            payment.received_by if payment else sample.collected_by,
            'Payment confirmation' if payment else 'Collection (no payment stamp)',
        ),
        'COLLECTION': (sample.collected_at or sample.created_at, sample.collected_by, 'Sample collection'),
        'RECEIVE': (
            receive.created_at if receive else (sample.collected_at or sample.created_at),
            receive.performed_by if receive else sample.collected_by,
            'Department receiving' if receive else 'Collection (not yet received)',
        ),
    }
    start_at, start_actor, start_label = starts.get(start_key, starts['PAYMENT'])

    ends = {
        'RELEASE': (
            getattr(result, 'released_at', None) if result else None,
            getattr(result, 'released_by', None) if result else None,
            'Controlled release',
        ),
        'AUTHENTICATE': (
            getattr(result, 'authenticated_at', None) if result else None,
            getattr(result, 'authenticated_by', None) if result else None,
            'Authentication',
        ),
        'REVIEW': (
            getattr(result, 'reviewed_at', None) if result else None,
            getattr(result, 'reviewed_by', None) if result else None,
            'First review',
        ),
    }
    end_at, end_actor, end_label = ends.get(end_key, ends['RELEASE'])
    if end_at is None and sample.status in ('RELEASED', 'CANCELLED', 'REJECTED'):
        end_at = sample.updated_at
    if end_at is None:
        end_at = timezone.now()
        end_label = f'{end_label} (in progress)'
    return {
        'start_at': start_at,
        'end_at': end_at,
        'start_actor': _actor_name(start_actor),
        'end_actor': _actor_name(end_actor),
        'start_label': start_label,
        'end_label': end_label,
        'completed': sample.status == 'RELEASED' or (result and result.status in ('RELEASED', 'AMENDED')),
    }


def pause_minutes(sample, *, start_at=None, end_at=None, only_clock_pausing=True) -> float:
    """Sum documented pause duration overlapping [start_at, end_at]."""
    start_at = start_at or sample.created_at
    end_at = end_at or timezone.now()
    total = 0.0
    qs = sample.tat_pauses.select_related('reason').all()
    for pause in qs:
        if only_clock_pausing and pause.reason_id and not pause.reason.pauses_clock:
            continue
        p_start = max(pause.started_at, start_at) if pause.started_at else start_at
        p_end = pause.ended_at or timezone.now()
        p_end = min(p_end, end_at)
        if p_end > p_start:
            total += (p_end - p_start).total_seconds() / 60.0
    return round(total, 1)


def compute_tat(sample, rule: TatRule | None = None) -> dict[str, Any]:
    rule = rule or resolve_tat_rule(sample.order.test)
    bounds = clock_bounds(sample, rule)
    start_at, end_at = bounds['start_at'], bounds['end_at']
    raw = _minutes(end_at - start_at) if start_at and end_at else 0.0
    paused = pause_minutes(sample, start_at=start_at, end_at=end_at) if (rule is None or rule.allow_pause) else 0.0
    effective = max(0.0, round(raw - paused, 1))
    expected = rule.expected_minutes if rule else DEFAULT_EXPECTED_MINUTES
    warning = rule.warning_minutes if rule and rule.warning_minutes else int(expected * 0.8)
    escalation = rule.escalation_minutes if rule and rule.escalation_minutes else expected
    open_pause = sample.tat_pauses.filter(ended_at__isnull=True, reason__pauses_clock=True).exists()
    status = 'ON_TRACK'
    if effective >= escalation:
        status = 'ESCALATED'
    elif effective >= expected:
        status = 'OVERDUE'
    elif effective >= warning:
        status = 'WARNING'
    if open_pause:
        status = 'PAUSED'
    return {
        'sample_id': sample.sample_id,
        'rule_id': str(rule.pk) if rule else None,
        'rule_name': rule.name if rule else 'Default 24h',
        'expected_minutes': expected,
        'warning_minutes': warning,
        'escalation_minutes': escalation,
        'raw_minutes': raw,
        'pause_minutes': paused,
        'effective_minutes': effective,
        'effective_hours': _hours(effective),
        'status': status,
        'clock_paused': open_pause,
        'allow_pause': bool(rule.allow_pause) if rule else True,
        'escalation_roles': list(rule.escalation_roles or []) if rule else ['Lab Manager'],
        **bounds,
    }


def build_unified_timeline(sample) -> list[dict[str, Any]]:
    """Registration → release events with actor and device attribution."""
    events: list[dict[str, Any]] = []
    visit = sample.order.visit
    events.append({
        'at': visit.created_at,
        'stage': 'registration',
        'action': 'VISIT_REGISTERED',
        'label': 'Patient / visit registered',
        'actor': _actor_name(visit.registered_by),
        'device': '',
        'note': visit.visit_id,
        'source': 'visit',
    })

    payment = _payment_at(visit)
    if payment:
        events.append({
            'at': payment.created_at,
            'stage': 'payment',
            'action': 'PAYMENT_CONFIRMED',
            'label': f'Payment confirmed ({payment.method})',
            'actor': _actor_name(payment.received_by),
            'device': '',
            'note': payment.receipt_number or '',
            'source': 'billing',
        })

    for row in sample.activities.select_related('performed_by').order_by('created_at'):
        stage = _stage_for_activity(row.action, row.to_status)
        events.append({
            'at': row.created_at,
            'stage': stage,
            'action': row.action,
            'label': row.action.replace('_', ' ').title(),
            'actor': _actor_name(row.performed_by),
            'device': _device_label(row.ip_address, row.browser),
            'note': row.note or f'{row.from_status} → {row.to_status}',
            'source': 'sample_activity',
        })

    for row in sample.barcode_prints.select_related('printed_by').order_by('printed_at'):
        events.append({
            'at': row.printed_at,
            'stage': 'collection',
            'action': 'BARCODE_PRINTED',
            'label': f'Barcode printed ({row.get_source_display()})',
            'actor': _actor_name(row.printed_by),
            'device': _device_label(row.ip_address, row.browser),
            'note': row.barcode_value,
            'source': 'barcode_print',
        })

    for proc in sample.processing_records.select_related('performed_by').order_by('started_at'):
        events.append({
            'at': proc.started_at,
            'stage': 'processing',
            'action': proc.action or 'PROCESSING',
            'label': 'Processing activity started',
            'actor': _actor_name(proc.performed_by),
            'device': '',
            'note': proc.notes or '',
            'source': 'processing',
        })
        if proc.completed_at:
            events.append({
                'at': proc.completed_at,
                'stage': 'processing',
                'action': 'PROCESSING_COMPLETED',
                'label': 'Processing activity completed',
                'actor': _actor_name(proc.performed_by),
                'device': '',
                'note': proc.notes or '',
                'source': 'processing',
            })

    result = _sample_result(sample)
    if result:
        events.append({
            'at': result.entered_at,
            'stage': 'result_entry',
            'action': 'RESULT_ENTERED',
            'label': 'Result entry',
            'actor': _actor_name(result.entered_by),
            'device': '',
            'note': f'status={result.status} v{result.version}',
            'source': 'result',
        })
        if result.reviewed_at:
            events.append({
                'at': result.reviewed_at,
                'stage': 'review',
                'action': 'RESULT_REVIEWED',
                'label': 'First review / validation',
                'actor': _actor_name(result.reviewed_by),
                'device': '',
                'note': '',
                'source': 'result',
            })
        if result.authenticated_at:
            events.append({
                'at': result.authenticated_at,
                'stage': 'authenticate',
                'action': 'RESULT_AUTHENTICATED',
                'label': 'Result authentication',
                'actor': _actor_name(result.authenticated_by),
                'device': '',
                'note': '',
                'source': 'result',
            })
        if result.released_at:
            events.append({
                'at': result.released_at,
                'stage': 'release',
                'action': 'RESULT_RELEASED',
                'label': 'Controlled result release',
                'actor': _actor_name(result.released_by),
                'device': '',
                'note': '',
                'source': 'result',
            })
        for rev in result.reviews.select_related('reviewer').order_by('created_at'):
            events.append({
                'at': rev.created_at,
                'stage': 'review' if rev.action in ('SUBMITTED', 'APPROVED', 'REJECTED') else rev.action.lower(),
                'action': f'REVIEW_{rev.action}',
                'label': f'Result {rev.action.lower()}',
                'actor': _actor_name(rev.reviewer),
                'device': '',
                'note': rev.note or '',
                'source': 'result_review',
            })

    try:
        from workflows.models import OrderWorkflowEvent
        wf_events = OrderWorkflowEvent.objects.filter(
            instance__lab_order=sample.order,
        ).select_related('actor', 'from_step', 'to_step').order_by('created_at')
        for row in wf_events:
            events.append({
                'at': row.created_at,
                'stage': (row.to_step.code or '').lower() if row.to_step_id else 'workflow',
                'action': f'WF_{row.action}',
                'label': f'Workflow: {row.action}',
                'actor': _actor_name(row.actor),
                'device': '',
                'note': f'{getattr(row.from_step, "code", "")} → {getattr(row.to_step, "code", "")}',
                'source': 'workflow',
            })
    except Exception:
        pass

    for pause in sample.tat_pauses.select_related('reason', 'started_by', 'ended_by').order_by('started_at'):
        events.append({
            'at': pause.started_at,
            'stage': 'pause',
            'action': 'TAT_PAUSE_START',
            'label': f'TAT pause started ({pause.reason.name})',
            'actor': _actor_name(pause.started_by),
            'device': _device_label(pause.start_ip, pause.start_browser),
            'note': pause.note or '',
            'source': 'tat_pause',
        })
        if pause.ended_at:
            events.append({
                'at': pause.ended_at,
                'stage': 'pause',
                'action': 'TAT_PAUSE_END',
                'label': f'TAT pause ended ({pause.reason.name})',
                'actor': _actor_name(pause.ended_by),
                'device': _device_label(pause.end_ip, pause.end_browser),
                'note': pause.note or '',
                'source': 'tat_pause',
            })

    events.sort(key=lambda e: (e['at'] or timezone.now(), e['action']))
    return events


def _stage_for_activity(action: str, to_status: str) -> str:
    mapping = {
        'COLLECTED': 'collection',
        'RECOLLECTED': 'collection',
        'BATCHED': 'batch',
        'DEPARTMENT_RECEIVED': 'receive',
        'PROCESSING_STARTED': 'processing',
        'PROCESSING_COMPLETED': 'processing',
        'RESULT_ENTERED': 'result_entry',
        'SUBMITTED_FOR_REVIEW': 'review',
        'RESULT_REVIEWED': 'review',
        'RESULT_REJECTED': 'review',
        'RESULT_AUTHENTICATED': 'authenticate',
        'RESULT_RELEASED': 'release',
        'REJECTED': 'collection',
        'SUPERSEDED': 'collection',
        'DISPATCHED': 'batch',
        'RESULT_AMENDED': 'release',
    }
    if action in mapping:
        return mapping[action]
    status_map = {
        'COLLECTED': 'collection', 'BATCHED': 'batch', 'RECEIVED': 'receive',
        'PROCESSING': 'processing', 'RESULT_ENTERED': 'result_entry',
        'UNDER_REVIEW': 'review', 'AUTHENTICATED': 'authenticate', 'RELEASED': 'release',
        'REJECTED': 'collection', 'SUPERSEDED': 'collection',
    }
    return status_map.get(to_status, 'other')


def stage_durations(sample, timeline: list[dict] | None = None) -> list[dict[str, Any]]:
    """Minutes spent in each major workflow stage (from first event of stage to next)."""
    timeline = timeline or build_unified_timeline(sample)
    # Pick earliest timestamp per stage key that appears in STAGE_ORDER.
    stage_keys = [key for key, _ in STAGE_ORDER]
    first_at: dict[str, Any] = {}
    for event in timeline:
        stage = event.get('stage')
        if stage in stage_keys and stage not in first_at and event.get('at'):
            first_at[stage] = event['at']
    # Ensure current status contributes an anchor.
    if sample.status == 'PROCESSING' and 'processing' not in first_at:
        first_at['processing'] = sample.updated_at
    points = [(key, label, first_at[key]) for key, label in STAGE_ORDER if key in first_at]
    points.sort(key=lambda row: row[2])
    now = timezone.now()
    result = _sample_result(sample)
    end_anchor = result.released_at if result and result.released_at else None
    end_anchor = end_anchor or (sample.updated_at if sample.status in ('RELEASED', 'REJECTED', 'CANCELLED') else now)

    rows = []
    for idx, (key, label, started) in enumerate(points):
        ended = points[idx + 1][2] if idx + 1 < len(points) else end_anchor
        mins = _minutes(ended - started) if ended and started else 0.0
        rows.append({
            'stage': key,
            'label': label,
            'started_at': started,
            'ended_at': ended,
            'minutes': mins,
            'hours': _hours(mins),
            'open': idx == len(points) - 1 and sample.status not in ('RELEASED', 'REJECTED', 'CANCELLED'),
        })
    return rows


def result_milestones(sample) -> dict[str, Any]:
    result = _sample_result(sample)
    if not result:
        return {
            'entered_at': None, 'entered_by': '',
            'reviewed_at': None, 'reviewed_by': '',
            'authenticated_at': None, 'authenticated_by': '',
            'released_at': None, 'released_by': '',
        }
    return {
        'entered_at': result.entered_at,
        'entered_by': _actor_name(result.entered_by),
        'reviewed_at': result.reviewed_at,
        'reviewed_by': _actor_name(result.reviewed_by),
        'authenticated_at': result.authenticated_at,
        'authenticated_by': _actor_name(result.authenticated_by),
        'released_at': result.released_at,
        'released_by': _actor_name(result.released_by),
        'result_status': result.status,
        'version': result.version,
    }


def sample_timeline_payload(sample) -> dict[str, Any]:
    rule = resolve_tat_rule(sample.order.test)
    timeline = build_unified_timeline(sample)
    tat = compute_tat(sample, rule)
    return {
        'sample_id': sample.sample_id,
        'visit_id': sample.order.visit.visit_id,
        'patient': f'{sample.order.visit.patient.first_name} {sample.order.visit.patient.last_name}',
        'test': sample.order.test.name,
        'department': sample.order.test.department,
        'centre': sample.centre.code,
        'status': sample.status,
        'tat': tat,
        'stages': stage_durations(sample, timeline),
        'milestones': result_milestones(sample),
        'events': [
            {
                **row,
                'at': row['at'].isoformat() if row.get('at') else None,
            }
            for row in timeline
        ],
        'pauses': [
            {
                'id': str(p.pk),
                'reason': p.reason.name,
                'pauses_clock': p.reason.pauses_clock,
                'started_at': p.started_at.isoformat() if p.started_at else None,
                'ended_at': p.ended_at.isoformat() if p.ended_at else None,
                'started_by': _actor_name(p.started_by),
                'ended_by': _actor_name(p.ended_by),
                'note': p.note,
                'open': p.is_open,
            }
            for p in sample.tat_pauses.select_related('reason', 'started_by', 'ended_by')
        ],
    }


def pending_work_breakdown(user) -> dict[str, Any]:
    qs = scoped_open_samples(user)
    by_centre: dict[str, int] = defaultdict(int)
    by_department: dict[str, int] = defaultdict(int)
    by_role: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    rows = []
    for sample in qs.order_by('created_at')[:500]:
        centre = sample.centre.code
        dept = sample.order.test.department or '—'
        role = STATUS_ROLE.get(sample.status, 'Laboratory Scientist')
        # Prefer workflow step roles when bound.
        try:
            inst = getattr(sample.order, 'workflow_instance', None)
            if inst is None:
                from workflows.models import OrderWorkflowInstance
                inst = OrderWorkflowInstance.objects.filter(lab_order=sample.order).select_related('current_step').first()
            if inst and inst.current_step_id and inst.current_step.allowed_roles:
                role = inst.current_step.allowed_roles[0]
        except Exception:
            pass
        by_centre[centre] += 1
        by_department[dept] += 1
        by_role[role] += 1
        by_status[sample.status] += 1
        rows.append({
            'sample_id': sample.sample_id,
            'status': sample.status,
            'centre': centre,
            'department': dept,
            'role': role,
            'test': sample.order.test.name,
        })
    return {
        'by_centre': [{'key': k, 'count': v} for k, v in sorted(by_centre.items())],
        'by_department': [{'key': k, 'count': v} for k, v in sorted(by_department.items())],
        'by_role': [{'key': k, 'count': v} for k, v in sorted(by_role.items())],
        'by_status': [{'key': k, 'count': v} for k, v in sorted(by_status.items())],
        'items': rows[:100],
    }


def quality_rates(user, *, days=30) -> dict[str, Any]:
    since = timezone.now() - timedelta(days=days)
    qs = Sample.objects.filter(created_at__gte=since)
    if not user.is_superuser:
        qs = qs.filter(centre__user_access__user=user, centre__user_access__can_view=True).distinct()
    total = qs.count() or 0
    recollections = qs.filter(recollection_of__isnull=False).count()
    rejections = qs.filter(status='REJECTED').count()
    return {
        'window_days': days,
        'samples_total': total,
        'recollections': recollections,
        'rejections': rejections,
        'recollection_rate': round((recollections / total) * 100, 2) if total else 0.0,
        'rejection_rate': round((rejections / total) * 100, 2) if total else 0.0,
    }


def evaluate_escalations_for_sample(sample, rule: TatRule | None = None) -> list[TatEscalation]:
    """Create missing WARNING / OVERDUE / ESCALATED alerts for an open sample."""
    if sample.status in ('RELEASED', 'CANCELLED', 'REJECTED'):
        return []
    tat = compute_tat(sample, rule)
    if tat['status'] == 'PAUSED' or tat['status'] == 'ON_TRACK':
        return []
    created = []
    levels = []
    if tat['effective_minutes'] >= tat['warning_minutes']:
        levels.append('WARNING')
    if tat['effective_minutes'] >= tat['expected_minutes']:
        levels.append('OVERDUE')
    if tat['effective_minutes'] >= tat['escalation_minutes']:
        levels.append('ESCALATED')
    rule_obj = rule or resolve_tat_rule(sample.order.test)
    for level in levels:
        exists = TatEscalation.objects.filter(sample=sample, level=level, acknowledged_at__isnull=True).exists()
        if exists:
            continue
        row = TatEscalation.objects.create(
            sample=sample,
            rule=rule_obj,
            level=level,
            message=f'{level.title()} TAT for {sample.sample_id}: {tat["effective_hours"]}h vs { _hours(tat["expected_minutes"]) }h target',
            elapsed_minutes=int(tat['effective_minutes']),
            expected_minutes=int(tat['expected_minutes']),
            detail={
                'roles': tat['escalation_roles'],
                'centre': sample.centre.code,
                'department': sample.order.test.department,
                'status': sample.status,
            },
        )
        created.append(row)
    return created


def scan_escalations(user, *, limit=200) -> list[dict[str, Any]]:
    created_payload = []
    for sample in scoped_open_samples(user).order_by('created_at')[:limit]:
        for row in evaluate_escalations_for_sample(sample):
            created_payload.append({
                'id': str(row.pk),
                'sample_id': sample.sample_id,
                'level': row.level,
                'message': row.message,
            })
    return created_payload


def open_escalations(user):
    qs = TatEscalation.objects.filter(acknowledged_at__isnull=True).select_related(
        'sample__centre', 'sample__order__test', 'rule',
    )
    if not user.is_superuser:
        qs = qs.filter(sample__centre__user_access__user=user, sample__centre__user_access__can_view=True).distinct()
    return qs.order_by('-fired_at')


def tat_dashboard_payload(user, *, hours_overdue=None, scan=True) -> dict[str, Any]:
    """Expanded dashboard used by API and laboratory overview."""
    if scan:
        scan_escalations(user, limit=150)

    qs = scoped_open_samples(user)
    overdue_rows = []
    warning_rows = []
    on_track = 0
    paused = 0
    for sample in qs.order_by('created_at')[:300]:
        tat = compute_tat(sample)
        row = {
            'sample_id': sample.sample_id,
            'pk': sample.pk,
            'status': sample.status,
            'centre': sample.centre.code,
            'department': sample.order.test.department,
            'test': sample.order.test.name,
            'role': STATUS_ROLE.get(sample.status, 'Laboratory Scientist'),
            'effective_hours': tat['effective_hours'],
            'expected_hours': _hours(tat['expected_minutes']),
            'tat_status': tat['status'],
            'rule': tat['rule_name'],
        }
        if tat['status'] == 'PAUSED':
            paused += 1
        elif tat['status'] in ('OVERDUE', 'ESCALATED'):
            overdue_rows.append(row)
        elif tat['status'] == 'WARNING':
            warning_rows.append(row)
        else:
            on_track += 1

    by_status = list(qs.values('status').annotate(count=Count('id')).order_by('status'))
    pending = pending_work_breakdown(user)
    quality = quality_rates(user)
    escalations = [
        {
            'id': str(e.pk),
            'sample_id': e.sample.sample_id,
            'sample_pk': e.sample.pk,
            'level': e.level,
            'message': e.message,
            'fired_at': e.fired_at.isoformat() if e.fired_at else None,
            'centre': e.sample.centre.code,
            'department': e.sample.order.test.department,
            'roles': (e.rule.escalation_roles if e.rule_id else []) or e.detail.get('roles', []),
        }
        for e in open_escalations(user)[:50]
    ]

    # Backward-compatible keys used by the old overview widget.
    legacy_hours = hours_overdue if hours_overdue is not None else 24
    return {
        'open_total': qs.count(),
        'overdue_total': len(overdue_rows),
        'warning_total': len(warning_rows),
        'on_track_total': on_track,
        'paused_total': paused,
        'overdue_hours': legacy_hours,
        'by_status': by_status,
        'overdue_samples': overdue_rows[:50],
        'warning_samples': warning_rows[:50],
        'pending': pending,
        'quality': quality,
        'escalations': escalations,
        'result_milestones_note': (
            'Result entry, review, authentication and release each have dedicated timestamps '
            'on LabResult and appear on the sample timeline.'
        ),
    }
