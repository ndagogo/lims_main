"""Radiology screen actions. They use the imaging API, including its centre checks."""
from django.contrib.auth import get_user_model

from billing.visibility import imaging_payment_complete, settled
from core.models import UserCentreAccess
from core.paging import paginate
from core.staff_api import call_action, call_create, describe
from patients.models import Visit
from radiology.api import ImagingOrderViewSet, RadiologyExamViewSet, RadiologyReportViewSet
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def handle_radiology_post(request):
    action = request.POST.get('action')
    user = request.user
    post = request.POST
    if action == 'create':
        return False, 'Imaging studies are added at registration. They appear here after the paypoint completes payment.'
    if action in ('assign', 'start', 'end', 'draft', 'finalize', 'override') and not imaging_payment_complete(
        order_id=post.get('order') or None, exam_id=post.get('exam') or None, report_id=post.get('report') or None,
    ):
        return False, 'This study stays off the radiology worklist until the paypoint completes payment.'
    if action == 'assign':
        return _finish(*call_action(ImagingOrderViewSet, 'assign', user, post.get('order'), {'radiographer': post.get('radiographer')}), 'Radiographer assigned.')
    if action == 'start':
        return _finish(*call_action(RadiologyExamViewSet, 'start', user, post.get('exam'), {}), 'Exam started.')
    if action == 'end':
        return _finish(*call_action(RadiologyExamViewSet, 'end', user, post.get('exam'), {'acquisition_notes': post.get('acquisition_notes', '')}), 'Exam ended.')
    if action == 'draft':
        return _finish(*call_create(RadiologyReportViewSet, user, {
            'exam': post.get('exam'), 'findings': post.get('findings', ''),
            'impression': post.get('impression', ''), 'recommendations': post.get('recommendations', ''),
        }), 'Report draft saved.')
    if action == 'finalize':
        return _finish(*call_action(RadiologyReportViewSet, 'finalize', user, post.get('report'), {}), 'Report finalised.')
    if action == 'override':
        return _finish(*call_action(RadiologyReportViewSet, 'override', user, post.get('report'), {
            'reason': post.get('reason', ''), 'findings': post.get('findings', ''),
            'impression': post.get('impression', ''), 'recommendations': post.get('recommendations', ''),
        }), 'Report amended. The previous version was kept.')
    return False, 'Choose a radiology action.'


def build_radiology_context(request):
    user = request.user
    centre_ids = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True).values_list('centre_id', flat=True)
    orders = settled(ImagingOrder.objects.select_related('visit__patient', 'visit__centre', 'modality', 'procedure', 'exam'), 'imaging_order_id').order_by('-created_at')
    exams = settled(
        RadiologyExam.objects.select_related('order__visit__patient', 'order__visit__centre', 'order__modality', 'assigned_radiographer'),
        'imaging_order_id', 'order_id',
    )
    reports = settled(
        RadiologyReport.objects.select_related('exam__order__visit__patient', 'exam__order__visit__centre', 'author'),
        'imaging_order_id', 'exam__order_id',
    ).order_by('-updated_at')
    visits = Visit.objects.select_related('patient', 'centre').order_by('-created_at')
    modalities = ImagingModality.objects.filter(is_active=True).select_related('centre')
    if not user.is_superuser:
        orders = orders.filter(visit__centre_id__in=centre_ids)
        exams = exams.filter(order__visit__centre_id__in=centre_ids)
        reports = reports.filter(exam__order__visit__centre_id__in=centre_ids)
        visits = visits.filter(centre_id__in=centre_ids)
        modalities = modalities.filter(centre_id__in=centre_ids)
    staff = get_user_model().objects.filter(is_active=True, centre_access__centre_id__in=centre_ids, centre_access__can_edit=True).distinct().order_by('username')
    return {
        'orders': paginate(request, orders, 'radpage'),
        'exams': paginate(request, exams.order_by('-created_at'), 'expage'),
        'reports': paginate(request, reports, 'repage'),
        'visits': visits,
        'modalities': modalities,
        'staff': staff,
        'order_counts': {status: orders.filter(status=status).count() for status, _label in ImagingOrder.STATUSES},
    }
