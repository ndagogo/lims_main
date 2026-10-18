"""Reporting access: assigned radiologist or authorised substitute only for unsigned reports."""
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.access import RADIOLOGIST_ROLES, require_any_role


def can_edit_unsigned_report(user, report) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    exam = report.exam
    if exam.assigned_radiologist_id and exam.assigned_radiologist_id == user.pk:
        return True
    if exam.reporting_substitute_id and exam.reporting_substitute_id == user.pk:
        return True
    return False


def assert_can_edit_unsigned_report(user, report):
    """Only the assigned reporting radiologist or an authorised substitute may edit drafts."""
    require_any_role(user, RADIOLOGIST_ROLES, perm='radiology.finalize_report')
    exam = report.exam
    if not exam.assigned_radiologist_id and not exam.reporting_substitute_id:
        raise ValidationError('Assign a reporting radiologist before preparing or editing the report.')
    if can_edit_unsigned_report(user, report):
        return 'substitute' if exam.reporting_substitute_id == user.pk and exam.assigned_radiologist_id != user.pk else 'assigned'
    raise PermissionDenied(
        'Only the assigned reporting radiologist, or a properly authorised substitute, '
        'may edit an unsigned report.'
    )


def assert_can_create_report(user, exam):
    require_any_role(user, RADIOLOGIST_ROLES, perm='radiology.finalize_report')
    if not exam.assigned_radiologist_id and not exam.reporting_substitute_id:
        raise ValidationError('Assign a reporting radiologist before preparing a report.')
    if user.is_superuser:
        return
    if exam.assigned_radiologist_id == user.pk:
        return
    if exam.reporting_substitute_id == user.pk:
        return
    raise PermissionDenied(
        'Only the assigned reporting radiologist, or a properly authorised substitute, '
        'may prepare this report.'
    )


def assert_can_sign_report(user, report):
    require_any_role(user, RADIOLOGIST_ROLES, perm='radiology.finalize_report')
    exam = report.exam
    if user.is_superuser:
        return
    if exam.assigned_radiologist_id == user.pk:
        return
    if exam.reporting_substitute_id == user.pk:
        return
    raise PermissionDenied('Only the assigned reporting radiologist or authorised substitute may sign this report.')
