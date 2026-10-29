"""Modified approval and result dispatch, shared by radiology and laboratory."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ResultLedger, UserCentreAccess


REASON_LABEL = 'Reason For Modification'


def _in_group(user, *names):
    return user.groups.filter(name__in=names).exists()


def user_can_approve_radiology(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or _in_group(user, 'Platform Administrator'):
        return True
    return user.has_perm('radiology.approve_result') or user.has_perm('radiology.authenticate_result')


def user_can_dispatch_radiology(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or _in_group(user, 'Platform Administrator'):
        return True
    return user.has_perm('radiology.dispatch_result')


def user_can_modify_radiology(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or _in_group(user, 'Platform Administrator', 'Radiologist'):
        return True
    return user.has_perm('radiology.modify_result')


def user_can_dispatch_laboratory(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or _in_group(user, 'Platform Administrator', 'Lab Manager'):
        return True
    return user.has_perm('laboratory.release_labresult') or user.has_perm('laboratory.dispatch_lab_result')


def user_can_modify_laboratory(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or _in_group(user, 'Platform Administrator', 'Lab Manager'):
        return True
    return user.has_perm('laboratory.modify_lab_result')


def user_can_open_modify(user):
    return user_can_modify_radiology(user) or user_can_modify_laboratory(user)


def user_can_open_dispatch(user):
    return user_can_dispatch_radiology(user) or user_can_dispatch_laboratory(user)


def _sees_centre(user, centre):
    if user.is_superuser:
        return True
    return UserCentreAccess.objects.filter(
        user=user, centre=centre, centre__is_active=True, can_view=True,
    ).exists()


def _require_reason(reason):
    text = (reason or '').strip()
    if not text:
        raise ValidationError(f'{REASON_LABEL} is required.')
    return text


def _write(kind, service, visit, object_type, object_id, actor, summary='', reason='', request=None):
    row = ResultLedger.objects.create(
        kind=kind, service=service, visit=visit, object_type=object_type,
        object_id=str(object_id), summary=(summary or '')[:200], reason=reason or '', actor=actor,
    )
    from core.audit import record_audit
    record_audit(
        actor=actor, action=f'RESULT_{kind}', object_type=object_type, object_id=str(object_id),
        request=request, centre=getattr(visit, 'centre', None),
        visit_ref=getattr(visit, 'visit_id', '') or '', result_ref=str(object_id),
        reason=reason or '', new_values={'service': service, 'summary': summary, 'ledger_id': row.pk},
        category='CLINICAL',
    )
    return row


def record_dispatch(*, user, service, visit, object_type, object_id, summary, request=None):
    """Always append a dispatch row, including when the same result is dispatched again."""
    return _write('DISPATCH', service, visit, object_type, object_id, user, summary=summary, request=request)


def approve_radiology_report(user, report, request=None):
    if not user_can_approve_radiology(user):
        raise PermissionDenied('You do not have the right to approve a radiology result.')
    visit = report.exam.order.visit
    if not _sees_centre(user, visit.centre):
        raise PermissionDenied('You cannot approve a result outside your centre.')
    if report.author_id == user.id and not user.is_superuser:
        raise PermissionDenied('The reporting radiologist cannot approve their own result.')
    if report.status != 'FINAL':
        raise ValidationError('Only a reported result can be approved.')
    report.status = 'APPROVED'
    report.approved_at = timezone.now()
    report.approved_by = user
    report.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])
    _write(
        'APPROVE', 'RADIOLOGY', visit, 'RadiologyReport', report.pk, user,
        summary=f'{visit.visit_id} approved for dispatch', request=request,
    )
    return report


def dispatch_radiology_report(user, report, request=None):
    if not user_can_dispatch_radiology(user):
        raise PermissionDenied('You do not have the right to dispatch a radiology result.')
    visit = report.exam.order.visit
    if not _sees_centre(user, visit.centre):
        raise PermissionDenied('You cannot dispatch a result outside your centre.')
    summary = f'{visit.visit_id} radiology result'
    if report.status in ('RELEASED', 'AMENDED'):
        record_dispatch(
            user=user, service='RADIOLOGY', visit=visit, object_type='RadiologyReport',
            object_id=report.pk, summary=summary, request=request,
        )
        return report
    if report.status != 'APPROVED':
        raise ValidationError('Approve the reported result before dispatch.')
    from billing.eligibility import assert_radiology_report_releasable
    assert_radiology_report_releasable(report)
    with transaction.atomic():
        report.status = 'RELEASED'
        report.released_at = timezone.now()
        report.released_by = user
        report.save(update_fields=['status', 'released_at', 'released_by', 'updated_at'])
        order = report.exam.order
        order.status = 'COMPLETED'
        order.save(update_fields=['status', 'updated_at'])
        record_dispatch(
            user=user, service='RADIOLOGY', visit=visit, object_type='RadiologyReport',
            object_id=report.pk, summary=summary, request=request,
        )
    from radiology.activity import record_exam_activity
    record_exam_activity(
        report.exam, action='REPORT_RELEASED', user=user,
        from_status=report.exam.status, to_status=report.exam.status, request=request,
    )
    return report


def return_radiology_for_modification(user, report, reason, request=None):
    if not user_can_modify_radiology(user):
        raise PermissionDenied('You do not have the right to modify a radiology result.')
    visit = report.exam.order.visit
    if not _sees_centre(user, visit.centre):
        raise PermissionDenied('You cannot modify a result outside your centre.')
    text = _require_reason(reason)
    if report.status not in ('FINAL', 'APPROVED', 'RELEASED', 'AMENDED'):
        raise ValidationError('Only a reported result can be returned for modification.')
    previous = report.status
    with transaction.atomic():
        report.status = 'DRAFT'
        report.finalized_at = None
        report.finalized_by = None
        report.approved_at = None
        report.approved_by = None
        report.released_at = None
        report.released_by = None
        report.save(update_fields=[
            'status', 'finalized_at', 'finalized_by', 'approved_at', 'approved_by',
            'released_at', 'released_by', 'updated_at',
        ])
        order = report.exam.order
        if order.status == 'COMPLETED':
            order.status = 'IN_PROGRESS'
            order.save(update_fields=['status', 'updated_at'])
        _write(
            'MODIFY', 'RADIOLOGY', visit, 'RadiologyReport', report.pk, user,
            summary=f'{visit.visit_id} returned from {previous}', reason=text, request=request,
        )
    return report


def dispatch_laboratory_result(user, result, request=None):
    if not user_can_dispatch_laboratory(user):
        raise PermissionDenied('You do not have the right to dispatch a laboratory result.')
    visit = result.sample.order.visit
    if not _sees_centre(user, visit.centre):
        raise PermissionDenied('You cannot dispatch a result outside your centre.')
    summary = f'{visit.visit_id} laboratory result'
    if result.status in ('RELEASED', 'AMENDED'):
        record_dispatch(
            user=user, service='LABORATORY', visit=visit, object_type='LabResult',
            object_id=result.pk, summary=summary, request=request,
        )
        return result
    if result.status != 'AUTHENTICATED':
        raise ValidationError('Only an authenticated laboratory result can be dispatched.')
    from core.staff_api import call_action, describe
    from laboratory.api import LabResultViewSet
    status, body = call_action(LabResultViewSet, 'release', user, result.pk, {})
    if status >= 400:
        raise ValidationError(describe(body))
    result.refresh_from_db()
    return result


def return_laboratory_for_modification(user, result, reason, request=None):
    if not user_can_modify_laboratory(user):
        raise PermissionDenied('You do not have the right to modify a laboratory result.')
    visit = result.sample.order.visit
    if not _sees_centre(user, visit.centre):
        raise PermissionDenied('You cannot modify a result outside your centre.')
    text = _require_reason(reason)
    if result.status not in ('AUTHENTICATED', 'RELEASED', 'AMENDED'):
        raise ValidationError('Only a dispatched or authenticated laboratory result can be returned for modification.')
    from laboratory.models import ResultReview
    previous = result.status
    with transaction.atomic():
        ResultReview.objects.create(
            result=result, action='AMENDED', reviewer=user,
            note=f'{REASON_LABEL}: {text}',
            snapshot={'status': previous, 'narrative': result.narrative, 'version': result.version},
        )
        result.status = 'DRAFT'
        result.version += 1
        result.released_at = None
        result.released_by = None
        result.save(update_fields=['status', 'version', 'released_at', 'released_by', 'updated_at'])
        sample = result.sample
        if sample.status in ('AUTHENTICATED', 'RELEASED'):
            sample.status = 'RESULT_ENTERED'
            sample.save(update_fields=['status', 'updated_at'])
        order = sample.order
        if order.status == 'COMPLETED':
            order.status = 'IN_PROGRESS'
            order.save(update_fields=['status', 'updated_at'])
        _write(
            'MODIFY', 'LABORATORY', visit, 'LabResult', result.pk, user,
            summary=f'{visit.visit_id} returned from {previous}', reason=text, request=request,
        )
    return result


def visit_results(user, visit_id):
    from laboratory.models import LabResult
    from patients.models import Visit
    from radiology.models import RadiologyReport

    number = (visit_id or '').strip()
    visit = Visit.objects.filter(visit_id__iexact=number).select_related('patient', 'centre').first()
    if visit is None or not _sees_centre(user, visit.centre):
        return None, [], []
    reports = []
    if user_can_modify_radiology(user):
        reports = list(
            RadiologyReport.objects.select_related('exam__order__modality', 'exam__order__procedure', 'author')
            .filter(exam__order__visit=visit)
            .exclude(status='DRAFT')
            .order_by('-updated_at')
        )
    results = []
    if user_can_modify_laboratory(user):
        results = list(
            LabResult.objects.select_related('sample__order__test')
            .filter(sample__order__visit=visit)
            .exclude(status='DRAFT')
            .order_by('-updated_at')
        )
    return visit, reports, results
