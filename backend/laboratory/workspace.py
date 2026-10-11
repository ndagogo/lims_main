"""Laboratory screen actions. Buttons call the same transitions the API enforces."""
from django.core.exceptions import ObjectDoesNotExist

from billing.visibility import settled
from core.models import UserCentreAccess
from core.paging import paginate
from core.staff_api import call_action, call_create, describe
from integrations.api import AnalyzerMessageViewSet
from integrations.models import AnalyzerMessage
from laboratory.api import LabOrderViewSet, LabResultViewSet, SampleBatchViewSet, SampleViewSet
from laboratory.models import LabOrder, LabResult, Sample, SampleBatch


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def _values(post, prefix):
    rows = []
    for key, value in post.items():
        if key.startswith(prefix) and str(value).strip():
            rows.append({'parameter': key[len(prefix):], 'value': value})
    return rows


def handle_laboratory_post(request):
    action = request.POST.get('action')
    user = request.user
    post = request.POST
    if action == 'collect':
        return _finish(*call_action(LabOrderViewSet, 'collect', user, post.get('order'), {'note': post.get('note', '')}), 'Sample collected.')
    if action == 'recollect':
        return _finish(*call_action(SampleViewSet, 'recollect', user, post.get('sample'), {'reason': post.get('reason', '')}), 'Recollection created.')
    if action == 'reject':
        return _finish(*call_action(SampleViewSet, 'reject', user, post.get('sample'), {'reason': post.get('reason', '')}), 'Sample rejected.')
    if action == 'batch':
        return _finish(*call_create(SampleBatchViewSet, user, {
            'centre': post.get('centre'), 'department': post.get('department'), 'sample_ids': post.getlist('sample_ids'),
        }), 'Batch created.')
    if action == 'dispatch':
        return _finish(*call_action(SampleBatchViewSet, 'dispatch_batch', user, post.get('batch'), {}), 'Batch dispatched.')
    if action == 'receive':
        return _finish(*call_action(SampleBatchViewSet, 'receive', user, post.get('batch'), {}), 'Batch received.')
    if action == 'start_processing':
        return _finish(*call_action(SampleViewSet, 'start_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing started.')
    if action == 'complete_processing':
        return _finish(*call_action(SampleViewSet, 'complete_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing completed.')
    if action == 'enter_result':
        return _finish(*call_action(SampleViewSet, 'enter_result', user, post.get('sample'), {
            'narrative': post.get('narrative', ''), 'values': _values(post, 'param-'),
        }), 'Draft result saved.')
    if action == 'submit_review':
        return _finish(*call_action(LabResultViewSet, 'submit_review', user, post.get('result'), {'note': post.get('note', '')}), 'Result submitted for review.')
    if action == 'review':
        return _finish(*call_action(LabResultViewSet, 'review', user, post.get('result'), {
            'decision': post.get('decision'), 'note': post.get('note', ''),
        }), 'Review recorded.')
    if action == 'authenticate':
        return _finish(*call_action(LabResultViewSet, 'authenticate', user, post.get('result'), {'note': post.get('note', '')}), 'Result authenticated.')
    if action == 'release':
        return _finish(*call_action(LabResultViewSet, 'release', user, post.get('result'), {'note': post.get('note', '')}), 'Result released.')
    if action == 'amend':
        return _finish(*call_action(LabResultViewSet, 'amend', user, post.get('result'), {
            'reason': post.get('reason', ''), 'narrative': post.get('narrative', ''), 'values': _values(post, 'amend-'),
        }), 'Released result amended. The previous version was kept.')
    if action == 'apply_message':
        return _finish(*call_action(AnalyzerMessageViewSet, 'apply_to_draft', user, post.get('message'), {}), 'Analyzer values saved as a draft. They are not released.')
    return False, 'Choose a laboratory action.'


def _result_of(sample):
    try:
        return sample.result
    except ObjectDoesNotExist:
        return None


def handle_phlebotomy_post(request):
    action = request.POST.get('action')
    if action not in ('collect', 'recollect'):
        return False, 'Phlebotomy can collect a sample or record a recollection.'
    return handle_laboratory_post(request)


def build_phlebotomy_context(request):
    user = request.user
    orders = settled(
        LabOrder.objects.select_related('visit__patient', 'visit__centre', 'test').filter(status__in=['ORDERED', 'COLLECTION_PENDING']),
        'lab_order_id',
    )
    collected = settled(
        Sample.objects.select_related('centre', 'order__test', 'order__visit__patient').filter(status='COLLECTED'),
        'lab_order_id', 'order_id',
    )
    if not user.is_superuser:
        centre_ids = user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
        orders = orders.filter(visit__centre_id__in=centre_ids)
        collected = collected.filter(centre_id__in=centre_ids)
    return {
        'pending_orders': paginate(request, orders.order_by('-created_at'), 'opage'),
        'collected_samples': paginate(request, collected.order_by('-collected_at'), 'cpage'),
    }


def build_laboratory_context(request):
    user = request.user
    centre_ids = user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
    samples = settled(
        Sample.objects.select_related('centre', 'order__test', 'order__visit__patient').prefetch_related('order__test__parameters', 'processing_records'),
        'lab_order_id', 'order_id',
    )
    batches = SampleBatch.objects.select_related('centre')
    results = settled(
        LabResult.objects.select_related('sample__centre', 'sample__order__test', 'sample__order__visit__patient'),
        'lab_order_id', 'sample__order_id',
    )
    orders = settled(
        LabOrder.objects.select_related('visit__patient', 'visit__centre', 'test').exclude(status__in=['COMPLETED', 'CANCELLED']),
        'lab_order_id',
    )
    messages = AnalyzerMessage.objects.select_related('analyzer', 'sample').filter(status__in=['PARSED', 'PENDING_REVIEW', 'MAPPED'])
    if not user.is_superuser:
        samples = samples.filter(centre_id__in=centre_ids)
        batches = batches.filter(centre_id__in=centre_ids)
        results = results.filter(sample__centre_id__in=centre_ids)
        orders = orders.filter(visit__centre_id__in=centre_ids)
        messages = messages.filter(analyzer__centre_id__in=centre_ids)
    actionable = paginate(request, samples.exclude(status__in=['RELEASED', 'CANCELLED']).order_by('-created_at'), 'spage')
    for sample in actionable.object_list:
        sample.result_row = _result_of(sample)
        sample.open_processing = any(row.completed_at is None for row in sample.processing_records.all())
    collected = samples.filter(status='COLLECTED').order_by('-collected_at')
    departments = sorted({department for department in collected.values_list('order__test__department', flat=True) if department})
    centres = UserCentreAccess.objects.filter(user=user, can_edit=True, centre__is_active=True).select_related('centre')
    if user.is_superuser:
        from core.models import Centre
        centre_rows = Centre.objects.filter(is_active=True)
    else:
        centre_rows = [row.centre for row in centres]
    return {
        'sample_counts': {status: samples.filter(status=status).count() for status, _label in Sample.STATUS_CHOICES},
        'actionable_samples': actionable,
        'collected_samples': paginate(request, collected, 'cpage'),
        'departments': departments,
        'batch_centres': centre_rows,
        'recent_batches': paginate(request, batches.order_by('-created_at'), 'bpage'),
        'pending_orders': paginate(request, orders.order_by('-created_at'), 'opage'),
        'pending_results': results.filter(status__in=['DRAFT', 'SUBMITTED', 'REVIEWED', 'AUTHENTICATED']).order_by('-created_at'),
        'analyzer_messages': paginate(request, messages.order_by('-received_at'), 'apage'),
    }
