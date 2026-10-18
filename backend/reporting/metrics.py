"""Precise financial and operational metrics from transactional records.

Definitions (do not conflate):
- gross_charges: Sum of Invoice.subtotal for non-cancelled invoices created in period
  (line value before discounts).
- discounts: Sum of Invoice.discount_amount on those invoices (or approved DiscountRequest
  applied_amount when measuring request workflow).
- invoice_value: Sum of Invoice.total (= gross − discount) for non-cancelled invoices in period.
- collections: Sum of confirmed Payment.amount received in period (cash collected).
- refunds: Sum of completed Refund.amount in period.
- net_receipts: collections − refunds.
- outstanding_receivables: For open UNPAID/PART_PAID invoices, total − (confirmed payments −
  completed refunds), matching Invoice.recalculate net_paid logic. Snapshot, not period-scoped
  unless invoices were created in the period.
"""
from decimal import Decimal

from django.db.models import Count, Sum

from billing.models import Invoice, Payment, Refund
from billing.reports import PERIOD_CHOICES, _centre_scope, resolve_period
from core.models import Centre

ZERO = Decimal('0.00')


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal('0.01'))


def centre_scope(user, centre_id=None):
    """Active centres the user may view; optional single-centre filter."""
    return _centre_scope(user, centre_id)


def period_window(period='monthly', start=None, end=None):
    return resolve_period(period, start, end)


def _invoice_qs(centre_ids, start_dt=None, end_dt=None):
    qs = Invoice.objects.exclude(status='CANCELLED')
    if centre_ids is not None:
        qs = qs.filter(visit__centre_id__in=centre_ids)
    if start_dt is not None:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt is not None:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


def _payment_qs(centre_ids, start_dt, end_dt):
    qs = Payment.objects.filter(status='CONFIRMED', created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        qs = qs.filter(invoice__visit__centre_id__in=centre_ids)
    return qs


def _refund_qs(centre_ids, start_dt, end_dt):
    qs = Refund.objects.filter(status='COMPLETED', created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        qs = qs.filter(invoice__visit__centre_id__in=centre_ids)
    return qs


def financial_kpis(user, *, period='monthly', centre_id=None, start=None, end=None):
    """Period financial measures from Invoice / Payment / Refund rows."""
    period, start_dt, end_dt, label = period_window(period, start, end)
    centres, centre_ids, selected = centre_scope(user, centre_id)
    invoices = _invoice_qs(centre_ids, start_dt, end_dt)
    payments = _payment_qs(centre_ids, start_dt, end_dt)
    refunds = _refund_qs(centre_ids, start_dt, end_dt)

    gross_charges = money(invoices.aggregate(t=Sum('subtotal'))['t'])
    discounts = money(invoices.aggregate(t=Sum('discount_amount'))['t'])
    invoice_value = money(invoices.aggregate(t=Sum('total'))['t'])
    collections = money(payments.aggregate(t=Sum('amount'))['t'])
    refunds_total = money(refunds.aggregate(t=Sum('amount'))['t'])
    net_receipts = money(collections - refunds_total)
    outstanding = outstanding_receivables(centre_ids)

    payments_by_method = [
        {'method': row['method'], 'count': row['count'], 'total': money(row['total'])}
        for row in payments.values('method').annotate(count=Count('id'), total=Sum('amount')).order_by('method')
    ]

    return {
        'period': period,
        'period_label': label,
        'start': start_dt,
        'end': end_dt,
        'start_date': start_dt.date().isoformat(),
        'end_date': end_dt.date().isoformat(),
        'centres': centres,
        'selected_centre': selected,
        'centre_id': str(selected.pk) if selected else '',
        'period_choices': PERIOD_CHOICES,
        'definitions': {
            'gross_charges': 'Invoice line value before discounts (sum of subtotal).',
            'discounts': 'Discounts recorded on invoices in the period.',
            'invoice_value': 'Amount billed after discounts (sum of invoice total).',
            'collections': 'Confirmed payments received in the period (cash collected).',
            'refunds': 'Completed refunds in the period.',
            'net_receipts': 'Collections minus refunds.',
            'outstanding_receivables': 'Open invoice balances after net paid (payments − refunds).',
        },
        'kpis': {
            'gross_charges': gross_charges,
            'discounts': discounts,
            'invoice_value': invoice_value,
            'collections': collections,
            'refunds': refunds_total,
            'net_receipts': net_receipts,
            'outstanding_receivables': outstanding,
            'payment_count': payments.count(),
            'invoice_count': invoices.count(),
            'refund_count': refunds.count(),
        },
        'payments_by_method': payments_by_method,
    }


def outstanding_receivables(centre_ids=None) -> Decimal:
    """Snapshot of open balances using net_paid = confirmed payments − completed refunds."""
    qs = Invoice.objects.filter(status__in=['UNPAID', 'PART_PAID']).prefetch_related('payments', 'refunds')
    if centre_ids is not None:
        qs = qs.filter(visit__centre_id__in=centre_ids)
    total = ZERO
    for invoice in qs:
        paid = sum((p.amount for p in invoice.payments.all() if p.status == 'CONFIRMED'), ZERO)
        refunded = sum((r.amount for r in invoice.refunds.all() if r.status == 'COMPLETED'), ZERO)
        net_paid = max(ZERO, paid - refunded)
        total += max(ZERO, (invoice.total or ZERO) - net_paid)
    return money(total)


def visible_centres(user):
    centres = Centre.objects.filter(is_active=True).order_by('name')
    if not user.is_superuser:
        centres = centres.filter(user_access__user=user, user_access__can_view=True).distinct()
    return centres
