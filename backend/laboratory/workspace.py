"""Laboratory screen actions. Buttons call the same transitions the API enforces."""
from django.core.exceptions import ObjectDoesNotExist

from billing.visibility import settled
from core.models import UserCentreAccess
from core.paging import paginate
from core.staff_api import call_action, call_create, describe
from integrations.api import AnalyzerMessageViewSet
from integrations.models import AnalyzerMessage
from laboratory.api import LabOrderViewSet, LabResultViewSet, SampleBatchViewSet, SampleViewSet
from laboratory.models import LabOrder, LabResult, Sample, SampleActivity, SampleBatch


LAB_STAGES = [
    ('batch', 'Batch', 'Create and dispatch sample batches'),
    ('receive', 'Receive', 'Department receiving'),
    ('process', 'Process', 'Processing and activity logging'),
    ('entry', 'Results', 'Result entry'),
    ('review', 'Review', 'First review / validation'),
    ('auth', 'Authenticate', 'Result authentication'),
    ('release', 'Release', 'Controlled result release'),
    ('overview', 'Overview', 'Counts, TAT, QC, analyzers'),
]


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def _values(post, prefix):
    """Collect parameter values; MULTI_CHOICE may post multiple values for one key."""
    rows = []
    seen = set()
    for key in post.keys():
        if not key.startswith(prefix):
            continue
        param_id = key[len(prefix):]
        if param_id in seen:
            continue
        seen.add(param_id)
        parts = [str(v).strip() for v in post.getlist(key) if str(v).strip()]
        if not parts:
            continue
        comment = (post.get(f'comment-{param_id}') or '').strip()
        rows.append({
            'parameter': param_id,
            'value': ', '.join(parts),
            'comment': comment,
        })
    return rows


def handle_laboratory_post(request):
    action = request.POST.get('action')
    user = request.user
    post = request.POST
    if action == 'collect':
        status, body = call_action(LabOrderViewSet, 'collect', user, post.get('order'), {'note': post.get('note', '')})
        if 200 <= status < 300 and isinstance(body, dict) and body.get('id'):
            sid = body.get('sample_id') or ''
            return True, f'Sample {sid} collected. Unique barcode ready to print.', body['id']
        return _finish(status, body, 'Sample collected.')
    if action == 'recollect':
        status, body = call_action(SampleViewSet, 'recollect', user, post.get('sample'), {
            'reason': post.get('reason', ''),
            'disposition': post.get('disposition', 'SUPERSEDED'),
            'corrective_action': post.get('corrective_action', ''),
        })
        if 200 <= status < 300 and isinstance(body, dict) and body.get('id'):
            sid = body.get('sample_id') or ''
            return True, f'Recollection {sid} created. Original sample superseded. Print the new barcode.', body['id']
        return _finish(status, body, 'Recollection created.')
    if action == 'reject':
        return _finish(*call_action(SampleViewSet, 'reject', user, post.get('sample'), {
            'reason': post.get('reason', ''),
            'disposition': post.get('disposition', 'REJECTED'),
            'corrective_action': post.get('corrective_action', ''),
        }), 'Sample rejected.')
    if action == 'batch':
        return _finish(*call_create(SampleBatchViewSet, user, {
            'centre': post.get('centre'), 'department': post.get('department'), 'sample_ids': post.getlist('sample_ids'),
        }), 'Batch created.')
    if action == 'dispatch':
        return _finish(*call_action(SampleBatchViewSet, 'dispatch_batch', user, post.get('batch'), {}), 'Batch dispatched.')
    if action == 'receive':
        return _finish(*call_action(SampleBatchViewSet, 'receive', user, post.get('batch'), {}), 'Batch received by department.')
    if action == 'start_processing':
        return _finish(*call_action(SampleViewSet, 'start_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing started.')
    if action == 'complete_processing':
        return _finish(*call_action(SampleViewSet, 'complete_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing completed.')
    if action == 'enter_result':
        payload = {'narrative': post.get('narrative', ''), 'values': _values(post, 'param-')}
        if post.get('template'):
            payload['template'] = post.get('template')
        return _finish(*call_action(SampleViewSet, 'enter_result', user, post.get('sample'), payload), 'Draft result saved.')
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
    if action == 'tat_pause':
        return _finish(*call_action(SampleViewSet, 'tat_pause', user, post.get('sample'), {
            'reason': post.get('reason'), 'note': post.get('note', ''),
        }), 'TAT clock paused with documented reason.')
    if action == 'tat_resume':
        return _finish(*call_action(SampleViewSet, 'tat_resume', user, post.get('sample'), {
            'note': post.get('note', ''),
        }), 'TAT clock resumed.')
    if action == 'ack_escalation':
        from laboratory.api import TatEscalationViewSet
        return _finish(*call_action(TatEscalationViewSet, 'acknowledge', user, post.get('escalation'), {}), 'Escalation acknowledged.')
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
    samples = settled(
        Sample.objects.select_related('centre', 'order__test', 'order__visit__patient', 'collected_by'),
        'lab_order_id', 'order_id',
    )
    if not user.is_superuser:
        centre_ids = user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
        orders = orders.filter(visit__centre_id__in=centre_ids)
        samples = samples.filter(centre_id__in=centre_ids)
    collected = samples.filter(status='COLLECTED')
    collected_ever = samples.filter(collected_at__isnull=False)
    history = _collection_history(request, collected_ever)
    return {
        'pending_orders': paginate(request, orders.order_by('-created_at'), 'opage'),
        'collected_samples': paginate(request, collected.order_by('-collected_at'), 'cpage'),
        'collection_history': paginate(request, history, 'hpage'),
        'pending_count': orders.count(),
        'collected_count': collected.count(),
        'history_count': collected_ever.count(),
        'history_query': (request.GET.get('hq') or '').strip(),
        'history_from': (request.GET.get('hfrom') or '').strip(),
        'history_to': (request.GET.get('hto') or '').strip(),
    }


def _collection_history(request, samples):
    """Every sample that was collected, including those that later left phlebotomy."""
    from django.db.models import Q
    from django.utils.dateparse import parse_date

    history = samples
    text = (request.GET.get('hq') or '').strip()
    if text:
        history = history.filter(
            Q(sample_id__icontains=text)
            | Q(order__visit__visit_id__icontains=text)
            | Q(order__visit__patient__uhid__icontains=text)
            | Q(order__visit__patient__first_name__icontains=text)
            | Q(order__visit__patient__last_name__icontains=text)
            | Q(order__test__name__icontains=text)
        )
    start = parse_date(request.GET.get('hfrom') or '')
    end = parse_date(request.GET.get('hto') or '')
    if start:
        history = history.filter(collected_at__date__gte=start)
    if end:
        history = history.filter(collected_at__date__lte=end)
    return history.order_by('-collected_at', '-created_at')


def _annotate_samples(queryset, *, with_entry_fields=False):
    from laboratory.templates_svc import entry_fields_for_sample

    rows = list(queryset)
    for sample in rows:
        sample.result_row = _result_of(sample)
        sample.open_processing = any(row.completed_at is None for row in sample.processing_records.all())
        sample.recent_activities = list(sample.activities.select_related('performed_by').order_by('-created_at')[:8])
        if with_entry_fields:
            template, fields = entry_fields_for_sample(sample)
            sample.entry_template = template
            sample.entry_fields = fields
    return rows


def build_laboratory_context(request):
    user = request.user
    stage = (request.GET.get('stage') or 'batch').strip().lower()
    if stage not in {key for key, *_ in LAB_STAGES}:
        stage = 'batch'
    centre_ids = user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
    samples = settled(
        Sample.objects.select_related(
            'centre', 'order__test', 'order__test__default_template', 'order__visit__patient', 'collected_by',
        ).prefetch_related(
            'order__test__parameters__reference_ranges',
            'order__test__default_template__fields__parameter',
            'order__test__result_templates__fields__parameter',
            'processing_records', 'activities',
        ),
        'lab_order_id', 'order_id',
    )
    batches = SampleBatch.objects.select_related('centre', 'created_by', 'received_by').prefetch_related('items__sample')
    results = settled(
        LabResult.objects.select_related(
            'sample__centre', 'sample__order__test', 'sample__order__visit__patient',
            'entered_by', 'reviewed_by', 'authenticated_by',
        ).prefetch_related('sample__order__test__parameters', 'values__parameter'),
        'lab_order_id', 'sample__order_id',
    )
    messages = AnalyzerMessage.objects.select_related('analyzer', 'sample').filter(status__in=['PARSED', 'PENDING_REVIEW', 'MAPPED'])
    if not user.is_superuser:
        samples = samples.filter(centre_id__in=centre_ids)
        batches = batches.filter(centre_id__in=centre_ids)
        results = results.filter(sample__centre_id__in=centre_ids)
        messages = messages.filter(analyzer__centre_id__in=centre_ids)

    collected = samples.filter(status='COLLECTED').order_by('-collected_at')
    departments = sorted({department for department in collected.values_list('order__test__department', flat=True) if department})
    centres = UserCentreAccess.objects.filter(user=user, can_edit=True, centre__is_active=True).select_related('centre')
    if user.is_superuser:
        from core.models import Centre
        centre_rows = Centre.objects.filter(is_active=True)
    else:
        centre_rows = [row.centre for row in centres]

    from laboratory.models import QcMaterial, QcRun
    from laboratory.tat import tat_dashboard_payload
    qc_materials = QcMaterial.objects.filter(is_active=True).select_related('centre', 'test')
    if not user.is_superuser:
        qc_materials = qc_materials.filter(centre__user_access__user=user, centre__user_access__can_view=True).distinct()
    recent_qc = QcRun.objects.filter(material__in=qc_materials).select_related('material', 'parameter').order_by('-run_at')[:20]

    sample_counts = {status: samples.filter(status=status).count() for status, _label in Sample.STATUS_CHOICES}
    stage_counts = {
        'batch': sample_counts.get('COLLECTED', 0),
        'receive': batches.filter(status='DISPATCHED').count() + sample_counts.get('BATCHED', 0),
        'process': sample_counts.get('RECEIVED', 0) + sample_counts.get('PROCESSING', 0),
        'entry': samples.filter(status='PROCESSING').count() + sample_counts.get('RESULT_ENTERED', 0),
        'review': results.filter(status__in=['DRAFT', 'SUBMITTED']).count(),
        'auth': results.filter(status='REVIEWED').count(),
        'release': results.filter(status='AUTHENTICATED').count(),
        'overview': samples.exclude(status__in=['RELEASED', 'CANCELLED', 'REJECTED', 'SUPERSEDED']).count(),
    }

    ctx = {
        'lab_stages': LAB_STAGES,
        'active_stage': stage,
        'stage_counts': stage_counts,
        'sample_counts': sample_counts,
        'batch_centres': centre_rows,
        'departments': departments,
        'tat_summary': tat_dashboard_payload(user, scan=False),
        'qc_materials': qc_materials[:30],
        'recent_qc_runs': recent_qc,
        'analyzer_messages': paginate(request, messages.order_by('-received_at'), 'apage'),
    }

    if stage == 'batch':
        ctx['collected_samples'] = paginate(request, collected, 'cpage')
        ctx['recent_batches'] = paginate(request, batches.order_by('-created_at'), 'bpage')
    elif stage == 'receive':
        ctx['open_batches'] = paginate(
            request,
            batches.filter(status__in=['OPEN', 'DISPATCHED', 'RECEIVED']).order_by('-created_at'),
            'bpage',
        )
    elif stage == 'process':
        queue = samples.filter(status__in=['RECEIVED', 'PROCESSING']).order_by('-updated_at')
        page = paginate(request, queue, 'spage')
        page.object_list = _annotate_samples(page.object_list)
        ctx['process_samples'] = page
    elif stage == 'entry':
        queue = samples.filter(status__in=['PROCESSING', 'RESULT_ENTERED']).order_by('-updated_at')
        page = paginate(request, queue, 'spage')
        annotated = _annotate_samples(page.object_list, with_entry_fields=True)
        # Ready for entry: processing finished, or an existing draft to correct.
        ready = []
        for sample in annotated:
            if sample.open_processing:
                continue
            if sample.status == 'PROCESSING' or (sample.result_row and sample.result_row.status == 'DRAFT'):
                ready.append(sample)
        page.object_list = ready or [s for s in annotated if not s.open_processing]
        ctx['entry_samples'] = page
        ctx['draft_results'] = results.filter(status='DRAFT').order_by('-updated_at')[:40]
    elif stage == 'review':
        ctx['review_results'] = paginate(
            request,
            results.filter(status__in=['DRAFT', 'SUBMITTED']).order_by('-updated_at'),
            'rpage',
        )
    elif stage == 'auth':
        ctx['auth_results'] = paginate(
            request,
            results.filter(status='REVIEWED').order_by('-reviewed_at'),
            'rpage',
        )
    elif stage == 'release':
        ctx['release_results'] = paginate(
            request,
            results.filter(status='AUTHENTICATED').order_by('-authenticated_at'),
            'rpage',
        )
        ctx['recent_released'] = results.filter(status__in=['RELEASED', 'AMENDED']).order_by('-released_at')[:15]
    else:
        recent_activity = SampleActivity.objects.select_related(
            'sample', 'performed_by', 'sample__order__test',
        )
        if not user.is_superuser:
            recent_activity = recent_activity.filter(sample__centre_id__in=centre_ids)
        ctx['recent_activity'] = recent_activity.order_by('-created_at')[:40]
        ctx['pending_results'] = results.filter(status__in=['DRAFT', 'SUBMITTED', 'REVIEWED', 'AUTHENTICATED']).order_by('-created_at')[:30]

    return ctx
