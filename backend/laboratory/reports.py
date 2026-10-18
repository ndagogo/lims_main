"""Printable laboratory report. Amended results stay marked as amended."""
import io
import os

from django.conf import settings


def build_result_pdf(result):
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
    pdf.setTitle(f'HFDN {sample.sample_id}')

    watermark = (fmt.get('watermark_text') or '').strip()
    if watermark:
        pdf.saveState()
        pdf.setFont(font, 40)
        pdf.setFillGray(0.9)
        pdf.translate(width / 2, height / 2)
        pdf.rotate(35)
        pdf.drawCentredString(0, 0, watermark)
        pdf.restoreState()

    if layout == 'MODERN':
        pdf.setFillColorRGB(*_hex_rgb(fmt.get('accent_color') or '#0f766e'))
        pdf.rect(0, height - 18, width, 18, fill=1, stroke=0)

    logo_drawn = False
    logo_path = fmt.get('logo_path') or ''
    if fmt.get('show_logo') and logo_path:
        abs_logo = os.path.join(settings.MEDIA_ROOT, logo_path)
        if os.path.isfile(abs_logo):
            try:
                pdf.drawImage(ImageReader(abs_logo), margin, y - 42, width=90, height=42, preserveAspectRatio=True, mask='auto')
                logo_drawn = True
            except Exception:
                logo_drawn = False

    text_x = margin + (100 if logo_drawn else 0)
    pdf.setFillColorRGB(*_hex_rgb(fmt.get('primary_color') or '#102033'))
    centre_name = fmt.get('centre_name') or sample.centre.name
    bold = _bold_font(font)
    if layout == 'CLASSIC':
        pdf.setFont(bold, 14)
        pdf.drawCentredString(width / 2, y, centre_name)
        y -= 16
        pdf.setFont(font, size)
        pdf.drawCentredString(width / 2, y, title)
        y -= 14
    else:
        pdf.setFont(bold, 13)
        pdf.drawString(text_x, y, centre_name)
        y -= 14
        pdf.setFont(font, size)
        pdf.drawString(text_x, y, title)
        y -= 12

    if fmt.get('header_text'):
        pdf.setFont(font, max(8, size - 1))
        for chunk in _wrap(fmt['header_text'], 95):
            pdf.drawString(margin, y, chunk)
            y -= 11
    if fmt.get('centre_address'):
        pdf.setFont(font, max(8, size - 1))
        pdf.drawString(margin, y, fmt['centre_address'][:110])
        y -= 11
    contact = ' · '.join(p for p in [fmt.get('centre_phone'), fmt.get('centre_email')] if p)
    if contact:
        pdf.setFont(font, max(8, size - 1))
        pdf.drawString(margin, y, contact[:110])
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
    pdf.setFont(bold, size)
    pdf.drawString(margin, y, 'Parameter')
    pdf.drawString(260, y, 'Value')
    pdf.drawString(360, y, 'Flag')
    pdf.drawString(450, y, 'Range')
    y -= 8
    pdf.line(margin, y, width - margin, y)
    y -= 16
    snap_params = {p.get('parameter_id'): p for p in snap.get('parameters') or []}
    if snap.get('template'):
        pdf.setFont(font, 8)
        tpl = snap['template']
        pdf.drawString(margin, y, f"Template: {tpl.get('name', '')} v{tpl.get('version', '')} (frozen at authentication)")
        y -= 14
    if isinstance(snap.get('template'), dict) and snap['template'].get('report_header'):
        pdf.setFont(f'{font}-Oblique' if font == 'Helvetica' else font, max(8, size - 1))
        for chunk in _wrap(snap['template']['report_header'], 95):
            if y < margin + 72:
                pdf.showPage()
                y = height - margin
            pdf.drawString(margin, y, chunk)
            y -= 12
        y -= 4
    pdf.setFont(font, size)
    for row in result.values.select_related('parameter'):
        if y < margin + 72:
            pdf.showPage()
            y = height - margin
            pdf.setFont(font, size)
        row_snap = snap_params.get(row.parameter_id) or {}
        range_text = row.reference_display or row_snap.get('matched_display') or row.parameter.reference_range or ''
        pdf.drawString(margin, y, (row_snap.get('name') or row.parameter.name)[:32])
        pdf.drawString(260, y, (row.value or '')[:18])
        pdf.drawString(360, y, (row.flag or '')[:16])
        pdf.drawString(450, y, range_text[:18])
        y -= 14
    if result.narrative:
        y -= 10
        pdf.setFont(f'{font}-Oblique' if font == 'Helvetica' else font, size)
        for chunk in _wrap(result.narrative, 90):
            if y < margin + 72:
                pdf.showPage()
                y = height - margin
            pdf.drawString(margin, y, chunk)
            y -= 13

    if fmt.get('show_authenticator_signature', True) and auth:
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

    footer = (fmt.get('footer_text') or '').strip() or 'Released copies are the copies authorised for clinical use. Analyzer import never releases a result.'
    pdf.setFont(font, 8)
    pdf.drawString(margin, 28, footer[:120])
    pdf.save()
    return buffer.getvalue()


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
