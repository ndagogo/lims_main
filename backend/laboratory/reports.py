"""Printable laboratory report. Amended results stay marked as amended."""
import io


def build_result_pdf(result):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    sample = result.sample
    patient = sample.order.visit.patient
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 48
    pdf.setTitle(f'HFDN {sample.sample_id}')
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(48, y, 'Health Focus Diagnostics Nigeria')
    y -= 18
    pdf.setFont('Helvetica', 10)
    pdf.drawString(48, y, f'{sample.centre.name}  ·  Laboratory report')
    y -= 22
    if result.status == 'AMENDED':
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawString(48, y, 'AMENDED — this copy is not the current released report.')
        y -= 18
    pdf.setFont('Helvetica', 10)
    lines = [
        f'Patient: {patient.first_name} {patient.last_name}    UHID: {patient.uhid}',
        f'Visit: {sample.order.visit.visit_id}    Sample: {sample.sample_id}',
        f'Test: {sample.order.test.name}    Status: {result.status}    Version: {result.version}',
    ]
    for line in lines:
        pdf.drawString(48, y, line[:110])
        y -= 14
    y -= 8
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(48, y, 'Parameter')
    pdf.drawString(260, y, 'Value')
    pdf.drawString(360, y, 'Flag')
    pdf.drawString(450, y, 'Range')
    y -= 8
    pdf.line(48, y, width - 48, y)
    y -= 16
    pdf.setFont('Helvetica', 10)
    for row in result.values.select_related('parameter'):
        if y < 72:
            pdf.showPage()
            y = height - 48
            pdf.setFont('Helvetica', 10)
        pdf.drawString(48, y, row.parameter.name[:32])
        pdf.drawString(260, y, (row.value or '')[:18])
        pdf.drawString(360, y, (row.flag or '')[:16])
        pdf.drawString(450, y, (row.parameter.reference_range or '')[:18])
        y -= 14
    if result.narrative:
        y -= 10
        pdf.setFont('Helvetica-Oblique', 10)
        for chunk in _wrap(result.narrative, 90):
            if y < 72:
                pdf.showPage()
                y = height - 48
            pdf.drawString(48, y, chunk)
            y -= 13
    pdf.setFont('Helvetica', 8)
    pdf.drawString(48, 36, 'Released copies are the copies authorised for clinical use. Analyzer import never releases a result.')
    pdf.save()
    return buffer.getvalue()


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
