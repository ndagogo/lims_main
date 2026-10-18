"""Audited Excel / PDF / CSV exports for management reports."""
import csv
import io
from decimal import Decimal

from django.http import HttpResponse

from core.audit import record_audit


def _export_category(report_type):
    if report_type == 'management_clinical':
        return 'SYSTEM'
    if (
        'revenue' in report_type
        or report_type.startswith(('accountant', 'management'))
        or report_type in ('commission', 'hmo_statements')
    ):
        return 'BILLING'
    return 'SYSTEM'


def log_report_export(request, *, report_type, fmt, period_label='', centre='', row_count=0, include_patient_ids=False):
    """Record a sensitive export for the audit trail."""
    return record_audit(
        request=request,
        actor=request.user,
        action='REPORT_EXPORTED',
        object_type='ReportExport',
        object_id=report_type[:100],
        category=_export_category(report_type),
        detail={
            'report_type': report_type,
            'format': fmt,
            'period': period_label,
            'centre': centre,
            'row_count': row_count,
            'include_patient_ids': bool(include_patient_ids),
        },
        reason=f'Exported {report_type} as {fmt}',
    )


def table_export(title, headers, rows, *, fmt='csv', basename='hfdn-report', meta_lines=None):
    return tables_export(
        title, [{'title': title, 'headers': headers, 'rows': rows}],
        fmt=fmt, basename=basename, meta_lines=meta_lines,
    )


def tables_export(title, tables, *, fmt='csv', basename='hfdn-report', meta_lines=None):
    fmt = (fmt or 'csv').lower()
    tables = list(tables or [])
    if fmt == 'pdf':
        return _pdf_tables(title, tables, basename, meta_lines or [])
    if fmt in ('xlsx', 'excel'):
        return _xlsx_tables(tables, basename)
    return _csv_tables(tables, basename)


def _csv_tables(tables, basename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    multiple = len(tables) > 1
    for table in tables:
        headers = list(table['headers'])
        if multiple:
            headers = ['Section', *headers]
        writer.writerow(headers)
        for row in table['rows']:
            cells = [_cell(c) for c in row]
            if multiple:
                cells = [table['title'], *cells]
            writer.writerow(cells)
        if multiple:
            writer.writerow([])
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{basename}.csv"'
    return response


def _sheet_title(title, used):
    cleaned = ''.join(ch for ch in (title or 'Report') if ch not in '[]:*?/\\')
    cleaned = (cleaned or 'Report')[:31]
    candidate = cleaned
    n = 2
    while candidate in used:
        suffix = f' {n}'
        candidate = f'{cleaned[:31 - len(suffix)]}{suffix}'
        n += 1
    used.add(candidate)
    return candidate


def _xlsx_tables(tables, basename):
    from openpyxl import Workbook

    book = Workbook()
    used = set()
    for index, table in enumerate(tables):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = _sheet_title(table.get('sheet') or table['title'], used)
        sheet.append(list(table['headers']))
        for row in table['rows']:
            sheet.append([float(c) if isinstance(c, Decimal) else c for c in row])
    if not tables:
        book.active.title = 'Report'
    buffer = io.BytesIO()
    book.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{basename}.xlsx"'
    return response


def _pdf_tables(title, tables, basename, meta_lines):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)
    pdf.setTitle(f'HFDN {title}')

    def header(section_title, first=False):
        y = height - 40
        pdf.setFont('Helvetica-Bold', 12)
        pdf.drawString(36, y, 'Health Focus Diagnostics Nigeria — Management report')
        y -= 14
        pdf.setFont('Helvetica', 9)
        pdf.drawString(36, y, str(title)[:140])
        y -= 12
        if first:
            for line in meta_lines:
                pdf.drawString(36, y, str(line)[:140])
                y -= 11
        pdf.setFont('Helvetica-Bold', 10)
        pdf.drawString(36, y, str(section_title)[:140])
        return y - 16

    for index, table in enumerate(tables):
        if index:
            pdf.showPage()
        y = header(table['title'], first=index == 0)
        headers = list(table['headers'])
        pdf.setFont('Helvetica-Bold', 7)
        col_width = max(42, (width - 72) / max(len(headers), 1))
        x = 36
        for head in headers:
            pdf.drawString(x, y, str(head)[:18])
            x += col_width
        y -= 6
        pdf.line(36, y, width - 36, y)
        y -= 12
        pdf.setFont('Helvetica', 7)
        for row in table['rows']:
            if y < 36:
                pdf.showPage()
                y = header(table['title'])
                pdf.setFont('Helvetica', 7)
            x = 36
            for cell in row:
                pdf.drawString(x, y, str(_cell(cell))[:22])
                x += col_width
            y -= 11
    if not tables:
        header(title, first=True)
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{basename}.pdf"'
    return response


def _cell(value):
    if isinstance(value, Decimal):
        return f'{value:.2f}'
    return value


def management_summary_rows(dashboard):
    """Aggregate-only rows — no patient identifiers."""
    fin = dashboard['financial']
    clin = dashboard['clinical']
    ref = dashboard['referral']
    headers = ['Section', 'Metric', 'Value']
    rows = [
        ['Financial', 'Gross charges', fin['gross_charges']],
        ['Financial', 'Discounts', fin['discounts']],
        ['Financial', 'Invoice value', fin['invoice_value']],
        ['Financial', 'Collections', fin['collections']],
        ['Financial', 'Refunds', fin['refunds']],
        ['Financial', 'Net receipts', fin['net_receipts']],
        ['Financial', 'Outstanding receivables', fin['outstanding_receivables']],
        ['Clinical', 'Pending tests', clin['pending_tests']],
        ['Clinical', 'Open samples', clin['open_samples']],
        ['Clinical', 'Overdue samples', clin['overdue_samples']],
        ['Clinical', 'Released reports', clin['released_reports']],
        ['Clinical', 'Rejection rate %', clin['rejection_rate']],
        ['Clinical', 'Recollection rate %', clin['recollection_rate']],
        ['Referral', 'Referral visits', ref['referral_visits']],
        ['Referral', 'Commission accruals', ref['accruals']],
        ['Referral', 'Commission reversals', ref['reversals']],
        ['Referral', 'Commission settlements', ref['settlements']],
        ['Referral', 'Commission net', ref['commission_net']],
        ['Referral', 'Pending payout liability', ref['pending_payout_liability']],
    ]
    for row in ref.get('marketers') or []:
        names = '; '.join(row.get('doctor_names') or [])
        rows.append([
            'Referral',
            f"Doctors introduced by {row['name']} ({row['doctors']})",
            names,
        ])
    for row in dashboard.get('centres_panel', {}).get('by_centre') or []:
        rows.append([
            'Centre',
            f"{row['code']} invoice value",
            row['invoice_value'],
        ])
    return headers, rows
