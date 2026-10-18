"""Printable radiology report using frozen centre format + radiologist signature."""
from __future__ import annotations

import os
import re
from html import escape, unescape

from django.conf import settings
from django.utils import timezone


def build_radiology_report_pdf(report):
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    import io

    snap = report.configuration_snapshot or {}
    fmt = snap.get('format') or {}
    auth = snap.get('authenticator') or {}
    patient = report.exam.order.visit.patient
    visit = report.exam.order.visit
    centre = report.centre
    margin = max(12, int(fmt.get('margin_mm') or 16)) * mm
    font = fmt.get('font_family') or 'Helvetica'
    bold = _bold_font(font)
    size = int(fmt.get('body_font_size') or 10)
    accent = HexColor(fmt.get('accent_color') or '#0f766e')
    ink = HexColor(fmt.get('primary_color') or '#102033')
    mute = HexColor('#5c6570')
    rule = HexColor('#d9d3c7')
    paper = HexColor('#f6f4ef')

    identity = _identity_rows(report, snap)
    header_lines = _letterhead_lines(fmt, centre)
    logo_extra = 6 * mm if (fmt.get('show_logo') and fmt.get('logo_path')) else 0
    top_reserve = 12 * mm + logo_extra + (4 * mm * len(header_lines))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin + top_reserve, bottomMargin=margin + 8 * mm,
        title=f'{centre.code} {visit.visit_id}',
        pageCompression=0,
    )
    frame_width = A4[0] - (2 * margin)

    label_style = ParagraphStyle(
        'id-label', fontName=font, fontSize=7.5, leading=9, textColor=mute,
    )
    value_style = ParagraphStyle(
        'id-value', fontName=bold, fontSize=size, leading=size + 3, textColor=ink,
    )
    heading_style = ParagraphStyle(
        'sec-head', fontName=bold, fontSize=size, leading=size + 2, textColor=accent, spaceBefore=2,
    )
    body_style = ParagraphStyle(
        'sec-body', fontName=font, fontSize=size, leading=size + 4, textColor=ink, spaceAfter=2,
    )
    banner_style = ParagraphStyle(
        'banner', fontName=bold, fontSize=size, leading=size + 3, textColor=HexColor('#9a3412'), alignment=TA_CENTER,
    )
    sign_label = ParagraphStyle(
        'sign-label', fontName=font, fontSize=8, leading=10, textColor=mute,
    )
    sign_name = ParagraphStyle(
        'sign-name', fontName=bold, fontSize=size + 1, leading=size + 3, textColor=ink,
    )
    sign_meta = ParagraphStyle(
        'sign-meta', fontName=font, fontSize=8, leading=10, textColor=ink,
    )

    story = []
    if report.status == 'AMENDED':
        story.append(Paragraph('AMENDED — this copy replaces the previously released report.', banner_style))
        story.append(Spacer(1, 6))

    half = frame_width / 2
    label_w = 30 * mm
    table_rows = []
    for left, right in identity:
        table_rows.append([
            Paragraph(escape(left[0]).upper(), label_style),
            Paragraph(_markup(left[1]) or '—', value_style),
            Paragraph(escape(right[0]).upper(), label_style),
            Paragraph(_markup(right[1]) or '—', value_style),
        ])
    identity_table = Table(table_rows, colWidths=[label_w, half - label_w, label_w, half - label_w])
    identity_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), paper),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BOX', (0, 0), (-1, -1), 0.6, accent),
        ('LINEBELOW', (0, 0), (-1, -2), 0.25, rule),
        ('LINEAFTER', (1, 0), (1, -1), 0.25, rule),
        ('BACKGROUND', (0, 0), (0, -1), white),
        ('BACKGROUND', (2, 0), (2, -1), white),
    ]))
    story.append(identity_table)
    story.append(Spacer(1, 8))

    header_note = (snap.get('template') or {}).get('report_header') or ''
    if header_note.strip() and header_note.strip() not in (snap.get('findings') or report.findings or ''):
        story.append(Paragraph(_markup(header_note), body_style))

    for heading, body in _report_sections(report, snap):
        block = []
        if heading:
            block.extend([
                Paragraph(escape(heading).upper(), heading_style),
                HRFlowable(width='100%', thickness=0.6, color=accent, spaceBefore=1, spaceAfter=4),
            ])
        block.append(Paragraph(_markup(body), body_style))
        block.append(Spacer(1, 6))
        story.append(KeepTogether(block))

    extras = _extra_rows(snap, report)
    if extras:
        extra_data = []
        for label, value in extras:
            extra_data.append([
                Paragraph(escape(label), label_style),
                Paragraph(_markup(value), value_style),
            ])
        extra_table = Table(extra_data, colWidths=[42 * mm, frame_width - 42 * mm])
        extra_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        story.append(Spacer(1, 2))
        story.append(extra_table)

    if fmt.get('show_authenticator_signature', True) and auth:
        sign_bits = [Spacer(1, 10), HRFlowable(width='100%', thickness=0.4, color=rule, spaceBefore=2, spaceAfter=8)]
        sign_bits.append(Paragraph(escape(fmt.get('signature_label') or 'Reported by'), sign_label))
        sig_path = auth.get('signature_path') or ''
        if sig_path and _image_readable(os.path.join(settings.MEDIA_ROOT, sig_path)):
            sign_bits.append(Spacer(1, 2))
            sign_bits.append(Image(os.path.join(settings.MEDIA_ROOT, sig_path), width=42 * mm, height=16 * mm, kind='proportional'))
        if auth.get('name'):
            sign_bits.append(Paragraph(escape(auth['name']), sign_name))
        for line in (auth.get('title'), auth.get('qualifications'), auth.get('specialty')):
            if line:
                sign_bits.append(Paragraph(escape(str(line)), sign_meta))
        if auth.get('license_number'):
            sign_bits.append(Paragraph(escape(f"Reg. No: {auth['license_number']}"), sign_meta))
        signed = _when(report.finalized_at)
        if signed:
            sign_bits.append(Paragraph(escape(f'Signed {signed}'), sign_meta))
        released = _when(report.released_at)
        if released:
            sign_bits.append(Paragraph(escape(f'Dispatched {released}'), sign_meta))
        # Keep the signature with its name so a page break cannot separate them.
        story.append(KeepTogether(sign_bits))

    ctx = {
        'fmt': fmt, 'centre': centre, 'font': font, 'bold': bold, 'size': size,
        'accent': accent, 'ink': ink, 'mute': mute, 'header_lines': header_lines,
        'title': _study_title(report, fmt), 'margin': margin, 'visit_id': visit.visit_id,
    }

    def _paint(pdf, document):
        _paint_page(pdf, document, ctx)

    doc.build(story, onFirstPage=_paint, onLaterPages=_paint)
    return buffer.getvalue()


def _identity_rows(report, snap):
    patient = report.exam.order.visit.patient
    visit = report.exam.order.visit
    order = report.exam.order
    exam = report.exam
    procedure = ''
    if order.procedure_id:
        procedure = order.procedure.name
    modality = order.modality.get_modality_type_display()
    room = order.modality.name
    study = procedure or room
    on_date = exam.ended_at or exam.started_at or report.finalized_at or report.created_at
    age = _age_label(patient.date_of_birth, on_date)
    sex = patient.get_sex_display() if patient.sex else ''
    age_sex = ' / '.join(part for part in (age, sex) if part)
    return [
        (('Patient', _patient_name(patient)), ('Age / Sex', age_sex)),
        (('UHID', patient.uhid), ('Visit', visit.visit_id)),
        (('Date of birth', _date(patient.date_of_birth)), ('Exam date', _when(on_date))),
        (('Referred by', _referring_doctor(visit)), ('Procedure', study)),
        (('Modality', modality), ('Status', report.get_status_display())),
    ]


def _report_sections(report, snap):
    findings_raw = snap.get('findings') or report.findings or ''
    impression = snap.get('impression') or report.impression or ''
    recommendations = snap.get('recommendations') or report.recommendations or ''
    addendum = snap.get('addendum') or report.addendum or ''
    if _html_is_full_report(findings_raw):
        sections = [('', findings_raw)]
    else:
        sections = [
            ('Clinical information', snap.get('indication') or report.indication),
            ('Technique', snap.get('technique') or report.technique),
            ('Findings', findings_raw),
        ]
        if _substantive(impression) and impression.strip() not in findings_raw:
            sections.append(('Impression', impression))
        if _substantive(recommendations) and recommendations.strip() not in findings_raw:
            sections.append(('Recommendations', recommendations))
    if _substantive(addendum):
        sections.append(('Addendum', addendum))
    return [(heading, body) for heading, body in sections if (body or '').strip() and (not heading or _substantive(body))]


def _html_is_full_report(findings):
    if not _looks_like_html(findings):
        return False
    plain = re.sub(r'<[^>]+>', ' ', findings or '').upper()
    return 'FINDINGS' in plain and ('IMPRESSION' in plain or 'EXAM' in plain)


def _substantive(text):
    plain = re.sub(r'<[^>]+>', ' ', text or '')
    plain = unescape(re.sub(r'\s+', ' ', plain)).strip().rstrip(':').strip()
    if not plain:
        return False
    labels = {
        'clinical indication', 'clinical information', 'technique', 'findings',
        'impression', 'recommendations', 'exam', 'comparison', 'report',
    }
    return plain.lower() not in labels


def _extra_rows(snap, report):
    values = snap.get('extra_values') if snap.get('extra_values') is not None else report.extra_values
    values = values or {}
    fields = ((snap.get('template') or {}).get('extra_fields') or [])
    labels = {}
    for field in fields:
        if isinstance(field, dict) and field.get('code'):
            labels[field['code']] = field.get('label') or field['code']
    rows = []
    seen = set()
    for field in fields:
        if not isinstance(field, dict):
            continue
        code = field.get('code')
        if not code or code in seen:
            continue
        seen.add(code)
        raw = values.get(code)
        if raw in (None, '', [], {}):
            continue
        rows.append((labels.get(code, code), _plain(raw)))
    for code, raw in values.items():
        if code in seen or raw in (None, '', [], {}):
            continue
        rows.append((labels.get(code, str(code).replace('_', ' ')), _plain(raw)))
    return rows


def _patient_name(patient):
    surname = (patient.last_name or '').strip().upper()
    given = ' '.join(part for part in (patient.first_name, patient.other_names) if (part or '').strip())
    name = ', '.join(part for part in (surname, given) if part)
    title = (patient.title or '').strip()
    if title and title != '—':
        return f'{title} {name}'.strip()
    return name


def _referring_doctor(visit):
    from referrals.services import report_referring_name
    return report_referring_name(visit)


def _study_title(report, fmt):
    order = report.exam.order
    if order.procedure_id and order.procedure.name:
        return order.procedure.name
    custom = (fmt.get('title_radiology') or '').strip()
    modality = order.modality.get_modality_type_display()
    if custom and custom.lower() not in ('radiology report',):
        return custom
    return f'{modality} report'


def _letterhead_lines(fmt, centre):
    lines = []
    address = (fmt.get('centre_address') or '').replace('\r', '').strip()
    if address:
        lines.extend(part.strip() for part in address.split('\n') if part.strip())
    contact = '  ·  '.join(part for part in (fmt.get('centre_phone'), fmt.get('centre_email')) if part)
    if contact:
        lines.append(contact)
    header = (fmt.get('header_text') or '').strip()
    if header:
        lines.append(header)
    return lines[:5]


def _paint_page(pdf, doc, ctx):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader

    fmt = ctx['fmt']
    font = ctx['font']
    bold = ctx['bold']
    margin = ctx['margin']
    width, height = A4
    pdf.saveState()
    watermark = (fmt.get('watermark_text') or '').strip()
    if watermark:
        pdf.setFont(font, 42)
        pdf.setFillGray(0.92)
        pdf.translate(width / 2, height / 2)
        pdf.rotate(32)
        pdf.drawCentredString(0, 0, watermark[:40])
        pdf.restoreState()
        pdf.saveState()

    if (fmt.get('layout_style') or '') == 'MODERN':
        pdf.setFillColor(ctx['accent'])
        pdf.rect(0, height - 8, width, 8, fill=1, stroke=0)

    y = height - margin - 2
    logo_path = fmt.get('logo_path') or ''
    text_left = margin
    if fmt.get('show_logo') and logo_path:
        abs_logo = os.path.join(settings.MEDIA_ROOT, logo_path)
        if os.path.isfile(abs_logo):
            try:
                pdf.drawImage(
                    ImageReader(abs_logo), margin, y - 16 * 2.834, width=22 * 2.834, height=16 * 2.834,
                    preserveAspectRatio=True, mask='auto', anchor='w',
                )
                text_left = margin + 26 * 2.834
            except Exception:
                text_left = margin

    pdf.setFillColor(ctx['ink'])
    pdf.setFont(bold, 13)
    centre_name = fmt.get('centre_name') or ctx['centre'].name
    pdf.drawString(text_left, y - 12, centre_name[:70])
    pdf.setFillColor(ctx['accent'])
    pdf.setFont(bold, 11)
    pdf.drawRightString(width - margin, y - 12, ctx['title'][:42])
    y -= 28
    pdf.setFillColor(ctx['mute'])
    pdf.setFont(font, 8)
    for line in ctx['header_lines']:
        pdf.drawString(text_left, y, line[:110])
        y -= 11
    rule_y = height - doc.topMargin + 8
    pdf.setStrokeColor(ctx['accent'])
    pdf.setLineWidth(1.2)
    pdf.line(margin, rule_y, width - margin, rule_y)

    footer = (fmt.get('footer_text') or 'Released copies are authorised for clinical use.').strip()
    pdf.setFillColor(ctx['mute'])
    pdf.setFont(font, 7.5)
    pdf.drawString(margin, margin * 0.45, footer[:110])
    pdf.drawRightString(width - margin, margin * 0.45, f'{ctx["visit_id"]}  ·  Page {doc.page}')
    pdf.restoreState()


def _bold_font(font):
    return {
        'Helvetica': 'Helvetica-Bold',
        'Times-Roman': 'Times-Bold',
        'Courier': 'Courier-Bold',
    }.get(font, 'Helvetica-Bold')


def _age_label(dob, on_date):
    if not dob or not on_date:
        return ''
    on_day = on_date.date() if hasattr(on_date, 'date') else on_date
    years = on_day.year - dob.year - ((on_day.month, on_day.day) < (dob.month, dob.day))
    if years >= 2:
        return f'{years} yrs'
    months = (on_day.year - dob.year) * 12 + on_day.month - dob.month
    if on_day.day < dob.day:
        months -= 1
    if months >= 1:
        return f'{months} mo'
    days = (on_day - dob).days
    return f'{max(days, 0)} days'


def _when(value):
    if not value:
        return ''
    if hasattr(value, 'tzinfo') and timezone.is_aware(value):
        value = timezone.localtime(value)
    if hasattr(value, 'strftime') and hasattr(value, 'hour'):
        return value.strftime('%d %b %Y, %H:%M')
    return _date(value)


def _date(value):
    if not value:
        return ''
    return value.strftime('%d %b %Y')


def _plain(value):
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value if item not in (None, ''))
    if isinstance(value, dict):
        return ', '.join(f'{key}: {item}' for key, item in value.items())
    return str(value)


def _image_readable(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        from PIL import Image as PilImage
        with PilImage.open(path) as image:
            image.load()
        return True
    except Exception:
        return False


def _looks_like_html(text):
    return bool(re.search(r'<[a-zA-Z][^>]*>', text or ''))


def _markup(text):
    """Turn stored rich text into the small HTML subset ReportLab paragraphs accept."""
    raw = text or ''
    if not _looks_like_html(raw):
        return escape(raw).replace('\n', '<br/>')
    raw = unescape(raw)
    raw = re.sub(r'(?i)<br\s*/?>', '\n', raw)
    raw = re.sub(r'(?i)</p>\s*', '\n\n', raw)
    raw = re.sub(r'(?i)</div>\s*', '\n', raw)
    raw = re.sub(r'(?i)</tr>\s*', '\n', raw)
    raw = re.sub(r'(?i)</h[1-6]>\s*', '\n', raw)
    raw = re.sub(r'(?i)<li[^>]*>', '• ', raw)
    raw = re.sub(r'(?i)</li>', '\n', raw)
    raw = re.sub(r'(?i)<(strong|b)[^>]*>', '[[B]]', raw)
    raw = re.sub(r'(?i)</(strong|b)>', '[[/B]]', raw)
    raw = re.sub(r'(?i)<(em|i)[^>]*>', '[[I]]', raw)
    raw = re.sub(r'(?i)</(em|i)>', '[[/I]]', raw)
    raw = re.sub(r'(?i)<u[^>]*>', '[[U]]', raw)
    raw = re.sub(r'(?i)</u>', '[[/U]]', raw)
    raw = re.sub(r'<[^>]+>', '', raw)
    raw = escape(raw)
    raw = (
        raw.replace('[[B]]', '<b>').replace('[[/B]]', '</b>')
        .replace('[[I]]', '<i>').replace('[[/I]]', '</i>')
        .replace('[[U]]', '<u>').replace('[[/U]]', '</u>')
    )
    raw = re.sub(r'\n{3,}', '\n\n', raw).strip()
    return raw.replace('\n', '<br/>')
