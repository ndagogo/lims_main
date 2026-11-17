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
    if action == 'collect_selected':
        return _collect_selected(request)
    if action == 'recollect_selected':
        return _recollect_selected(request)
    if action not in ('collect', 'recollect'):
        return False, 'Phlebotomy can collect a sample or record a recollection.'
    return handle_laboratory_post(request)


def _collect_selected(request):
    ids = [value for value in request.POST.getlist('order') if value]
    if not ids:
        return False, 'Select at least one test to collect.'
    note = (request.POST.get('comments') or request.POST.get('note') or '').strip()
    collector = (request.POST.get('phlebotomist') or '').strip()
    collected = []
    errors = []
    last_pk = None
    for order_id in ids:
        payload = {'note': note}
        if collector:
            payload['collected_by'] = collector
        status, body = call_action(LabOrderViewSet, 'collect', request.user, order_id, payload)
        if 200 <= status < 300:
            collected.append((body or {}).get('sample_id') or '')
            last_pk = (body or {}).get('id')
        else:
            errors.append(describe(body))
    if collected:
        _save_visit_comments(request.user, request.POST.get('visit'), note)
    if not collected:
        return False, errors[0] if errors else 'Nothing was collected.'
    labels = ', '.join(sample_id for sample_id in collected if sample_id)
    if len(collected) == 1 and not errors:
        return True, f'Sample {labels} collected. Unique barcode ready to print.', last_pk
    summary = f'Collected {len(collected)} samples ({labels}). Open each label from collection history for barcode generation.'
    if errors:
        summary = f'{summary} Some tests were not collected: {errors[0]}'
    return True, summary


def _recollect_selected(request):
    ids = [value for value in request.POST.getlist('sample') if value]
    if not ids:
        return False, 'Select at least one sample to recollect.'
    collector = (request.POST.get('phlebotomist') or '').strip()
    collected = []
    errors = []
    last_pk = None
    for sample_id in ids:
        reason = (request.POST.get(f'reason-{sample_id}') or '').strip()
        if not reason:
            errors.append('Enter a recollection reason for every selected sample.')
            continue
        payload = {
            'reason': reason,
            'disposition': (request.POST.get(f'disposition-{sample_id}') or 'SUPERSEDED').strip(),
            'corrective_action': (request.POST.get('corrective_action') or '').strip(),
        }
        if collector:
            payload['collected_by'] = collector
        status, body = call_action(SampleViewSet, 'recollect', request.user, sample_id, payload)
        if 200 <= status < 300:
            collected.append((body or {}).get('sample_id') or '')
            last_pk = (body or {}).get('id')
        else:
            errors.append(describe(body))
    if collected:
        _save_visit_comments(request.user, request.POST.get('visit'), (request.POST.get('comments') or '').strip())
    if not collected:
        return False, errors[0] if errors else 'Nothing was recollected.'
    labels = ', '.join(sample_id for sample_id in collected if sample_id)
    if len(collected) == 1 and not errors:
        return True, f'Recollection {labels} is ready. The original sample was superseded and a new barcode is ready to print.', last_pk
    summary = f'Recollected {len(collected)} samples ({labels}). Print each new barcode from collection history.'
    if errors:
        summary = f'{summary} Some samples were not recollected: {errors[0]}'
    return True, summary


def _save_visit_comments(user, visit_pk, comments):
    if not visit_pk:
        return
    from patients.models import Visit
    visit = Visit.objects.filter(pk=visit_pk).first()
    if visit is None:
        return
    if not user.is_superuser and not user.centre_access.filter(centre=visit.centre, can_edit=True).exists():
        return
    text = (comments or '').strip()
    if text == (visit.comments or ''):
        return
    visit.comments = text
    visit.save(update_fields=['comments', 'updated_at'])


def build_phlebotomy_context(request):
    from urllib.parse import urlencode

    from django.db.models import Q
    from django.utils.dateparse import parse_date

    from billing.models import Invoice
    from core.models import Centre
    from referrals.services import report_referring_name

    user = request.user
    queue = (request.GET.get('queue') or 'pending').strip().lower()
    if queue not in ('pending', 'recollect'):
        queue = 'pending'
    view = (request.GET.get('view') or '').strip().lower()
    if view not in ('', 'history'):
        view = ''
    query = (request.GET.get('q') or '').strip()
    date_from = (request.GET.get('from') or '').strip()
    date_to = (request.GET.get('to') or '').strip()
    start = parse_date(date_from)
    end = parse_date(date_to)
    centre_filter = (request.GET.get('centre') or '').strip()
    browse = (request.GET.get('browse') or '').strip() == '1'
    visit_key = (request.GET.get('visit') or '').strip()

    orders = settled(
        LabOrder.objects.select_related(
            'visit__patient', 'visit__centre', 'visit__client', 'test__service_group', 'stat',
        ).filter(status__in=['ORDERED', 'COLLECTION_PENDING']),
        'lab_order_id',
    )
    samples = settled(
        Sample.objects.select_related(
            'centre', 'order__test__service_group', 'order__stat', 'order__visit__patient',
            'order__visit__centre', 'order__visit__client', 'collected_by',
        ),
        'lab_order_id', 'order_id',
    )
    if user.is_superuser:
        centres = list(Centre.objects.filter(is_active=True).order_by('name'))
    else:
        centre_ids = list(user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True))
        orders = orders.filter(visit__centre_id__in=centre_ids)
        samples = samples.filter(centre_id__in=centre_ids)
        centres = list(Centre.objects.filter(pk__in=centre_ids, is_active=True).order_by('name'))
    if centre_filter:
        orders = orders.filter(visit__centre_id=centre_filter)
        samples = samples.filter(centre_id=centre_filter)
    if query:
        orders = orders.filter(_visit_search(query, 'visit__') | Q(test__name__icontains=query))
        samples = samples.filter(
            _visit_search(query, 'order__visit__')
            | Q(order__test__name__icontains=query)
            | Q(sample_id__icontains=query)
        )
    if start:
        orders = orders.filter(visit__created_at__date__gte=start)
    if end:
        orders = orders.filter(visit__created_at__date__lte=end)

    pending = orders.order_by('-created_at')
    recollect_pool = samples.filter(
        Q(status='REJECTED') | Q(disposition__in=['REJECTED', 'UNSUITABLE']),
    ).exclude(status__in=['RELEASED', 'CANCELLED', 'SUPERSEDED']).filter(recollections__isnull=True)
    if start:
        recollect_pool = recollect_pool.filter(created_at__date__gte=start)
    if end:
        recollect_pool = recollect_pool.filter(created_at__date__lte=end)
    recollect_pool = recollect_pool.distinct().order_by('-created_at')

    collected = samples.filter(status='COLLECTED').order_by('-collected_at')
    collected_ever = samples.filter(collected_at__isnull=False)
    history = _collection_history(request, collected_ever)

    selected = None if browse else _selected_visit(user, visit_key, pending, recollect_pool, queue)
    desk_rows = []
    identity = None
    phlebotomists = []
    if selected is not None:
        phlebotomists = _phlebotomists(selected.centre, user)
        if queue == 'recollect':
            for sample in recollect_pool.filter(order__visit=selected):
                _decorate_order(sample.order)
                desk_rows.append({'kind': 'sample', 'order': sample.order, 'sample': sample, 'reason': sample.rejection_reason})
        else:
            for order in pending.filter(visit=selected):
                _decorate_order(order)
                desk_rows.append({'kind': 'order', 'order': order, 'sample': None, 'reason': ''})
        identity = _identity(selected, report_referring_name, Invoice)

    filters = {}
    if query:
        filters['q'] = query
    if date_from:
        filters['from'] = date_from
    if date_to:
        filters['to'] = date_to
    if centre_filter:
        filters['centre'] = centre_filter
    filter_qs = urlencode(filters)
    desk_centre = identity['centre'] if identity else None
    if desk_centre is None and centre_filter:
        desk_centre = next((centre for centre in centres if str(centre.pk) == centre_filter), None)
    if desk_centre is None and len(centres) == 1:
        desk_centre = centres[0]

    pending_page = paginate(request, pending, 'opage')
    for order in pending_page:
        _decorate_order(order)
    recollect_page = paginate(request, recollect_pool, 'rpage')
    for sample in recollect_page:
        _decorate_order(sample.order)

    return {
        'queue': queue,
        'desk_view': view,
        'browse': browse,
        'search_query': query,
        'date_from': date_from,
        'date_to': date_to,
        'centre_filter': centre_filter,
        'centres': centres,
        'desk_centre': desk_centre,
        'filter_qs': filter_qs,
        'pending_href': '?' + urlencode({**filters, 'queue': 'pending'}),
        'recollect_href': '?' + urlencode({**filters, 'queue': 'recollect'}),
        'history_href': '?' + urlencode({**filters, 'queue': queue, 'view': 'history', **({'visit': str(selected.pk)} if selected else {})}),
        'clear_href': '?' + urlencode({**filters, 'queue': queue, 'browse': '1'}),
        'pending_orders': pending_page,
        'recollect_samples': recollect_page,
        'collected_samples': paginate(request, collected, 'cpage'),
        'collection_history': paginate(request, history, 'hpage'),
        'pending_count': pending.count(),
        'recollect_count': recollect_pool.count(),
        'collected_count': collected.count(),
        'history_count': collected_ever.count(),
        'history_query': (request.GET.get('hq') or '').strip(),
        'history_from': (request.GET.get('hfrom') or '').strip(),
        'history_to': (request.GET.get('hto') or '').strip(),
        'selected_visit': selected,
        'identity': identity,
        'desk_rows': desk_rows,
        'phlebotomists': phlebotomists,
    }


def _visit_search(text, prefix):
    from django.db.models import Q
    return (
        Q(**{f'{prefix}visit_id__icontains': text})
        | Q(**{f'{prefix}patient__uhid__icontains': text})
        | Q(**{f'{prefix}patient__first_name__icontains': text})
        | Q(**{f'{prefix}patient__last_name__icontains': text})
        | Q(**{f'{prefix}patient__other_names__icontains': text})
        | Q(**{f'{prefix}patient__phone_number__icontains': text})
    )


def _selected_visit(user, visit_key, pending, recollect_pool, queue):
    from patients.models import Visit

    visit = None
    if visit_key:
        visit = Visit.objects.select_related('patient', 'centre', 'client').filter(pk=visit_key).first()
        if visit is None:
            visit = Visit.objects.select_related('patient', 'centre', 'client').filter(visit_id=visit_key).first()
    elif queue == 'recollect':
        sample = recollect_pool.select_related('order__visit__patient', 'order__visit__centre', 'order__visit__client').first()
        visit = sample.order.visit if sample else None
    else:
        order = pending.select_related('visit__patient', 'visit__centre', 'visit__client').first()
        visit = order.visit if order else None
    if visit is None:
        return None
    if user.is_superuser or user.centre_access.filter(centre=visit.centre, can_view=True, centre__is_active=True).exists():
        return visit
    return None


def _phlebotomists(centre, user):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    rows = list(
        get_user_model().objects.filter(is_active=True).filter(
            Q(groups__name='Phlebotomist') | Q(pk=user.pk),
        ).filter(
            Q(is_superuser=True) | Q(centre_access__centre=centre, centre_access__can_view=True) | Q(pk=user.pk),
        ).distinct().order_by('first_name', 'last_name', 'username')
    )
    if user.is_authenticated and user not in rows:
        rows.insert(0, user)
    return rows


def _identity(visit, referring_name, invoice_model):
    patient = visit.patient
    invoice = invoice_model.objects.filter(visit=visit).exclude(status='CANCELLED').order_by('created_at').first()
    referring = referring_name(visit) or (visit.client_name or '').strip()
    sex = patient.get_sex_display() if patient.sex else ''
    age = _age_text(patient)
    return {
        'visit': visit,
        'patient': patient,
        'name': _patient_name(patient),
        'gender_age': ' / '.join(part for part in (sex, age) if part),
        'mobile': _mobile(patient),
        'referring': referring,
        'channel': visit.get_referral_source_display(),
        'bill_date': invoice.created_at if invoice else visit.created_at,
        'centre': visit.centre,
    }


def _patient_name(patient):
    return ' '.join(part for part in (patient.title, patient.first_name, patient.other_names, patient.last_name) if (part or '').strip())


def _mobile(patient):
    code = (patient.phone_country_code or '').strip()
    number = (patient.phone_number or '').strip()
    if code and number:
        return f'{code}{number}' if code.startswith('+') else f'+{code}{number}'
    return number or code


def _age_text(patient):
    from decimal import Decimal

    from django.utils import timezone

    from laboratory.services import age_years

    years = age_years(patient)
    if years is None:
        return ''
    whole = int(years)
    if whole >= 1:
        unit = 'Year' if whole == 1 else 'Years'
        return f'{whole} {unit}'
    months = int((years * Decimal('12')).to_integral_value())
    if months >= 1:
        unit = 'Month' if months == 1 else 'Months'
        return f'{months} {unit}'
    days = max((timezone.localdate() - patient.date_of_birth).days, 0)
    unit = 'Day' if days == 1 else 'Days'
    return f'{days} {unit}'


def _decorate_order(order):
    test = order.test
    name = (test.name or '').lower()
    order.desk_group = test.service_group.name if getattr(test, 'service_group_id', None) else ''
    order.desk_specimen = (test.specimen or '').strip()
    order.desk_vacutainer = (test.container or '').strip()
    order.desk_prereq = (test.collection_requirements or '').strip()
    stat = order.stat
    if not stat or stat.is_default or (stat.name or '').strip().lower() in {'normal', 'routine', 'regular'}:
        order.desk_routine = 'Routine'
    else:
        order.desk_routine = stat.name
    order.desk_history = (order.visit.clinical_history or '').strip()
    order.desk_bt = 'Yes' if any(token in name for token in ('bleeding time', 'clotting time', 'bt/ct', 'prothrombin', 'aptt')) else ''
    order.desk_mantoux = 'Yes' if 'mantoux' in name or 'tuberculin' in name else ''
    if any(token in name for token in ('histo', 'biopsy', 'gross', 'cytolog')):
        order.desk_gross = (test.handling_instructions or '').strip()
    else:
        order.desk_gross = ''


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
            'centre', 'order__test', 'order__stat', 'order__test__default_template', 'order__visit__patient', 'collected_by',
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
            'sample__centre', 'sample__order__test', 'sample__order__stat', 'sample__order__visit__patient',
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

    from core.result_control import user_can_open_dispatch, user_can_open_modify
    ctx = {
        'can_modify_result': user_can_open_modify(user),
        'can_dispatch': user_can_open_dispatch(user),
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
