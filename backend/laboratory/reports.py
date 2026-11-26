"""Printable laboratory report. Amended results stay marked as amended."""
import io
import os

from django.conf import settings


def build_result_pdf(result, *, letterhead=True):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    sample = result.sample
    patient = sample.order.visit.patient
    snap = result.configuration_snapshot or {}
    fmt = snap.get('format') or {}
    auth = snap.get('authenticator') or {}
    margin = max(12, int(fmt.get('margin_mm') or 18)) * 2.834
    font = fmt.get('font_family') or 'Helvetica'
    size = int(fmt.get('body_font_size') or 10)
    title = fmt.get('title_lab') or 'Laboratory Report'
    layout = fmt.get('layout_style') or 'CLASSIC'

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - margin
    from core.organization import organization_short_name
    pdf.setTitle(f'{organization_short_name()} {sample.sample_id}')

    watermark = (fmt.get('watermark_text') or '').strip()
    if watermark:
        pdf.saveState()
        pdf.setFont(font, 40)
        pdf.setFillGray(0.9)
        pdf.translate(width / 2, height / 2)
        pdf.rotate(35)
        pdf.drawCentredString(0, 0, watermark)
        pdf.restoreState()

    from core.report_stationery import draw_fitted_image, image_file

    bold = _bold_font(font)
    mode = (fmt.get('header_mode') or '').upper()
    if not mode:
        mode = 'LETTERHEAD' if fmt.get('header_file') else 'COMPOSED'
    header_image = ''
    if letterhead and mode == 'LETTERHEAD':
        header_image = image_file(fmt, 'header_file')
        if header_image and draw_fitted_image(pdf, header_image, margin, y - 64, width - (2 * margin), 64):
            y -= 72
            pdf.setFillColorRGB(*_hex_rgb(fmt.get('primary_color') or '#102033'))
            pdf.setFont(bold, 12)
            pdf.drawCentredString(width / 2, y, title)
            y -= 16
        else:
            header_image = ''
    if letterhead and not header_image:
        if layout == 'MODERN':
            pdf.setFillColorRGB(*_hex_rgb(fmt.get('accent_color') or '#0f766e'))
            pdf.rect(0, height - 18, width, 18, fill=1, stroke=0)

        logo_path = fmt.get('logo_path') or ''
        if fmt.get('show_logo') and logo_path:
            abs_logo = os.path.join(settings.MEDIA_ROOT, logo_path)
            if os.path.isfile(abs_logo):
                try:
                    pdf.drawImage(
                        ImageReader(abs_logo),
                        _aligned_x(fmt.get('logo_align'), margin, width, 90),
                        y - 42, width=90, height=42, preserveAspectRatio=True, mask='auto',
                    )
                    y -= 48
                except Exception:
                    pass

        pdf.setFillColorRGB(*_hex_rgb(fmt.get('primary_color') or '#102033'))
        centre_name = fmt.get('centre_name') or sample.centre.name
        name_align = 'CENTER' if layout == 'CLASSIC' else 'LEFT'
        _draw_aligned(pdf, name_align, margin, width, y, centre_name, bold, 14 if layout == 'CLASSIC' else 13)
        y -= 16
        _draw_aligned(pdf, name_align, margin, width, y, title, font, size)
        y -= 14
        if fmt.get('header_text'):
            for chunk in _wrap(fmt['header_text'], 95):
                _draw_aligned(pdf, 'CENTER', margin, width, y, chunk, font, max(8, size - 1))
                y -= 11
        if fmt.get('centre_address'):
            _draw_aligned(
                pdf, fmt.get('address_align') or 'CENTER', margin, width, y,
                fmt['centre_address'][:110], font, max(8, size - 1),
            )
            y -= 11
        contact = ' · '.join(p for p in [fmt.get('centre_phone'), fmt.get('centre_email')] if p)
        if contact:
            _draw_aligned(
                pdf, fmt.get('contact_align') or 'CENTER', margin, width, y,
                contact[:110], font, max(8, size - 1),
            )
            y -= 11
    y -= 6

    pdf.setFillColorRGB(0, 0, 0)
    if result.status == 'AMENDED':
        pdf.setFont(bold, 11)
        pdf.drawString(margin, y, 'AMENDED — this copy is not the current released report.')
        y -= 18
    pdf.setFont(font, size)
    from referrals.services import report_referring_name
    referred = report_referring_name(sample.order.visit)
    lines = [
        f'Patient: {patient.first_name} {patient.last_name}    UHID: {patient.uhid}',
        f'Visit: {sample.order.visit.visit_id}    Sample: {sample.sample_id}',
        f'Test: {sample.order.test.name}    Status: {result.status}    Version: {result.version}',
    ]
    if referred:
        lines.append(f'Referred by: {referred}')
    for line in lines:
        pdf.drawString(margin, y, line[:110])
        y -= 14
    y -= 8
    tpl = snap.get('template') if isinstance(snap.get('template'), dict) else {}
    if tpl:
        pdf.setFont(font, 8)
        label = tpl.get('format_code') or tpl.get('name') or ''
        pdf.drawString(margin, y, f"Format: {label} v{tpl.get('version', '')}".strip()[:110])
        y -= 14
    if tpl.get('report_header'):
        pdf.setFont(f'{font}-Oblique' if font == 'Helvetica' else font, max(8, size - 1))
        for chunk in _wrap(tpl['report_header'], 95):
            if y < margin + 72:
                pdf.showPage()
                y = height - margin
            pdf.drawString(margin, y, chunk)
            y -= 12
        y -= 4
    captions = (
        tpl.get('column_caption_1') or 'Test',
        tpl.get('column_caption_2') or 'Result',
        tpl.get('column_caption_3') or 'Units',
        tpl.get('column_caption_4') or 'Biological Reference Interval',
    )
    report_title = (tpl.get('report_title') or '').strip()
    if report_title:
        pdf.setFont(bold, size + 1)
        pdf.drawString(margin, y, report_title[:90])
        y -= 16
    pdf.setFont(bold, max(8, size - 1))
    value_x = margin + 190
    unit_x = margin + 300
    range_x = margin + 360
    pdf.drawString(margin, y, captions[0][:24])
    pdf.drawString(value_x, y, captions[1][:16])
    pdf.drawString(unit_x, y, captions[2][:12])
    pdf.drawString(range_x, y, captions[3][:28])
    y -= 8
    pdf.line(margin, y, width - margin, y)
    y -= 16
    pdf.setFont(font, size)
    for row_snap, row in _ordered_result_rows(result, snap):
        if row_snap.get('include_in_report') is False:
            continue
        if row is not None and row.show_on_report is False:
            continue
        if y < margin + 72:
            pdf.showPage()
            y = height - margin
            pdf.setFont(font, size)
        subtitle = (row_snap.get('section_label') or '').strip()
        if subtitle:
            pdf.setFont(bold, size)
            pdf.drawString(margin, y, subtitle[:80])
            y -= 14
            pdf.setFont(font, size)
        name = row_snap.get('name') or (row.parameter.name if row else '')
        value = (row.value if row else row_snap.get('value')) or ''
        flag = (row.flag if row else row_snap.get('flag')) or ''
        unit = row_snap.get('unit') or (row.parameter.unit if row else '')
        range_text = ''
        if row_snap.get('show_reference_range', True):
            range_text = (row.reference_display if row else '') or row_snap.get('matched_display') or ''
        name_font = bold if row_snap.get('parameter_in_bold') else font
        name_size = size + (1 if row_snap.get('text_size') == 'BIG' else -1 if row_snap.get('text_size') == 'SMALL' else 0)
        pdf.setFont(name_font, max(8, name_size))
        pdf.drawString(margin, y, name[:28])
        pdf.setFont(font, size)
        shown = value[:16] + (f' {flag}' if flag else '')
        pdf.drawString(value_x, y, shown[:18])
        pdf.drawString(unit_x, y, (unit or '')[:12])
        pdf.drawString(range_x, y, range_text[:28])
        y -= 12
        method = (row_snap.get('method_name') or '').strip()
        if not method and row is not None:
            method = (getattr(row.parameter, 'method_name', '') or '').strip()
        if method:
            pdf.setFont(font, max(7, size - 2))
            pdf.drawString(margin, y, f'Method : {method}'[:48])
            y -= 11
            pdf.setFont(font, size)
        else:
            y -= 2
    interpretation = (tpl.get('interpretation') or '').strip()
    if interpretation:
        y -= 8
        pdf.setFont(font, max(8, size - 1))
        for chunk in _wrap(interpretation, 95):
            if y < margin + 72:
                pdf.showPage()
                y = height - margin
            pdf.drawString(margin, y, chunk)
            y -= 12
    if result.narrative:
        y -= 10
        pdf.setFont(f'{font}-Oblique' if font == 'Helvetica' else font, size)
        for chunk in _wrap(result.narrative, 90):
            if y < margin + 72:
                pdf.showPage()
                y = height - margin
            pdf.drawString(margin, y, chunk)
            y -= 13

    if result.status in ('AUTHENTICATED', 'RELEASED', 'AMENDED'):
        if not auth and getattr(result, 'authenticated_by_id', None):
            from core.report_format import signature_snapshot
            auth = signature_snapshot(result.authenticated_by)
    if auth and result.status in ('AUTHENTICATED', 'RELEASED', 'AMENDED'):
        if y < margin + 110:
            pdf.showPage()
            y = height - margin
        y -= 14
        pdf.setFont(font, max(8, size - 1))
        pdf.drawString(margin, y, fmt.get('signature_label') or 'Authenticated by')
        y -= 8
        sig_path = auth.get('signature_path') or ''
        if sig_path:
            abs_sig = os.path.join(settings.MEDIA_ROOT, sig_path)
            if os.path.isfile(abs_sig):
                try:
                    pdf.drawImage(ImageReader(abs_sig), margin, y - 48, width=140, height=48, preserveAspectRatio=True, mask='auto')
                    y -= 52
                except Exception:
                    y -= 4
        pdf.setFont(bold, size)
        pdf.drawString(margin, y, auth.get('name') or '')
        y -= 12
        if auth.get('title'):
            pdf.setFont(font, max(8, size - 1))
            pdf.drawString(margin, y, auth['title'])
            y -= 12
        if auth.get('qualifications'):
            pdf.setFont(font, max(8, size - 1))
            pdf.drawString(margin, y, auth['qualifications'][:90])
            y -= 12
        if auth.get('license_number'):
            pdf.setFont(font, max(8, size - 1))
            pdf.drawString(margin, y, f"Reg. No: {auth['license_number']}"[:90])

    if letterhead:
        footer = (fmt.get('footer_text') or '').strip() or 'Released copies are the copies authorised for clinical use. Analyzer import never releases a result.'
        footer_image = image_file(fmt, 'footer_file')
        text_y = 28
        if footer_image and draw_fitted_image(pdf, footer_image, margin, 22, width - (2 * margin), 36):
            text_y = 62
        pdf.setFont(font, 8)
        pdf.drawString(margin, text_y, footer[:120])
    pdf.save()
    raw = buffer.getvalue()
    if not letterhead:
        return raw
    from core.report_stationery import apply_pdf_stationery
    return apply_pdf_stationery(raw, fmt)


def result_table(result):
    """Column captions and ordered rows from the test format frozen on the result."""
    snap = result.configuration_snapshot or {}
    tpl = snap.get('template') if isinstance(snap.get('template'), dict) else {}
    rows = []
    for item, row in _ordered_result_rows(result, snap):
        rows.append({
            'subtitle': (item.get('section_label') or '').strip(),
            'name': item.get('name') or (row.parameter.name if row else ''),
            'method': (item.get('method_name') or (getattr(row.parameter, 'method_name', '') if row else '') or ''),
            'value': (row.value if row else item.get('value')) or '',
            'flag': (row.flag if row else item.get('flag')) or '',
            'unit': item.get('unit') or (row.parameter.unit if row else ''),
            'reference': ((row.reference_display if row else '') or item.get('matched_display') or ''),
            'bold': bool(item.get('parameter_in_bold')),
        })
    test_name = result.sample.order.test.name
    return {
        'title': (tpl.get('report_title') or '').strip() or test_name,
        'captions': [
            tpl.get('column_caption_1') or 'Test',
            tpl.get('column_caption_2') or 'Result',
            tpl.get('column_caption_3') or 'Units',
            tpl.get('column_caption_4') or 'Biological Reference Interval',
        ],
        'header': tpl.get('report_header') or '',
        'footer': tpl.get('report_footer') or '',
        'interpretation': tpl.get('interpretation') or '',
        'rows': rows,
    }


def _ordered_result_rows(result, snap):
    """Print in the mapped format sequence. Values outside that format follow."""
    values = {row.parameter_id: row for row in result.values.select_related('parameter')}
    ordered = []
    seen = set()
    for item in snap.get('parameters') or []:
        parameter_id = item.get('parameter_id')
        seen.add(parameter_id)
        if item.get('include_in_report') is False:
            continue
        ordered.append((item, values.get(parameter_id)))
    for row in result.values.select_related('parameter'):
        if row.parameter_id not in seen:
            ordered.append(({}, row))
    return ordered


def _aligned_x(align, margin, width, block_width):
    if (align or '').upper() == 'RIGHT':
        return width - margin - block_width
    if (align or '').upper() == 'CENTER':
        return max(margin, (width - block_width) / 2)
    return margin


def _draw_aligned(pdf, align, margin, width, y, text, font_name, size):
    pdf.setFont(font_name, size)
    shown = (text or '')[:110]
    align = (align or 'LEFT').upper()
    if align == 'CENTER':
        pdf.drawCentredString(width / 2, y, shown)
    elif align == 'RIGHT':
        pdf.drawRightString(width - margin, y, shown)
    else:
        pdf.drawString(margin, y, shown)


def _bold_font(font):
    return {
        'Helvetica': 'Helvetica-Bold',
        'Times-Roman': 'Times-Bold',
        'Courier': 'Courier-Bold',
    }.get(font, 'Helvetica-Bold')


def _hex_rgb(value):
    raw = (value or '#102033').lstrip('#')
    if len(raw) != 6:
        raw = '102033'
    return tuple(int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _wrap(text, size):
    words = (text or '').split()
    line = ''
    for word in words:
        trial = f'{line} {word}'.strip()
        if len(trial) > size:
            if line:
                yield line
            line = word
        else:
            line = trial
    if line:
        yield line
