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
                'module_height': 8.0,
                'module_width': 0.28,
                'quiet_zone': 1.0,
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
            'age_gender': _age_gender(
                patient,
                on_date=timezone.localdate(visit.created_at) if visit.created_at else date.today(),
            ),
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


def _fit(pdf, text, font, size, max_width):
    """Return text trimmed with ellipsis so it fits inside max_width."""
    text = str(text or '')
    if not text:
        return ''
    pdf.setFont(font, size)
    if pdf.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = '…'
    while text and pdf.stringWidth(text + ellipsis, font, size) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def build_batch_report_pdf(batch):
    """Return PDF bytes for a dispatched (or open) sample batch."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    rows = batch_report_rows(batch)
    header = batch_report_header(batch, rows)

    # Landscape gives the seven-column sample layout room without collisions.
    buffer = io.BytesIO()
    page = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page)
    width, height = page
    left = 24
    right = width - 24
    usable = right - left
    bottom = 28
    body_size = 8
    head_size = 8

    # Proportional column widths (points) — must sum to usable.
    widths = {
        'sample_id': 78,
        'visit_dt': 68,
        'customer': 150,
        'age': 90,
        'vid': 90,
        'specimen': 90,
        'service': usable - (78 + 68 + 150 + 90 + 90 + 90),
    }
    cols = {}
    x = left
    for key, w in widths.items():
        cols[key] = x
        x += w

    pdf.setTitle(f'Batch Report {header["batch_code"]}')

    def draw_header():
        top = height - 28
        # Left title block
        pdf.setFont('Helvetica-Bold', 18)
        pdf.drawString(left, top, 'Batch Report')
        if header.get('department'):
            pdf.setFont('Helvetica', 8)
            pdf.drawString(left, top - 14, f'Department: {header["department"]}')

        # Middle meta + barcode (kept clear of the right column)
        mid_x = left + 175
        mid_w = 300
        pdf.setFont('Helvetica', 9)
        pdf.drawString(mid_x, top, _fit(pdf, f'Reg Centre: {header["reg_centre"]}', 'Helvetica', 9, mid_w))
        pdf.drawString(mid_x, top - 13, _fit(pdf, f'Batch No: {header["batch_code"]}', 'Helvetica', 9, mid_w))
        pdf.setFont('Helvetica', 8)
        pdf.drawString(mid_x, top - 26, 'Barcode:')
        barcode = _barcode_image(header['batch_code'])
        if barcode:
            pdf.drawImage(
                barcode, mid_x + 48, top - 42, width=170, height=20,
                mask='auto', preserveAspectRatio=True, anchor='c',
            )
        else:
            pdf.drawString(mid_x + 48, top - 26, header['batch_code'])

        # Right meta
        right_x = left + 520
        right_w = right - right_x
        pdf.setFont('Helvetica', 9)
        pdf.drawString(right_x, top, _fit(pdf, f'Proc Centre: {header["proc_centre"]}', 'Helvetica', 9, right_w))
        pdf.drawString(right_x, top - 13, _fit(pdf, f'Batch Date: {header["batch_date"]}', 'Helvetica', 9, right_w))

        y = top - 52
        pdf.setStrokeColorRGB(0.1, 0.1, 0.1)
        pdf.setLineWidth(1.1)
        pdf.line(left, y, right, y)
        y -= 14

        pdf.setFont('Helvetica-Bold', head_size)
        labels = [
            ('sample_id', 'Sample ID'),
            ('visit_dt', 'Visit Dt'),
            ('customer', 'Customer Name'),
            ('age', 'Age&Gender'),
            ('vid', 'VID'),
            ('specimen', 'Specimen'),
            ('service', 'Service Name'),
        ]
        for key, label in labels:
            pdf.drawString(cols[key], y, label)
        y -= 4
        pdf.setLineWidth(0.7)
        pdf.line(left, y, right, y)
        return y - 12

    def ensure_space(y, needed):
        if y >= bottom + needed:
            return y
        pdf.showPage()
        return draw_header()

    def draw_cell(key, text, y, *, bold=False):
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        pad = 3
        max_w = widths[key] - pad
        fitted = _fit(pdf, text, font, body_size, max_w)
        pdf.setFont(font, body_size)
        pdf.drawString(cols[key], y, fitted)

    y = draw_header()

    if not rows:
        pdf.setFont('Helvetica', body_size)
        pdf.drawString(left, y, 'No samples in this batch.')
    else:
        for row in rows:
            y = ensure_space(y, 16)
            draw_cell('sample_id', row['sample_id'], y)
            draw_cell('visit_dt', row['visit_date'], y)
            if row['show_customer']:
                draw_cell('customer', row['customer_name'], y, bold=True)
                draw_cell('age', row['age_gender'], y)
                draw_cell('vid', row['vid'], y)
            draw_cell('specimen', row['specimen'], y)
            draw_cell('service', row['service_name'], y)
            y -= 12
            if row.get('group_end'):
                y = ensure_space(y, 10)
                pdf.setDash(1.2, 2.2)
                pdf.setStrokeColorRGB(0.35, 0.35, 0.35)
                pdf.setLineWidth(0.5)
                pdf.line(left, y + 4, right, y + 4)
                pdf.setDash()
                pdf.setStrokeColorRGB(0, 0, 0)
                y -= 4

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
