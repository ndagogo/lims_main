"""Radiology desk rules: who sees a study, exam duration, and accept-window expiry."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from radiology.models import RadiologyDeskSetting, RadiologyExam

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
    """Reassignment stays closed while the assigned radiologist is still inside the accept window."""
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


def release_expired_assignments():
    """Unaccepted studies past the platform window go back on the assignment list."""
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
        exam.assigned_radiologist = None
        exam.radiologist_assigned_at = None
        exam.save(update_fields=['assigned_radiologist', 'radiologist_assigned_at', 'updated_at'])
        from radiology.activity import record_exam_activity
        record_exam_activity(
            exam, action='REASSIGN_OPEN', user=None,
            from_status=exam.status, to_status=exam.status,
            note=f'Accept window of {window.accept_minutes} min elapsed for {name}.',
        )
        released += 1
    return released


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
