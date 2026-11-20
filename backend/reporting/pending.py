"""Cross-department pending / outstanding patient results for business owners."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from laboratory.models import LabResult, Sample
from laboratory.tat import OPEN_STATUSES
from radiology.models import ImagingOrder, RadiologyReport
from reporting.metrics import centre_scope

HEADERS = [
    'Department', 'Centre', 'Visit ID', 'Patient', 'UHID', 'Item', 'Sample / Study',
    'Status', 'Updated', 'Age (hours)',
]

DEPARTMENT_CHOICES = [
    ('', 'All departments'),
    ('LABORATORY', 'Laboratory'),
    ('RADIOLOGY', 'Radiology'),
]

LAB_PENDING_STATUSES = ('DRAFT', 'SUBMITTED', 'REVIEWED', 'AUTHENTICATED')
RAD_PENDING_REPORT_STATUSES = ('DRAFT', 'FINAL', 'APPROVED')


def _age_hours(when, now):
    if not when:
        return ''
    seconds = max(0, (now - when).total_seconds())
    return round(seconds / 3600, 1)


def _patient_name(patient):
    if not patient:
        return ''
    return f'{patient.first_name} {patient.last_name}'.strip()


def build_pending_results(
    user,
    *,
    department='',
    centre_id=None,
    query='',
    status='',
    date_from='',
    date_to='',
    min_age_hours=None,
):
    """
    Return filter context + unified pending result rows for Laboratory and Radiology.
    Outstanding = not yet released/dispatched to the patient.
    """
    centres, centre_ids, selected = centre_scope(user, centre_id)
    now = timezone.now()
    department = (department or '').upper()
    if department not in ('', 'LABORATORY', 'RADIOLOGY'):
        department = ''
    query = (query or '').strip()
    status = (status or '').strip().upper()
    start = parse_date(date_from or '') if date_from else None
    end = parse_date(date_to or '') if date_to else None
    try:
        min_age = float(min_age_hours) if min_age_hours not in (None, '') else None
    except (TypeError, ValueError):
        min_age = None

    rows = []
    if department in ('', 'LABORATORY'):
        rows.extend(_lab_rows(centre_ids, query, status, start, end, now))
    if department in ('', 'RADIOLOGY'):
        rows.extend(_radiology_rows(centre_ids, query, status, start, end, now))

    if min_age is not None:
        rows = [r for r in rows if isinstance(r.get('age_hours'), (int, float)) and r['age_hours'] >= min_age]

    rows.sort(key=lambda r: (r.get('updated') or now), reverse=True)

    status_options = sorted({
        *LAB_PENDING_STATUSES,
        *RAD_PENDING_REPORT_STATUSES,
        *OPEN_STATUSES,
        'ORDERED', 'SCHEDULED', 'IN_PROGRESS', 'ASSIGNED', 'ACQUIRED',
    })
    summary = {
        'total': len(rows),
        'laboratory': sum(1 for r in rows if r['department'] == 'Laboratory'),
        'radiology': sum(1 for r in rows if r['department'] == 'Radiology'),
    }
    table_rows = [
        [
            r['department'], r['centre'], r['visit_id'], r['patient'], r['uhid'],
            r['item'], r['ref'], r['status'], r['updated_display'], r['age_hours'],
        ]
        for r in rows
    ]
    return {
        'rows': rows,
        'table_rows': table_rows,
        'headers': HEADERS,
        'summary': summary,
        'centres': centres,
        'selected_centre': selected,
        'centre_id': str(selected.pk) if selected else (str(centre_id) if centre_id else ''),
        'department': department,
        'department_choices': DEPARTMENT_CHOICES,
        'query': query,
        'status': status,
        'status_options': status_options,
        'date_from': start.isoformat() if start else '',
        'date_to': end.isoformat() if end else '',
        'min_age_hours': '' if min_age is None else str(min_age),
        'generated_at': now,
    }


def _lab_rows(centre_ids, query, status, start, end, now):
    rows = []
    results = (
        LabResult.objects.filter(status__in=LAB_PENDING_STATUSES)
        .select_related(
            'sample__centre', 'sample__order__visit__patient', 'sample__order__test',
        )
        .order_by('-updated_at')
    )
    if centre_ids is not None:
        results = results.filter(sample__centre_id__in=centre_ids)
    if start:
        results = results.filter(updated_at__date__gte=start)
    if end:
        results = results.filter(updated_at__date__lte=end)
    if status:
        results = results.filter(status=status)
    if query:
        results = results.filter(
            Q(sample__sample_id__icontains=query)
            | Q(sample__order__visit__visit_id__icontains=query)
            | Q(sample__order__visit__patient__first_name__icontains=query)
            | Q(sample__order__visit__patient__last_name__icontains=query)
            | Q(sample__order__visit__patient__uhid__icontains=query)
            | Q(sample__order__test__name__icontains=query)
        )

    seen_sample_ids = set()
    for result in results[:2000]:
        sample = result.sample
        seen_sample_ids.add(sample.pk)
        visit = sample.order.visit
        patient = visit.patient
        updated = result.updated_at or result.entered_at
        rows.append({
            'department': 'Laboratory',
            'centre': sample.centre.code,
            'visit_id': visit.visit_id,
            'patient': _patient_name(patient),
            'uhid': getattr(patient, 'uhid', '') or '',
            'item': sample.order.test.name if sample.order.test_id else '',
            'ref': sample.sample_id,
            'status': result.get_status_display(),
            'status_code': result.status,
            'updated': updated,
            'updated_display': timezone.localtime(updated).strftime('%Y-%m-%d %H:%M') if updated else '',
            'age_hours': _age_hours(updated, now),
            'workspace_url': f'/laboratory/?sample={sample.pk}',
        })

    # Open samples still waiting for a result record.
    samples = (
        Sample.objects.filter(status__in=OPEN_STATUSES)
        .filter(Q(result__isnull=True))
        .select_related('centre', 'order__visit__patient', 'order__test')
        .order_by('-updated_at')
    )
    if centre_ids is not None:
        samples = samples.filter(centre_id__in=centre_ids)
    if start:
        samples = samples.filter(updated_at__date__gte=start)
    if end:
        samples = samples.filter(updated_at__date__lte=end)
    if status and status not in dict(Sample.STATUS_CHOICES):
        samples = samples.none()
    elif status:
        samples = samples.filter(status=status)
    if query:
        samples = samples.filter(
            Q(sample_id__icontains=query)
            | Q(order__visit__visit_id__icontains=query)
            | Q(order__visit__patient__first_name__icontains=query)
            | Q(order__visit__patient__last_name__icontains=query)
            | Q(order__visit__patient__uhid__icontains=query)
            | Q(order__test__name__icontains=query)
        )
    for sample in samples[:2000]:
        if sample.pk in seen_sample_ids:
            continue
        visit = sample.order.visit
        patient = visit.patient
        updated = sample.updated_at or sample.created_at
        rows.append({
            'department': 'Laboratory',
            'centre': sample.centre.code,
            'visit_id': visit.visit_id,
            'patient': _patient_name(patient),
            'uhid': getattr(patient, 'uhid', '') or '',
            'item': sample.order.test.name if sample.order.test_id else '',
            'ref': sample.sample_id,
            'status': sample.get_status_display(),
            'status_code': sample.status,
            'updated': updated,
            'updated_display': timezone.localtime(updated).strftime('%Y-%m-%d %H:%M') if updated else '',
            'age_hours': _age_hours(updated, now),
            'workspace_url': f'/laboratory/?sample={sample.pk}',
        })
    return rows


def _radiology_rows(centre_ids, query, status, start, end, now):
    rows = []
    # Studies with a report not yet dispatched.
    reports = (
        RadiologyReport.objects.filter(status__in=RAD_PENDING_REPORT_STATUSES)
        .select_related(
            'exam__order__visit__patient', 'exam__order__visit__centre',
            'exam__order__modality', 'exam__order__procedure',
        )
        .order_by('-updated_at')
    )
    if centre_ids is not None:
        reports = reports.filter(exam__order__visit__centre_id__in=centre_ids)
    if start:
        reports = reports.filter(updated_at__date__gte=start)
    if end:
        reports = reports.filter(updated_at__date__lte=end)
    if status:
        reports = reports.filter(status=status)
    if query:
        reports = reports.filter(
            Q(exam__order__visit__visit_id__icontains=query)
            | Q(exam__order__visit__patient__first_name__icontains=query)
            | Q(exam__order__visit__patient__last_name__icontains=query)
            | Q(exam__order__visit__patient__uhid__icontains=query)
            | Q(exam__order__procedure__name__icontains=query)
            | Q(exam__order__modality__name__icontains=query)
            | Q(exam__order__modality__code__icontains=query)
        )

    seen_order_ids = set()
    for report in reports[:2000]:
        order = report.exam.order
        seen_order_ids.add(order.pk)
        visit = order.visit
        patient = visit.patient
        updated = report.updated_at or report.created_at
        item = order.procedure.name if order.procedure_id else (order.modality.name if order.modality_id else 'Imaging')
        rows.append({
            'department': 'Radiology',
            'centre': visit.centre.code,
            'visit_id': visit.visit_id,
            'patient': _patient_name(patient),
            'uhid': getattr(patient, 'uhid', '') or '',
            'item': item,
            'ref': order.modality.code if order.modality_id else str(order.pk)[:8],
            'status': report.get_status_display(),
            'status_code': report.status,
            'updated': updated,
            'updated_display': timezone.localtime(updated).strftime('%Y-%m-%d %H:%M') if updated else '',
            'age_hours': _age_hours(updated, now),
            'workspace_url': f'/radiology/?study={order.pk}',
        })

    # Orders still in acquisition / without a report (not cancelled).
    orders = (
        ImagingOrder.objects.exclude(status='CANCELLED')
        .filter(Q(exam__isnull=True) | Q(exam__report__isnull=True))
        .select_related('visit__patient', 'visit__centre', 'modality', 'procedure', 'exam')
        .order_by('-updated_at')
    )
    if centre_ids is not None:
        orders = orders.filter(visit__centre_id__in=centre_ids)
    if start:
        orders = orders.filter(updated_at__date__gte=start)
    if end:
        orders = orders.filter(updated_at__date__lte=end)
    if status:
        # Match exam status or order status labels used in filters.
        orders = orders.filter(Q(status=status) | Q(exam__status=status))
    if query:
        orders = orders.filter(
            Q(visit__visit_id__icontains=query)
            | Q(visit__patient__first_name__icontains=query)
            | Q(visit__patient__last_name__icontains=query)
            | Q(visit__patient__uhid__icontains=query)
            | Q(procedure__name__icontains=query)
            | Q(modality__name__icontains=query)
            | Q(modality__code__icontains=query)
        )
    for order in orders[:2000]:
        if order.pk in seen_order_ids:
            continue
        visit = order.visit
        patient = visit.patient
        updated = order.updated_at or order.created_at
        try:
            exam = order.exam
        except ObjectDoesNotExist:
            exam = None
        if exam is not None:
            updated = exam.updated_at or updated
            status_code = exam.status
            status_label = exam.get_status_display()
        else:
            status_code = order.status
            status_label = order.get_status_display()
        item = order.procedure.name if order.procedure_id else (order.modality.name if order.modality_id else 'Imaging')
        rows.append({
            'department': 'Radiology',
            'centre': visit.centre.code,
            'visit_id': visit.visit_id,
            'patient': _patient_name(patient),
            'uhid': getattr(patient, 'uhid', '') or '',
            'item': item,
            'ref': order.modality.code if order.modality_id else str(order.pk)[:8],
            'status': status_label,
            'status_code': status_code,
            'updated': updated,
            'updated_display': timezone.localtime(updated).strftime('%Y-%m-%d %H:%M') if updated else '',
            'age_hours': _age_hours(updated, now),
            'workspace_url': f'/radiology/?study={order.pk}',
        })
    return rows
