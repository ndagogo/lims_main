"""Printable batch report PDF generated when a sample batch is dispatched."""
from __future__ import annotations

import io
from collections import OrderedDict
from datetime import date

from django.utils import timezone


def _age_gender(patient, on_date=None):
    from laboratory.services import age_years

    parts = []
    years = age_years(patient, on_date=on_date)
    if years is not None:
        parts.append(f'{int(years)} Years')
    sex = (patient.sex or '').strip()
    if sex:
        parts.append(sex)
    return ' & '.join(parts) if parts else '—'


def _customer_name(patient):
    title = (patient.title or '').strip()
    name = f'{patient.first_name} {patient.last_name}'.strip()
    if patient.other_names:
        name = f'{name} {patient.other_names}'.strip()
    name = name.upper()
    return f'{title}. {name}'.replace('..', '.') if title else name


def _fmt_date(value):
    if not value:
        return '—'
    if hasattr(value, 'astimezone'):
        value = timezone.localtime(value)
    if hasattr(value, 'strftime'):
        return value.strftime('%d-%m-%Y')
    return str(value)


def _fmt_datetime(value):
    if not value:
        return '—'
    if hasattr(value, 'astimezone'):
        value = timezone.localtime(value)
    return value.strftime('%d-%m-%Y %H:%M')


def _barcode_image(value):
    """Render Code128 for the batch number; returns ImageReader or None."""
    try:
        import barcode
        from barcode.writer import ImageWriter
        from reportlab.lib.utils import ImageReader
    except ImportError:
        return None
    raw = (value or '').strip()
    if not raw:
        return None
    buffer = io.BytesIO()
    try:
        barcode.get('code128', raw, writer=ImageWriter()).write(
            buffer,
            options={
                'write_text': False,
                'module_height': 10.0,
                'module_width': 0.32,
                'quiet_zone': 1.5,
            },
        )
    except Exception:
        return None
    buffer.seek(0)
    return ImageReader(buffer)


def batch_report_rows(batch):
    """
    Ordered sample rows for the batch report, grouped by patient then visit date.
    Each item is one sample (one service); customer fields repeat only on first
    row of a patient group for layout mirroring.
    """
    items = list(
        batch.items.select_related(
            'sample__centre',
            'sample__order__test',
            'sample__order__visit__patient',
            'sample__order__visit__centre',
        ).order_by(
            'sample__order__visit__patient__last_name',
            'sample__order__visit__patient__first_name',
            'sample__order__visit__created_at',
            'sample__sample_id',
        )
    )
    groups = OrderedDict()
    for item in items:
        sample = item.sample
        visit = sample.order.visit
        patient = visit.patient
        key = str(patient.pk)
        groups.setdefault(key, []).append({
            'sample_id': sample.sample_id,
            'visit_date': _fmt_date(visit.created_at),
            'customer_name': _customer_name(patient),
            'age_gender': _age_gender(patient, on_date=timezone.localdate(visit.created_at) if visit.created_at else date.today()),
            'vid': visit.visit_id,
            'specimen': (sample.specimen_type or sample.order.test.specimen or '—').strip() or '—',
            'service_name': sample.order.test.name,
            'reg_centre': visit.centre.name if visit.centre_id else '',
            'proc_centre': sample.centre.name if sample.centre_id else '',
        })
    rows = []
    for group in groups.values():
        for index, row in enumerate(group):
            rows.append({
                **row,
                'show_customer': index == 0,
                'group_end': index == len(group) - 1,
            })
    return rows


def batch_report_header(batch, rows=None):
    rows = rows if rows is not None else batch_report_rows(batch)
    reg_names = []
    for row in rows:
        name = (row.get('reg_centre') or '').strip()
        if name and name not in reg_names:
            reg_names.append(name)
    return {
        'batch_code': batch.batch_code,
        'batch_date': _fmt_datetime(batch.dispatched_at or batch.created_at),
        'reg_centre': ' / '.join(reg_names) if reg_names else (batch.centre.name if batch.centre_id else '—'),
        'proc_centre': batch.centre.name if batch.centre_id else '—',
        'department': batch.department or '',
    }


def build_batch_report_pdf(batch):
    """Return PDF bytes for a dispatched (or open) sample batch."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    rows = batch_report_rows(batch)
    header = batch_report_header(batch, rows)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left = 28
    right = width - 28
    usable = right - left

    # Column x positions matching the sample layout.
    cols = {
        'sample_id': left,
        'visit_dt': left + 72,
        'customer': left + 128,
        'age': left + 248,
        'vid': left + 318,
        'specimen': left + 388,
        'service': left + 458,
    }
    service_width = right - cols['service']

    pdf.setTitle(f'Batch Report {header["batch_code"]}')

    def draw_header(y):
        pdf.setFont('Helvetica-Bold', 16)
        pdf.drawString(left, y, 'Batch Report')
        mid_x = left + usable * 0.36
        right_x = left + usable * 0.68
        meta_y = y
        pdf.setFont('Helvetica', 9)
        pdf.drawString(mid_x, meta_y, f'Reg Centre: {header["reg_centre"]}')
        pdf.drawString(right_x, meta_y, f'Proc Centre: {header["proc_centre"]}')
        meta_y -= 12
        pdf.drawString(mid_x, meta_y, f'Batch No: {header["batch_code"]}')
        pdf.drawString(right_x, meta_y, f'Batch Date: {header["batch_date"]}')
        meta_y -= 14
        pdf.drawString(mid_x, meta_y, 'Barcode:')
        barcode = _barcode_image(header['batch_code'])
        if barcode:
            pdf.drawImage(barcode, mid_x + 48, meta_y - 6, width=130, height=22, mask='auto', preserveAspectRatio=True)
        else:
            pdf.setFont('Helvetica', 8)
            pdf.drawString(mid_x + 48, meta_y, header['batch_code'])
        y = min(y - 18, meta_y - 18)
        if header.get('department'):
            pdf.setFont('Helvetica', 8)
            pdf.drawString(left, y, f'Department: {header["department"]}')
            y -= 12
        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.setLineWidth(1)
        pdf.line(left, y, right, y)
        y -= 14
        pdf.setFont('Helvetica-Bold', 8)
        pdf.drawString(cols['sample_id'], y, 'Sample ID')
        pdf.drawString(cols['visit_dt'], y, 'Visit Dt')
        pdf.drawString(cols['customer'], y, 'Customer Name')
        pdf.drawString(cols['age'], y, 'Age&Gender')
        pdf.drawString(cols['vid'], y, 'VID')
        pdf.drawString(cols['specimen'], y, 'Specimen')
        pdf.drawString(cols['service'], y, 'Service Name')
        y -= 4
        pdf.setLineWidth(0.6)
        pdf.line(left, y, right, y)
        return y - 12

    def ensure_space(y, needed=36):
        if y >= needed:
            return y
        pdf.showPage()
        return draw_header(height - 40)

    y = draw_header(height - 40)
    pdf.setFont('Helvetica', 8)

    if not rows:
        pdf.drawString(left, y, 'No samples in this batch.')
    else:
        for row in rows:
            y = ensure_space(y, 28)
            pdf.setFont('Helvetica', 8)
            pdf.drawString(cols['sample_id'], y, str(row['sample_id'])[:14])
            pdf.drawString(cols['visit_dt'], y, row['visit_date'])
            if row['show_customer']:
                pdf.drawString(cols['customer'], y, (row['customer_name'] or '—')[:22])
                pdf.drawString(cols['age'], y, (row['age_gender'] or '—')[:16])
                pdf.drawString(cols['vid'], y, (row['vid'] or '—')[:14])
            pdf.drawString(cols['specimen'], y, (row['specimen'] or '—')[:14])
            service = row['service_name'] or '—'
            # Wrap long service names under the Service column.
            while service:
                chunk = service[:28]
                pdf.drawString(cols['service'], y, chunk)
                service = service[28:]
                if service:
                    y -= 10
                    y = ensure_space(y, 28)
                    pdf.setFont('Helvetica', 8)
            y -= 12
            if row.get('group_end'):
                y = ensure_space(y, 16)
                pdf.setDash(1, 2)
                pdf.setLineWidth(0.5)
                pdf.line(left, y + 4, right, y + 4)
                pdf.setDash()
                y -= 6

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
