"""Accountant financial reports: period aggregates and CSV / Excel / PDF export."""
import csv
import io
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone

from billing.models import DiscountRequest, Invoice, Payment
from core.models import Centre
from patients.models import Visit

PERIOD_CHOICES = (
    ('daily', 'Today'),
    ('weekly', 'This week'),
    ('monthly', 'This month'),
    ('annual', 'This year'),
    ('custom', 'Custom range'),
)
DATASETS = (
    ('summary', 'Executive summary'),
    ('payments', 'Payments / collections'),
    ('invoices', 'Invoices'),
    ('discounts', 'Discounts'),
    ('visits', 'Visits'),
)


def resolve_period(period, start_raw=None, end_raw=None):
    """Return inclusive local-date window and a label for the selected period."""
    now = timezone.localtime()
    today = now.date()
    period = (period or 'daily').strip().lower()
    if period not in dict(PERIOD_CHOICES):
        period = 'daily'
    if period == 'custom':
        start_date = _parse_date(start_raw) or today
        end_date = _parse_date(end_raw) or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        label = f'{start_date.isoformat()} to {end_date.isoformat()}'
    elif period == 'weekly':
        start_date = today - timedelta(days=today.weekday())
        end_date = today
        label = f'Week of {start_date.isoformat()}'
    elif period == 'monthly':
        start_date = today.replace(day=1)
        end_date = today
        label = start_date.strftime('%B %Y')
    elif period == 'annual':
        start_date = today.replace(month=1, day=1)
        end_date = today
        label = start_date.strftime('%Y')
    else:
        start_date = today
        end_date = today
        label = today.isoformat()
        period = 'daily'
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))
    return period, start_dt, end_dt, label


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _money(value):
    return Decimal(value or 0).quantize(Decimal('0.01'))


def _centre_scope(user, centre_id=None):
    centres = Centre.objects.filter(is_active=True).order_by('name')
    if not user.is_superuser:
        centres = centres.filter(user_access__user=user, user_access__can_view=True).distinct()
    centre_ids = list(centres.values_list('pk', flat=True))
    selected = None
    if centre_id:
        selected = centres.filter(pk=centre_id).first()
        if selected:
            centre_ids = [selected.pk]
    return centres, centre_ids, selected


def build_accountant_report(user, period='daily', centre_id=None, start=None, end=None):
    period, start_dt, end_dt, label = resolve_period(period, start, end)
    centres, centre_ids, selected_centre = _centre_scope(user, centre_id)

    payments = Payment.objects.filter(
        status='CONFIRMED', created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('invoice__visit__patient', 'invoice__visit__centre', 'received_by')
    invoices = Invoice.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('visit__patient', 'visit__centre').prefetch_related('payments')
    discounts = DiscountRequest.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('invoice__visit__patient', 'invoice__visit__centre', 'requested_by', 'reviewed_by')
    visits = Visit.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('patient', 'centre')
    outstanding = Invoice.objects.filter(status__in=['UNPAID', 'PART_PAID']).select_related(
        'visit__patient', 'visit__centre',
    ).prefetch_related('payments')

    if centre_ids is not None:
        payments = payments.filter(invoice__visit__centre_id__in=centre_ids)
        invoices = invoices.filter(visit__centre_id__in=centre_ids)
        discounts = discounts.filter(invoice__visit__centre_id__in=centre_ids)
        visits = visits.filter(centre_id__in=centre_ids)
        outstanding = outstanding.filter(visit__centre_id__in=centre_ids)

    collections = _money(payments.aggregate(total=Sum('amount'))['total'])
    invoice_billed = _money(invoices.exclude(status='CANCELLED').aggregate(total=Sum('total'))['total'])
    discount_applied = _money(
        discounts.filter(status='APPROVED').aggregate(total=Sum('applied_amount'))['total']
    )
    outstanding_total = Decimal('0')
    for invoice in outstanding:
        paid = sum((p.amount for p in invoice.payments.all() if p.status == 'CONFIRMED'), Decimal('0'))
        outstanding_total += (invoice.total or Decimal('0')) - paid
    outstanding_total = _money(outstanding_total)

    payments_by_method = [
        {'method': row['method'], 'count': row['count'], 'total': _money(row['total'])}
        for row in payments.values('method').annotate(count=Count('id'), total=Sum('amount')).order_by('method')
    ]
    payments_by_centre = [
        {
            'centre': row['invoice__visit__centre__code'],
            'name': row['invoice__visit__centre__name'],
            'count': row['count'],
            'total': _money(row['total']),
        }
        for row in payments.values(
            'invoice__visit__centre__code', 'invoice__visit__centre__name',
        ).annotate(count=Count('id'), total=Sum('amount')).order_by('invoice__visit__centre__code')
    ]
    invoices_by_status = [
        {'status': row['status'], 'count': row['count'], 'total': _money(row['total'])}
        for row in invoices.values('status').annotate(count=Count('id'), total=Sum('total')).order_by('status')
    ]
    discounts_by_status = [
        {'status': row['status'], 'count': row['count'], 'applied': _money(row['applied'])}
        for row in discounts.values('status').annotate(count=Count('id'), applied=Sum('applied_amount')).order_by('status')
    ]
    daily_collections = [
        {'date': row['day'].date() if hasattr(row['day'], 'date') else row['day'], 'total': _money(row['total']), 'count': row['count']}
        for row in payments.annotate(day=TruncDate('created_at')).values('day').annotate(
            count=Count('id'), total=Sum('amount'),
        ).order_by('day')
    ]

    payment_rows = [{
        'receipt': p.receipt_number,
        'invoice': p.invoice.invoice_number,
        'patient': f'{p.invoice.visit.patient.first_name} {p.invoice.visit.patient.last_name}',
        'centre': p.invoice.visit.centre.code,
        'method': p.method,
        'amount': _money(p.amount),
        'reference': p.reference,
        'received_by': getattr(p.received_by, 'username', '') or '',
        'created_at': timezone.localtime(p.created_at).strftime('%Y-%m-%d %H:%M'),
    } for p in payments.order_by('-created_at')[:2000]]

    invoice_rows = [{
        'invoice': inv.invoice_number,
        'visit': inv.visit.visit_id,
        'patient': f'{inv.visit.patient.first_name} {inv.visit.patient.last_name}',
        'centre': inv.visit.centre.code,
        'subtotal': _money(inv.subtotal),
        'discount': _money(inv.discount_amount),
        'total': _money(inv.total),
        'status': inv.status,
        'created_at': timezone.localtime(inv.created_at).strftime('%Y-%m-%d %H:%M'),
    } for inv in invoices.order_by('-created_at')[:2000]]

    discount_rows = [{
        'invoice': d.invoice.invoice_number,
        'patient': f'{d.invoice.visit.patient.first_name} {d.invoice.visit.patient.last_name}',
        'centre': d.invoice.visit.centre.code,
        'type': d.discount_type,
        'requested': _money(d.requested_value),
        'applied': _money(d.applied_amount),
        'status': d.status,
        'reason': d.reason,
        'requested_by': getattr(d.requested_by, 'username', '') or '',
        'reviewed_by': getattr(d.reviewed_by, 'username', '') or '',
        'created_at': timezone.localtime(d.created_at).strftime('%Y-%m-%d %H:%M'),
    } for d in discounts.order_by('-created_at')[:2000]]

    visit_rows = [{
        'visit': v.visit_id,
        'patient': f'{v.patient.first_name} {v.patient.last_name}',
        'uhid': v.patient.uhid,
        'centre': v.centre.code,
        'channel': v.referral_source,
        'status': v.status,
        'created_at': timezone.localtime(v.created_at).strftime('%Y-%m-%d %H:%M'),
    } for v in visits.order_by('-created_at')[:2000]]

    return {
        'period': period,
        'period_label': label,
        'start': start_dt,
        'end': end_dt,
        'start_date': start_dt.date().isoformat(),
        'end_date': end_dt.date().isoformat(),
        'centres': centres,
        'selected_centre': selected_centre,
        'centre_id': str(selected_centre.pk) if selected_centre else '',
        'kpis': {
            'collections': collections,
            'invoice_billed': invoice_billed,
            'discount_applied': discount_applied,
            'outstanding': outstanding_total,
            'payment_count': payments.count(),
            'invoice_count': invoices.count(),
            'visit_count': visits.count(),
            'pending_discounts': discounts.filter(status='PENDING').count(),
            'approved_discounts': discounts.filter(status='APPROVED').count(),
            'rejected_discounts': discounts.filter(status='REJECTED').count(),
        },
        'payments_by_method': payments_by_method,
        'payments_by_centre': payments_by_centre,
        'invoices_by_status': invoices_by_status,
        'discounts_by_status': discounts_by_status,
        'daily_collections': daily_collections,
        'payment_rows': payment_rows,
        'invoice_rows': invoice_rows,
        'discount_rows': discount_rows,
        'visit_rows': visit_rows,
        'period_choices': PERIOD_CHOICES,
        'datasets': DATASETS,
    }


def _dataset_table(report, dataset):
    if dataset == 'invoices':
        headers = ['Invoice', 'Visit', 'Patient', 'Centre', 'Subtotal', 'Discount', 'Total', 'Status', 'Created']
        rows = [[
            r['invoice'], r['visit'], r['patient'], r['centre'], r['subtotal'], r['discount'],
            r['total'], r['status'], r['created_at'],
        ] for r in report['invoice_rows']]
        title = 'Invoices'
    elif dataset == 'discounts':
        headers = ['Invoice', 'Patient', 'Centre', 'Type', 'Requested', 'Applied', 'Status', 'Reason', 'Requested by', 'Reviewed by', 'Created']
        rows = [[
            r['invoice'], r['patient'], r['centre'], r['type'], r['requested'], r['applied'],
            r['status'], r['reason'], r['requested_by'], r['reviewed_by'], r['created_at'],
        ] for r in report['discount_rows']]
        title = 'Discounts'
    elif dataset == 'visits':
        headers = ['Visit', 'Patient', 'UHID', 'Centre', 'Channel', 'Status', 'Created']
        rows = [[
            r['visit'], r['patient'], r['uhid'], r['centre'], r['channel'], r['status'], r['created_at'],
        ] for r in report['visit_rows']]
        title = 'Visits'
    elif dataset == 'summary':
        headers = ['Metric', 'Value']
        kpis = report['kpis']
        rows = [
            ['Period', report['period_label']],
            ['Collections (₦)', kpis['collections']],
            ['Invoice billed (₦)', kpis['invoice_billed']],
            ['Discounts applied (₦)', kpis['discount_applied']],
            ['Outstanding balance (₦)', kpis['outstanding']],
            ['Payments', kpis['payment_count']],
            ['Invoices', kpis['invoice_count']],
            ['Visits', kpis['visit_count']],
            ['Pending discounts', kpis['pending_discounts']],
            ['Approved discounts', kpis['approved_discounts']],
            ['Rejected discounts', kpis['rejected_discounts']],
        ]
        for row in report['payments_by_method']:
            rows.append([f"Collections via {row['method']}", row['total']])
        for row in report['payments_by_centre']:
            rows.append([f"Collections at {row['centre']}", row['total']])
        title = 'Executive summary'
    else:
        headers = ['Receipt', 'Invoice', 'Patient', 'Centre', 'Method', 'Amount', 'Reference', 'Received by', 'Created']
        rows = [[
            r['receipt'], r['invoice'], r['patient'], r['centre'], r['method'], r['amount'],
            r['reference'], r['received_by'], r['created_at'],
        ] for r in report['payment_rows']]
        title = 'Payments'
        dataset = 'payments'
    return dataset, title, headers, rows


def export_report(report, fmt='csv', dataset='payments'):
    dataset, title, headers, rows = _dataset_table(report, dataset)
    fmt = (fmt or 'csv').lower()
    stamp = report['start_date'].replace('-', '')
    basename = f'hfdn-{dataset}-{report["period"]}-{stamp}'
    if fmt == 'pdf':
        return _pdf_response(report, title, headers, rows, basename)
    if fmt in ('xlsx', 'excel'):
        return _xlsx_response(title, headers, rows, basename)
    return _csv_response(headers, rows, basename)


def _csv_response(headers, rows, basename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{basename}.csv"'
    return response


def _xlsx_response(title, headers, rows, basename):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = title[:31] or 'Report'
    sheet.append(headers)
    for row in rows:
        sheet.append([float(cell) if isinstance(cell, Decimal) else cell for cell in row])
    buffer = io.BytesIO()
    book.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{basename}.xlsx"'
    return response


def _pdf_response(report, title, headers, rows, basename):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)
    y = height - 40
    pdf.setTitle(f'HFDN {title}')
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(36, y, 'Health Focus Diagnostics Nigeria — Accountant report')
    y -= 16
    pdf.setFont('Helvetica', 9)
    pdf.drawString(36, y, f'{title} · {report["period_label"]} · {report["start_date"]} to {report["end_date"]}')
    y -= 18
    pdf.setFont('Helvetica-Bold', 8)
    col_width = max(60, (width - 72) / max(len(headers), 1))
    x = 36
    for header in headers:
        pdf.drawString(x, y, str(header)[:18])
        x += col_width
    y -= 6
    pdf.line(36, y, width - 36, y)
    y -= 12
    pdf.setFont('Helvetica', 7)
    for row in rows:
        if y < 40:
            pdf.showPage()
            y = height - 40
            pdf.setFont('Helvetica', 7)
        x = 36
        for cell in row:
            text = f'{cell:.2f}' if isinstance(cell, Decimal) else str(cell)
            pdf.drawString(x, y, text[:22])
            x += col_width
        y -= 11
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{basename}.pdf"'
    return response
