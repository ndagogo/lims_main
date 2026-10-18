"""Shared release-eligibility checks (documents, payment, workflow)."""
from django.core.exceptions import ValidationError
from django.db.models import Q

from billing.models import InvoiceItem


class ReleaseBlocker:
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def as_dict(self):
        return {'code': self.code, 'message': self.message}


def check_visit_release(visit, *, check_workflow=True):
    blockers = []
    if not visit.documents.filter(document_type__in=['REFERRAL', 'REGISTRATION']).exists():
        blockers.append(ReleaseBlocker(
            'missing_document',
            'A referral or registration form must be uploaded before result release.',
        ))
    linked = InvoiceItem.objects.filter(Q(lab_order__visit=visit) | Q(imaging_order__visit=visit))
    if linked.exists():
        statuses = set(linked.values_list('invoice__status', flat=True))
        if statuses - {'PAID'}:
            blockers.append(ReleaseBlocker(
                'unpaid_invoice',
                'Outstanding financial restrictions must be resolved before release.',
            ))
    if check_workflow:
        try:
            from workflows.services import workflow_blocks_release
            reason = workflow_blocks_release(visit)
            if reason:
                blockers.append(ReleaseBlocker('workflow', reason))
        except ImportError:
            pass
    return blockers


def _raise_release_blocked(blockers):
    """Raise an API-safe validation error (DRF when available)."""
    if not blockers:
        return
    message = blockers[0].message
    try:
        from rest_framework.exceptions import ValidationError as APIValidationError
        raise APIValidationError(message)
    except ImportError:
        raise ValidationError(message)


def assert_lab_result_releasable(result):
    blockers = check_visit_release(result.sample.order.visit)
    _raise_release_blocked(blockers)
    return True


def assert_radiology_report_releasable(report):
    """Imaging release follows the visit documents and payment. An open laboratory order does not hold it."""
    blockers = check_visit_release(report.exam.order.visit, check_workflow=False)
    _raise_release_blocked(blockers)
    return True


def release_eligibility_payload(visit):
    blockers = check_visit_release(visit)
    return {
        'eligible': not blockers,
        'blockers': [b.as_dict() for b in blockers],
    }
