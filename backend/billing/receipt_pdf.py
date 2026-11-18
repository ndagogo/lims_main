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
    contact = centre.receipt_contact_line() if hasattr(centre, 'receipt_contact_line') else ' · '.join(
        part for part in (centre.phone, centre.email) if part
    )
    if contact:
        pdf.setFont('Times-Roman', 9)
        for chunk_start in range(0, len(contact), 110):
            pdf.drawString(48, y, contact[chunk_start:chunk_start + 110])
            y -= 12
        y -= 4
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
    from billing.pricing import visit_shows_test_prices
    show_prices = True
    try:
        if visit.show_test_prices is False:
            show_prices = False
        else:
            show_prices = visit_shows_test_prices(visit)
    except Exception:
        show_prices = bool(getattr(visit, 'show_test_prices', False))

    pdf.setFont('Times-Bold', 10)
    pdf.drawString(48, y, 'Description')
    if show_prices:
        pdf.drawRightString(width - 48, y, 'Amount')
    y -= 6
    pdf.line(48, y, width - 48, y)
    y -= 16
    pdf.setFont('Times-Roman', 10)
    items = list(invoice.items.all())
    if not items:
        pdf.drawString(48, y, 'Payment received')
        y -= 16
    for item in items:
        if y < 90:
            pdf.showPage()
            y = height - 54
            pdf.setFont('Times-Roman', 10)
        label = item.description or 'Item'
        pdf.drawString(48, y, label[:70])
        if show_prices:
            pdf.drawRightString(width - 48, y, _money(item.line_total))
        y -= 15
    y -= 6
    pdf.line(300, y, width - 48, y)
    y -= 18
    lines = [
        ('Subtotal', _money(invoice.subtotal)),
    ]
    if invoice.discount_amount:
        lines.append(('Discount', _money(invoice.discount_amount)))
    lines.extend([
        ('Invoice total', _money(invoice.total)),
        ('Total amount paid', _money(payment.amount)),
        ('Method', payment.get_method_display()),
        ('Confirmed payments', _money(paid)),
        ('Balance', _money(balance)),
    ])
    for label, value in lines:
        pdf.setFont('Times-Bold' if label in ('Total amount paid', 'Invoice total', 'Balance') else 'Times-Roman', 10)
        pdf.drawString(300, y, label)
        pdf.drawRightString(width - 48, y, value)
        y -= 14
    if payment.centre_bank_id:
        bank_label = payment.centre_bank.display_label()
        pdf.setFont('Times-Roman', 10)
        pdf.drawString(300, y, 'Bank')
        pdf.drawRightString(width - 48, y, bank_label[:42])
        y -= 14
    y -= 16
    footer = (getattr(centre, 'receipt_footer', '') or '').strip()
    if footer:
        pdf.setFont('Times-Roman', 9)
        for line in footer.splitlines() or [footer]:
            if y < 70:
                pdf.showPage()
                y = height - 54
                pdf.setFont('Times-Roman', 9)
            pdf.drawString(48, y, line[:110])
            y -= 12
        y -= 8
    pdf.setFont('Times-Italic', 8)
    pdf.drawString(48, max(y, 48), 'This receipt confirms money received for the visit above. Keep it with your records.')
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
