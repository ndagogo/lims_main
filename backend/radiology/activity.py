"""Exam history helpers — Start/End Exam and related transitions."""
from radiology.models import RadiologyExamActivity


def record_exam_activity(exam, *, action, user=None, from_status='', to_status='', note='', request=None):
    ip = browser = ''
    if request is not None:
        ip = getattr(request, 'audit_ip', None)
        browser = getattr(request, 'audit_browser', '') or ''
    return RadiologyExamActivity.objects.create(
        exam=exam,
        action=action,
        from_status=from_status or '',
        to_status=to_status or '',
        note=note or '',
        performed_by=user if getattr(user, 'is_authenticated', False) else None,
        ip_address=ip or None,
        browser=(browser or '')[:255],
    )
