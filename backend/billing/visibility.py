"""Department queues stay empty until the visit's invoice is paid in full.

A row with no invoice is left visible so work started before this paypoint rule
can still be finished. A row billed by reception is hidden while that invoice is
unpaid or only part paid.
"""
from django.db.models import Exists, OuterRef

from billing.models import InvoiceItem


def settled(qs, field, outer='pk'):
    linked = InvoiceItem.objects.filter(**{field: OuterRef(outer)})
    paid = linked.filter(invoice__status='PAID')
    return qs.annotate(_hfdn_billed=Exists(linked), _hfdn_paid=Exists(paid)).exclude(_hfdn_billed=True, _hfdn_paid=False)


def imaging_payment_complete(*, order_id=None, exam_id=None, report_id=None):
    items = InvoiceItem.objects.all()
    if order_id:
        items = items.filter(imaging_order_id=order_id)
    elif exam_id:
        items = items.filter(imaging_order__exam__pk=exam_id)
    elif report_id:
        items = items.filter(imaging_order__exam__report__pk=report_id)
    else:
        return False
    if not items.exists():
        return True
    return items.filter(invoice__status='PAID').exists()
