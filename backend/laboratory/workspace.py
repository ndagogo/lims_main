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
    ('batch', 'Batch Generation', 'Create and dispatch sample batches'),
    ('receive', 'Batch Receiving', 'Scan, accept, missing, or reject tubes at department receiving'),
    ('process', 'Department Receiving', 'Accept, hold, mark missing, or reject tubes arriving at the department'),
    ('entry', 'Result Entry', 'Result entry'),
    ('review', 'First Review', 'First review / validation'),
    ('auth', 'Authenticate', 'Result authentication'),
    ('release', 'Release', 'Controlled result release'),
    ('overview', 'Overview', 'Counts, TAT, QC, analyzers'),
]


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def _receive_desk_save(request):
    """Enterprise batch-receiving desk: accept / missing / reject selected tubes."""
    post = request.POST
    batch_id = (post.get('batch') or '').strip()
    if not batch_id:
        return False, 'Open a batch before saving.'
    accept = [v for v in post.getlist('accept') if v]
    missing = [v for v in post.getlist('missing') if v]
    reject = [v for v in post.getlist('reject') if v]
    hold = [v for v in post.getlist('hold') if v]
    if not accept and not missing and not reject and not hold:
        return False, 'Select at least one sample to accept, mark missing, reject, or hold.'
    remarks = {}
    for key in post.keys():
        if not key.startswith('remarks-'):
            continue
        sid = key[len('remarks-'):]
        remarks[sid] = (post.get(key) or '').strip()
    reject_lines = [
        {'sample_id': sid, 'reason': remarks.get(sid) or 'Rejected at department receiving'}
        for sid in reject
    ]
    payload = {
        'accept_sample_ids': accept,
        'missing_sample_ids': missing,
        'reject_lines': reject_lines,
        'hold_sample_ids': hold,
        'remarks': remarks,
        'receive_temperature': (post.get('receive_temperature') or '').strip(),
        'receive_comments': (post.get('receive_comments') or '').strip(),
    }
    status, body = call_action(SampleBatchViewSet, 'receive', request.user, batch_id, payload)
    if 200 <= status < 300:
        parts = []
        if accept:
            parts.append(f'{len(accept)} accepted')
        if missing:
            parts.append(f'{len(missing)} missing')
        if reject:
            parts.append(f'{len(reject)} rejected')
        if hold:
            parts.append(f'{len(hold)} held')
        return True, 'Department receiving saved: ' + ', '.join(parts) + '.'
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
        flag = (post.get(f'flag-{param_id}') or '').strip()
        if (post.get(f'alarm-{param_id}') or '').strip() == 'CRITICAL' and flag in ('', 'NORMAL'):
            flag = 'CRITICAL_HIGH'
        show = post.get(f'show-{param_id}')
        rows.append({
            'parameter': param_id,
            'value': ', '.join(parts),
            'comment': comment,
            'flag': flag,
            'show_on_report': show not in ('0', 'no', 'No', 'false'),
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
        payload = {'centre': post.get('centre'), 'sample_ids': post.getlist('sample_ids')}
        if (post.get('department') or '').strip():
            payload['department'] = post.get('department').strip()
        return _finish(*call_create(SampleBatchViewSet, user, payload), 'Batch created.')
    if action == 'dispatch':
        status, body = call_action(SampleBatchViewSet, 'dispatch_batch', user, post.get('batch'), {})
        if 200 <= status < 300 and isinstance(body, dict) and body.get('id'):
            from django.urls import reverse
            report_url = reverse('batch-report', kwargs={'pk': body['id']})
            return True, 'Batch dispatched. Opening printable batch report.', report_url
        return _finish(status, body, 'Batch dispatched.')
    if action == 'receive_desk':
        return _receive_desk_save(request)
    if action == 'receive':
        return _finish(*call_action(SampleBatchViewSet, 'receive', user, post.get('batch'), {}), 'Batch received by department.')
    if action == 'start_processing':
        return _finish(*call_action(SampleViewSet, 'start_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing started.')
    if action == 'complete_processing':
        return _finish(*call_action(SampleViewSet, 'complete_processing', user, post.get('sample'), {'notes': post.get('notes', '')}), 'Processing completed.')
    if action == 'enter_result':
        payload = {
            'narrative': post.get('narrative', ''),
            'remarks': post.get('remarks', ''),
            'instrument_name': post.get('instrument_name', ''),
            'values': _values(post, 'param-'),
        }
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
    default_centre = (request.POST.get('processing_centre') or '').strip()
    collected = []
    errors = []
    last_pk = None
    for order_id in ids:
        payload = {'note': note}
        if collector:
            payload['collected_by'] = collector
        centre = (request.POST.get(f'processing_centre-{order_id}') or default_centre).strip()
        if centre:
            payload['processing_centre'] = centre
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
        centre = (
            request.POST.get(f'processing_centre-{sample_id}')
            or request.POST.get('processing_centre')
            or ''
        ).strip()
        if centre:
            payload['processing_centre'] = centre
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
    # All active business centres with a sample digit can be processing destinations.
    processing_centres = list(
        Centre.objects.filter(is_active=True, sample_code_config__is_active=True)
        .select_related('sample_code_config')
        .order_by('name')
    )
    # Collection desk is scoped by registration centre. Users only see visits/samples for
    # centres they can access; multi-centre staff see every centre they are granted.
    if user.is_superuser:
        centres = list(Centre.objects.filter(is_active=True).order_by('name'))
    else:
        centre_ids = list(user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True))
        orders = orders.filter(visit__centre_id__in=centre_ids)
        samples = samples.filter(
            Q(centre_id__in=centre_ids) | Q(order__visit__centre_id__in=centre_ids)
        ).distinct()
        centres = list(Centre.objects.filter(pk__in=centre_ids, is_active=True).order_by('name'))
    if centre_filter:
        orders = orders.filter(visit__centre_id=centre_filter)
        samples = samples.filter(
            Q(centre_id=centre_filter) | Q(order__visit__centre_id=centre_filter)
        ).distinct()
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
        'processing_centres': processing_centres,
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
    from django.urls import reverse

    patient = visit.patient
    invoice = invoice_model.objects.filter(visit=visit).exclude(status='CANCELLED').order_by('created_at').first()
    referring = referring_name(visit) or (visit.client_name or '').strip()
    sex = patient.get_sex_display() if patient.sex else ''
    age = _age_text(patient)
    request_form = (
        visit.documents.filter(document_type__in=('REGISTRATION', 'REFERRAL'))
        .order_by('-created_at')
        .first()
    )
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
        'request_form': request_form,
        'request_form_url': (
            f"{reverse('patient-document-download', args=[request_form.pk])}?inline=1"
            if request_form else ''
        ),
        'request_form_label': (
            request_form.get_document_type_display() if request_form else ''
        ),
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


def _age_label(patient):
    dob = getattr(patient, 'date_of_birth', None)
    if not dob:
        return ''
    from django.utils import timezone
    today = timezone.localdate()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return f'{years} Yrs'


def _tat_label(sample):
    minutes = getattr(sample.order.test, 'tat_target_minutes', None)
    if not minutes:
        return '—'
    return f'{minutes // 60}:{minutes % 60:02d}'


def _entry_filters(request):
    return {
        'evid': (request.GET.get('evid') or '').strip(),
        'esid': (request.GET.get('esid') or '').strip(),
        'egroup': (request.GET.get('egroup') or '').strip(),
        'etest': (request.GET.get('etest') or '').strip(),
        'efrom': (request.GET.get('efrom') or '').strip(),
        'eto': (request.GET.get('eto') or '').strip(),
        'eloc': (request.GET.get('eloc') or '').strip(),
        'echannel': (request.GET.get('echannel') or '').strip(),
        'elist': (request.GET.get('elist') or 'completed').strip().lower(),
        'esel': (request.GET.get('esel') or '').strip(),
        'etemplate': (request.GET.get('etemplate') or '').strip(),
    }


def _build_entry_context(request, samples, centre_rows):
    """Result-entry workbench: filtered worklist plus the selected sample's parameter grid."""
    from django.db.models import Exists, OuterRef, Q
    from django.utils.dateparse import parse_date

    from integrations.models import Analyzer
    from laboratory.models import ResultTemplate, SampleProcessing, ServiceGroup
    from laboratory.templates_svc import entry_fields_for_sample
    from patients.models import CHANNEL_CHOICES
    from referrals.models import ReferralAttribution

    filters = _entry_filters(request)
    open_processing = SampleProcessing.objects.filter(sample_id=OuterRef('pk'), completed_at__isnull=True)
    queue = samples.filter(status__in=['PROCESSING', 'RESULT_ENTERED']).annotate(
        open_processing_exists=Exists(open_processing),
    ).filter(open_processing_exists=False)
    if filters['evid']:
        queue = queue.filter(order__visit__visit_id__icontains=filters['evid'])
    if filters['esid']:
        queue = queue.filter(sample_id__icontains=filters['esid'])
    if filters['egroup'].isdigit():
        queue = queue.filter(order__test__service_group_id=int(filters['egroup']))
    if filters['etest']:
        queue = queue.filter(order__test__name__icontains=filters['etest'])
    start = parse_date(filters['efrom'])
    end = parse_date(filters['eto'])
    if start:
        queue = queue.filter(collected_at__date__gte=start)
    if end:
        queue = queue.filter(collected_at__date__lte=end)
    if filters['eloc']:
        queue = queue.filter(centre_id=filters['eloc'])
    if filters['echannel']:
        queue = queue.filter(order__visit__referral_source=filters['echannel'])
    chip = filters['elist']
    if chip == 'stat':
        queue = queue.filter(order__stat__name__icontains='stat')
    elif chip == 'retest':
        queue = queue.filter(recollection_of__isnull=False)
    elif chip == 'outsourced':
        queue = queue.filter(Q(order__test__default_outsource=True) | Q(order__test__is_outsourceable=True))
    queue = queue.order_by('-updated_at')
    page = paginate(request, queue, 'spage')
    for sample in page.object_list:
        sample.tat_label = _tat_label(sample)
        sample.result_row = _result_of(sample)
    selected = None
    if filters['esel']:
        from django.core.exceptions import ValidationError
        try:
            selected = queue.filter(pk=filters['esel']).first()
        except (ValidationError, ValueError):
            selected = None
    if selected is None:
        selected = page.object_list[0] if page.object_list else None
    work = None
    if selected is not None:
        selected.result_row = _result_of(selected)
        selected.tat_label = _tat_label(selected)
        preferred = None
        if filters['etemplate'].isdigit():
            preferred = ResultTemplate.objects.filter(
                pk=int(filters['etemplate']), test=selected.order.test, is_active=True,
            ).first()
        template, fields = entry_fields_for_sample(selected, preferred)
        saved = {}
        if selected.result_row:
            for row in selected.result_row.values.select_related('parameter'):
                saved[row.parameter_id] = row
        for field in fields:
            row = saved.get(field['parameter'].pk)
            field['saved_value'] = row.value if row else ''
            field['saved_choices'] = [part.strip() for part in (row.value or '').split(',') if part.strip()] if row else []
            field['saved_flag'] = row.flag if row else ''
            field['saved_comment'] = row.comment if row else ''
            field['saved_show'] = row.show_on_report if row else field['include_in_report']
            field['alarm'] = 'CRITICAL' if (field['saved_flag'] or '').startswith('CRITICAL') else ''
        visit = selected.order.visit
        patient = visit.patient
        doctor = ''
        attribution = ReferralAttribution.objects.select_related('doctor').filter(visit=visit).first()
        if attribution and attribution.doctor_id:
            doctor = str(attribution.doctor)
        templates = list(
            ResultTemplate.objects.filter(test=selected.order.test, is_active=True)
            .exclude(status='RETIRED')
            .order_by('-version', 'name')
        )
        work = {
            'sample': selected,
            'template': template,
            'fields': fields,
            'templates': templates,
            'analyzers': list(Analyzer.objects.filter(centre=selected.centre, is_active=True).order_by('name')),
            'patient': {
                'uhid': patient.uhid,
                'name': f'{patient.first_name} {patient.last_name}'.strip(),
                'sex': patient.get_sex_display() if patient.sex else '',
                'age': _age_label(patient),
                'mobile': f'{patient.phone_country_code} {patient.phone_number}'.strip(),
                'visit_id': visit.visit_id,
                'visit_date': visit.created_at,
                'client': (visit.client.name if visit.client_id else visit.client_name) or '',
                'channel': visit.get_referral_source_display(),
                'centre': visit.centre.name,
                'doctor': doctor,
                'client_phone': (visit.client.phone if visit.client_id else '') or '',
            },
            'narrative': selected.result_row.narrative if selected.result_row else '',
            'remarks': selected.result_row.remarks if selected.result_row else '',
            'instrument_name': selected.result_row.instrument_name if selected.result_row else '',
        }
    return {
        'entry_samples': page,
        'entry_filters': filters,
        'entry_work': work,
        'entry_groups': ServiceGroup.objects.order_by('name'),
        'entry_centres': centre_rows,
        'entry_channels': CHANNEL_CHOICES,
        'entry_flags': [
            ('', '—'),
            ('NORMAL', 'N'),
            ('LOW', 'L'),
            ('HIGH', 'H'),
            ('CRITICAL_LOW', 'CL'),
            ('CRITICAL_HIGH', 'CH'),
        ],
    }


def build_laboratory_context(request):
    user = request.user
    stage = (request.GET.get('stage') or 'batch').strip().lower()
    if stage not in {key for key, *_ in LAB_STAGES}:
        stage = 'batch'
    centre_ids = user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
    samples = settled(
        Sample.objects.select_related(
            'centre', 'order__test', 'order__test__service_group', 'order__stat',
            'order__test__default_template', 'order__visit__patient', 'order__visit__centre', 'collected_by',
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
    centres = UserCentreAccess.objects.filter(user=user, can_edit=True, centre__is_active=True).select_related('centre')
    if user.is_superuser:
        from core.models import Centre
        centre_rows = list(Centre.objects.filter(is_active=True).order_by('name'))
    else:
        centre_rows = [row.centre for row in centres]
    batch_centre = (request.GET.get('centre') or '').strip()
    if batch_centre:
        collected = collected.filter(centre_id=batch_centre)
    elif centre_rows:
        batch_centre = str(centre_rows[0].pk)
        collected = collected.filter(centre_id=batch_centre)

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
        'batch_centre': batch_centre,
        'tat_summary': tat_dashboard_payload(user, scan=False),
        'qc_materials': qc_materials[:30],
        'recent_qc_runs': recent_qc,
        'analyzer_messages': paginate(request, messages.order_by('-received_at'), 'apage'),
    }

    if stage == 'batch':
        ctx.update(_build_batch_generation_context(request, collected, batches, centre_rows, batch_centre))
    elif stage == 'receive':
        ctx.update(_build_batch_receiving_context(request, batches))
    elif stage == 'process':
        ctx.update(_build_department_receiving_context(request, batches, samples))
    elif stage == 'entry':
        ctx.update(_build_entry_context(request, samples, centre_rows))
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


def _vacutainer_tone(name):
    text = (name or '').lower()
    if any(token in text for token in ('yellow', 'gold', 'sst', 'gel')):
        return 'yellow'
    if any(token in text for token in ('lavender', 'purple', 'edta')):
        return 'lavender'
    if any(token in text for token in ('blue', 'citrate')):
        return 'blue'
    if any(token in text for token in ('green', 'heparin')):
        return 'green'
    if any(token in text for token in ('red', 'plain', 'clot')):
        return 'red'
    if any(token in text for token in ('grey', 'gray', 'fluoride')):
        return 'grey'
    return ''


def _preview_batch_code(centre):
    from core.models import SequenceCounter
    if centre is None:
        return ''
    counter = SequenceCounter.objects.filter(key=f'batch-{centre.code}').first()
    next_value = (counter.value + 1) if counter else 1
    return f'{centre.code}-B{next_value:07d}'


def _build_batch_generation_context(request, collected, batches, centre_rows, batch_centre):
    """Desk context for Batch Generation — collected samples ready to batch at one centre."""
    from urllib.parse import urlencode

    from django.db.models import Q
    from django.utils import timezone
    from django.utils.dateparse import parse_date

    from laboratory.models import ServiceGroup

    query = (request.GET.get('q') or request.GET.get('sample_id') or '').strip()
    visit_id = (request.GET.get('vid') or '').strip()
    date_from = (request.GET.get('from') or '').strip()
    date_to = (request.GET.get('to') or '').strip()
    service_group = (request.GET.get('service_group') or '').strip()
    view = (request.GET.get('view') or '').strip().lower()
    if view not in ('', 'grid'):
        view = ''
    start = parse_date(date_from)
    end = parse_date(date_to)

    queue = collected.select_related(
        'centre',
        'order__test__service_group',
        'order__stat',
        'order__visit__patient',
        'order__visit__centre',
        'collected_by',
    )
    if query:
        queue = queue.filter(
            Q(sample_id__icontains=query)
            | Q(order__visit__visit_id__icontains=query)
            | Q(order__visit__patient__first_name__icontains=query)
            | Q(order__visit__patient__last_name__icontains=query)
            | Q(order__visit__patient__uhid__icontains=query)
            | Q(order__test__name__icontains=query)
            | Q(order__test__code__icontains=query)
        )
    if visit_id:
        queue = queue.filter(order__visit__visit_id__icontains=visit_id)
    if start:
        queue = queue.filter(collected_at__date__gte=start)
    if end:
        queue = queue.filter(collected_at__date__lte=end)
    if service_group:
        queue = queue.filter(
            Q(order__test__service_group__name__icontains=service_group)
            | Q(order__test__department__icontains=service_group)
        )

    selected_centre = None
    if batch_centre:
        selected_centre = next((c for c in centre_rows if str(c.pk) == str(batch_centre)), None)
    if selected_centre is None and len(centre_rows) == 1:
        selected_centre = centre_rows[0]
        batch_centre = str(selected_centre.pk)

    filters = {}
    if query:
        filters['q'] = query
    if visit_id:
        filters['vid'] = visit_id
    if date_from:
        filters['from'] = date_from
    if date_to:
        filters['to'] = date_to
    if service_group:
        filters['service_group'] = service_group
    if batch_centre:
        filters['centre'] = batch_centre
    filter_qs = urlencode(filters)
    clear_params = {'stage': 'batch'}
    grid_params = {'stage': 'batch', 'view': 'grid', **filters}

    pending_count = queue.count()
    collected_page = paginate(request, queue.order_by('-collected_at', 'sample_id'), 'cpage')
    desk_rows = []
    for sample in collected_page:
        order = sample.order
        _decorate_order(order)
        patient = order.visit.patient
        desk_rows.append({
            'sample': sample,
            'order': order,
            'patient_name': f'{patient.first_name} {patient.last_name}'.strip().upper(),
            'age': _age_text(patient),
            'sex': (patient.sex or '').strip(),
            'uhid': patient.uhid or '',
            'visit_id': order.visit.visit_id,
            'vacutainer': order.desk_vacutainer,
            'vac_tone': _vacutainer_tone(order.desk_vacutainer),
            'specimen': sample.specimen_type or order.desk_specimen or '',
            'group': order.desk_group or (order.test.department or ''),
            'collected_at': sample.collected_at,
            'is_recollection': bool(sample.recollection_of_id),
            'centre': sample.centre,
        })

    service_groups = list(
        ServiceGroup.objects.filter(is_active=True).order_by('name').values_list('name', flat=True)
    )
    open_batches = batches.filter(status='OPEN').order_by('-created_at')
    return {
        'batch_desk': True,
        'desk_rows': desk_rows,
        'collected_samples': collected_page,
        'recent_batches': paginate(request, batches.order_by('-created_at'), 'bpage'),
        'open_batches': paginate(request, open_batches, 'opage'),
        'pending_vac_count': pending_count,
        'batch_preview_code': _preview_batch_code(selected_centre),
        'batch_date': timezone.localdate(),
        'selected_centre': selected_centre,
        'batch_centre': batch_centre,
        'search_query': query,
        'visit_filter': visit_id,
        'date_from': date_from,
        'date_to': date_to,
        'service_group': service_group,
        'service_groups': service_groups,
        'desk_view': view,
        'filter_qs': filter_qs,
        'clear_href': '?' + urlencode(clear_params),
        'grid_href': '?' + urlencode(grid_params),
    }


def _build_batch_receiving_context(request, batches):
    from urllib.parse import urlencode

    from django.db.models import Count, Q
    from django.utils.dateparse import parse_date

    query = (request.GET.get('q') or '').strip()
    date_from = (request.GET.get('from') or '').strip()
    date_to = (request.GET.get('to') or '').strip()
    service_group = (request.GET.get('service_group') or '').strip()
    batch_key = (request.GET.get('batch') or request.GET.get('batch_code') or '').strip()
    view = (request.GET.get('view') or '').strip().lower()
    if view not in ('', 'grid'):
        view = ''
    start = parse_date(date_from)
    end = parse_date(date_to)

    queue = batches.filter(status__in=['DISPATCHED', 'RECEIVED', 'OPEN']).annotate(
        sample_count=Count('items', distinct=True),
    )
    if start:
        queue = queue.filter(Q(dispatched_at__date__gte=start) | Q(created_at__date__gte=start))
    if end:
        queue = queue.filter(Q(dispatched_at__date__lte=end) | Q(created_at__date__lte=end))
    if query:
        queue = queue.filter(
            Q(batch_code__icontains=query)
            | Q(department__icontains=query)
            | Q(centre__name__icontains=query)
            | Q(centre__code__icontains=query)
            | Q(items__sample__sample_id__icontains=query)
            | Q(items__sample__order__visit__patient__first_name__icontains=query)
            | Q(items__sample__order__visit__patient__last_name__icontains=query)
            | Q(items__sample__order__visit__patient__uhid__icontains=query)
        ).distinct()
    if service_group:
        queue = queue.filter(
            Q(items__sample__order__test__service_group__name__icontains=service_group)
            | Q(department__icontains=service_group)
        ).distinct()

    selected = None
    if batch_key:
        selected = queue.filter(Q(pk=batch_key) | Q(batch_code__iexact=batch_key)).first()
        if selected is None:
            selected = batches.filter(Q(pk=batch_key) | Q(batch_code__iexact=batch_key)).first()

    desk_rows = []
    pending_count = 0
    scanned_count = 0
    service_groups = sorted({
        name for name in batches.filter(status='DISPATCHED').values_list(
            'items__sample__order__test__service_group__name', flat=True,
        ) if name
    })

    if selected is not None:
        items = (
            selected.items.select_related(
                'sample__centre',
                'sample__order__test__service_group',
                'sample__order__stat',
                'sample__order__visit__patient',
                'sample__order__visit__centre',
            ).order_by('sample__sample_id')
        )
        for item in items:
            sample = item.sample
            order = sample.order
            _decorate_order(order)
            patient = order.visit.patient
            disposition = item.receive_disposition or 'PENDING'
            is_pending = sample.status == 'BATCHED' and disposition in ('PENDING', 'SCANNED', '')
            is_scanned = bool(item.scanned_at) or disposition in ('SCANNED', 'ACCEPTED')
            if is_pending:
                pending_count += 1
            if is_scanned or disposition == 'ACCEPTED':
                scanned_count += 1
            desk_rows.append({
                'item': item,
                'sample': sample,
                'order': order,
                'patient_name': f'{patient.first_name} {patient.last_name}'.strip(),
                'age': _age_text(patient),
                'sex': (patient.sex or '').strip(),
                'uhid': patient.uhid or '',
                'vacutainer': order.desk_vacutainer,
                'vac_tone': _vacutainer_tone(order.desk_vacutainer),
                'specimen': sample.specimen_type or order.desk_specimen or '',
                'group': order.desk_group or selected.department,
                'disposition': disposition,
                'editable': sample.status == 'BATCHED' and selected.status == 'DISPATCHED',
                'collected_at': sample.collected_at,
            })

    filters = {}
    if query:
        filters['q'] = query
    if date_from:
        filters['from'] = date_from
    if date_to:
        filters['to'] = date_to
    if service_group:
        filters['service_group'] = service_group
    filter_qs = urlencode(filters)
    clear_params = {'stage': 'receive', 'browse': '1', **filters}
    grid_params = {'stage': 'receive', 'view': 'grid', **filters}
    if selected is not None:
        grid_params['batch'] = str(selected.pk)

    pending_batches = queue.filter(status='DISPATCHED').order_by('-dispatched_at', '-created_at')
    return {
        'open_batches': paginate(request, queue.order_by('-created_at'), 'bpage'),
        'pending_batches': paginate(request, pending_batches, 'ppage'),
        'receive_desk': True,
        'selected_batch': selected,
        'desk_rows': desk_rows,
        'pending_vac_count': pending_count if selected else pending_batches.count(),
        'scanned_vac_count': scanned_count,
        'search_query': query,
        'date_from': date_from,
        'date_to': date_to,
        'service_group': service_group,
        'service_groups': service_groups,
        'desk_view': view,
        'filter_qs': filter_qs,
        'clear_href': '?' + urlencode(clear_params),
        'grid_href': '?' + urlencode(grid_params),
        'receive_editable': bool(selected and selected.status == 'DISPATCHED'),
    }


def _sex_label(value):
    text = (value or '').strip()
    folded = text.upper()
    if folded in ('M', 'MALE'):
        return 'Male'
    if folded in ('F', 'FEMALE'):
        return 'Female'
    return text


def _age_short(patient):
    text = _age_text(patient)
    if text.endswith(' Year') or text.endswith(' Years'):
        return f'{text.split()[0]} Y(s)'
    return text


def _build_department_receiving_context(request, batches, samples):
    """Department receiving desk: accept, hold, missing, or reject tubes on a dispatched batch."""
    from urllib.parse import urlencode

    from django.db.models import Count, Q
    from django.utils.dateparse import parse_date

    from billing.models import InvoiceItem
    from laboratory.models import ServiceGroup

    query = (request.GET.get('q') or request.GET.get('sample_id') or '').strip()
    date_from = (request.GET.get('from') or '').strip()
    date_to = (request.GET.get('to') or '').strip()
    service_group = (request.GET.get('service_group') or '').strip()
    batch_key = (request.GET.get('batch') or request.GET.get('batch_code') or '').strip()
    hold_bills = (request.GET.get('hold_bills') or '') in ('1', 'on', 'true')
    browse = (request.GET.get('browse') or '') == '1'
    view = (request.GET.get('view') or '').strip().lower()
    if view not in ('', 'grid'):
        view = ''
    start = parse_date(date_from)
    end = parse_date(date_to)

    queue = batches.filter(status__in=['DISPATCHED', 'RECEIVED']).annotate(
        sample_count=Count('items', distinct=True),
    )
    if start:
        queue = queue.filter(Q(dispatched_at__date__gte=start) | Q(created_at__date__gte=start))
    if end:
        queue = queue.filter(Q(dispatched_at__date__lte=end) | Q(created_at__date__lte=end))
    if service_group:
        queue = queue.filter(
            Q(items__sample__order__test__service_group__name__icontains=service_group)
            | Q(department__icontains=service_group)
        ).distinct()
    if query:
        queue = queue.filter(
            Q(batch_code__icontains=query)
            | Q(items__sample__sample_id__icontains=query)
            | Q(items__sample__order__visit__visit_id__icontains=query)
            | Q(items__sample__order__visit__patient__uhid__icontains=query)
            | Q(items__sample__order__visit__patient__first_name__icontains=query)
            | Q(items__sample__order__visit__patient__last_name__icontains=query)
        ).distinct()

    selected = None
    if batch_key:
        selected = queue.filter(Q(pk=batch_key) | Q(batch_code__iexact=batch_key)).first()
        if selected is None:
            selected = batches.filter(Q(pk=batch_key) | Q(batch_code__iexact=batch_key)).first()
    elif not query and not browse:
        selected = queue.filter(status='DISPATCHED').order_by('-dispatched_at', '-created_at').first()

    desk_rows = []
    pending_count = 0
    scanned_count = 0
    hold_count = 0
    if selected is not None:
        items = list(
            selected.items.select_related(
                'sample__centre',
                'sample__order__test__service_group',
                'sample__order__visit__patient',
                'sample__order__visit__centre',
                'sample__collected_by',
            ).prefetch_related('sample__processing_records').order_by('sample__sample_id')
        )
        order_ids = [item.sample.order_id for item in items]
        unpaid_orders = set(
            InvoiceItem.objects.filter(lab_order_id__in=order_ids)
            .exclude(invoice__status='PAID')
            .values_list('lab_order_id', flat=True)
        ) if order_ids else set()
        for item in items:
            sample = item.sample
            order = sample.order
            _decorate_order(order)
            patient = order.visit.patient
            disposition = item.receive_disposition or 'PENDING'
            bill_held = order.pk in unpaid_orders
            is_pending = sample.status == 'BATCHED' and disposition in ('PENDING', 'SCANNED', '')
            is_scanned = bool(item.scanned_at) or disposition in ('SCANNED', 'ACCEPTED')
            if disposition == 'HOLD' or (hold_bills and bill_held and sample.status == 'BATCHED'):
                hold_count += 1
            elif is_pending:
                pending_count += 1
            if is_scanned or disposition == 'ACCEPTED':
                scanned_count += 1
            open_processing = any(row.completed_at is None for row in sample.processing_records.all())
            receiver = selected.received_by
            desk_rows.append({
                'item': item,
                'sample': sample,
                'order': order,
                'patient_name': f'{patient.first_name} {patient.last_name}'.strip().upper(),
                'age': _age_short(patient),
                'sex': _sex_label(patient.sex),
                'uhid': patient.uhid or '',
                'visit_id': order.visit.visit_id,
                'vacutainer': order.desk_vacutainer,
                'vac_tone': _vacutainer_tone(order.desk_vacutainer),
                'specimen': sample.specimen_type or order.desk_specimen or '',
                'group': order.desk_group or selected.department or '',
                'disposition': disposition,
                'bill_held': bill_held,
                'editable': sample.status == 'BATCHED' and selected.status == 'DISPATCHED' and not (hold_bills and bill_held),
                'accept_locked': bool(hold_bills and bill_held and sample.status == 'BATCHED'),
                'collected_at': sample.collected_at,
                'open_processing': open_processing,
                'can_start': sample.status == 'RECEIVED',
                'can_complete': sample.status == 'PROCESSING' and open_processing,
                'received_by': (getattr(receiver, 'staff_id', '') or getattr(receiver, 'username', '') or '') if receiver else '',
                'received_on': selected.received_at,
                'location': sample.centre.name if sample.centre_id else selected.centre.name,
            })

    filters = {'stage': 'process'}
    if query:
        filters['q'] = query
    if date_from:
        filters['from'] = date_from
    if date_to:
        filters['to'] = date_to
    if service_group:
        filters['service_group'] = service_group
    if hold_bills:
        filters['hold_bills'] = '1'
    if selected is not None:
        filters['batch'] = str(selected.pk)
    clear_href = '?stage=process&browse=1'
    grid_filters = dict(filters)
    grid_filters['view'] = '' if view == 'grid' else 'grid'
    service_groups = list(ServiceGroup.objects.filter(is_active=True).order_by('name').values_list('name', flat=True))
    pending_batches = queue.filter(status='DISPATCHED').order_by('-dispatched_at', '-created_at')
    return {
        'dept_selected_batch': selected,
        'dept_rows': desk_rows,
        'dept_pending_count': pending_count if selected else pending_batches.count(),
        'dept_scanned_count': scanned_count,
        'dept_hold_count': hold_count,
        'dept_hold_bills': hold_bills,
        'dept_query': query,
        'dept_date_from': date_from,
        'dept_date_to': date_to,
        'dept_service_group': service_group,
        'dept_service_groups': service_groups,
        'dept_view': view,
        'dept_clear_href': clear_href,
        'dept_grid_href': '?' + urlencode(grid_filters),
        'dept_editable': bool(selected and selected.status == 'DISPATCHED'),
        'dept_pending_batches': paginate(request, pending_batches, 'ppage'),
        'dept_filter_qs': urlencode({k: v for k, v in filters.items() if k not in ('stage', 'batch')}),
    }
