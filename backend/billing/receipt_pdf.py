"""Patient-facing payment receipt PDF."""
import io
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _money(value):
    amount = value if isinstance(value, Decimal) else Decimal(value or 0)
    return f'NGN {amount:,.2f}'


def build_receipt_pdf(payment):
    from core.organization import organization_legal_name
    invoice = payment.invoice
    visit = invoice.visit
    patient = visit.patient
    centre = visit.centre
    confirmed = invoice.payments.filter(status='CONFIRMED')
    paid = sum((row.amount for row in confirmed), Decimal('0'))
    balance = max(Decimal('0'), (invoice.total or Decimal('0')) - paid)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setTitle(payment.receipt_number or 'Receipt')
    y = height - 54
    pdf.setFont('Times-Bold', 16)
    pdf.drawString(48, y, organization_legal_name() or centre.name)
    y -= 18
    pdf.setFont('Times-Roman', 11)
    pdf.drawString(48, y, centre.name)
    y -= 14
    if centre.address:
        pdf.setFont('Times-Roman', 9)
        pdf.drawString(48, y, centre.address.replace('\n', ', ')[:110])
        y -= 12
    contact = ' · '.join(part for part in (centre.phone, centre.email) if part)
    if contact:
        pdf.setFont('Times-Roman', 9)
        pdf.drawString(48, y, contact[:110])
        y -= 16
    pdf.setStrokeColorRGB(0.09, 0.09, 0.09)
    pdf.setLineWidth(1.2)
    pdf.line(48, y, width - 48, y)
    y -= 28
    pdf.setFont('Times-Bold', 14)
    pdf.drawString(48, y, 'Payment receipt')
    pdf.setFont('Times-Bold', 12)
    pdf.drawRightString(width - 48, y, payment.receipt_number or '')
    y -= 16
    pdf.setFont('Times-Roman', 10)
    pdf.drawRightString(width - 48, y, payment.created_at.strftime('%d %b %Y, %H:%M'))
    y -= 28
    if payment.status == 'REVERSED':
        pdf.setFillColorRGB(0.6, 0.15, 0.15)
        pdf.setFont('Times-Bold', 12)
        pdf.drawString(48, y, 'VOID — PAYMENT REVERSED')
        pdf.setFillColorRGB(0, 0, 0)
        y -= 22
    pdf.setFont('Times-Bold', 10)
    pdf.drawString(48, y, 'Received from')
    pdf.drawString(300, y, 'Visit')
    y -= 14
    pdf.setFont('Times-Roman', 10)
    pdf.drawString(48, y, f'{patient.first_name} {patient.last_name}'.strip())
    pdf.drawString(300, y, f'{visit.visit_id} · {centre.code}')
    y -= 13
    pdf.drawString(48, y, patient.uhid or '')
    pdf.drawString(300, y, f'Invoice {invoice.invoice_number}')
    y -= 13
    phone = ' '.join(part for part in (patient.phone_country_code, patient.phone_number) if part)
    if phone:
        pdf.drawString(48, y, phone)
        y -= 13
    y -= 10
    pdf.setFont('Times-Bold', 10)
    pdf.drawString(48, y, 'Description')
    pdf.drawRightString(width - 48, y, 'Amount')
    y -= 6
    pdf.line(48, y, width - 48, y)
    y -= 16
    pdf.setFont('Times-Roman', 10)
    items = list(invoice.items.all())
    if not items:
        pdf.drawString(48, y, 'Payment received')
        pdf.drawRightString(width - 48, y, _money(payment.amount))
        y -= 16
    for item in items:
        if y < 90:
            pdf.showPage()
            y = height - 54
            pdf.setFont('Times-Roman', 10)
        label = item.description or 'Item'
        pdf.drawString(48, y, label[:70])
        pdf.drawRightString(width - 48, y, _money(item.line_total))
        y -= 15
    y -= 6
    pdf.line(300, y, width - 48, y)
    y -= 18
    lines = [
        ('This receipt', _money(payment.amount)),
        ('Method', payment.get_method_display()),
        ('Invoice total', _money(invoice.total)),
        ('Confirmed payments', _money(paid)),
        ('Balance', _money(balance)),
    ]
    for label, value in lines:
        pdf.setFont('Times-Bold' if label in ('This receipt', 'Balance') else 'Times-Roman', 10)
        pdf.drawString(300, y, label)
        pdf.drawRightString(width - 48, y, value)
        y -= 14
    y -= 16
    pdf.setFont('Times-Italic', 8)
    pdf.drawString(48, max(y, 48), 'This receipt confirms money received for the visit above. Keep it with your records.')
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
