"""Line-level management reports for each dashboard section.

Rows use invoices, receipts, samples, and visit numbers. Patient names and UHIDs
are left off these exports.
"""
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from billing.models import InvoiceItem, Payment, Refund
from laboratory.models import Sample
from laboratory.tat import compute_tat
from referrals.models import CommissionLedgerEntry, ReferralAttribution, ReferralDoctor, WithdrawalRequest
from referrals.services import doctor_ids_serving

from .dashboard import _doctor_display_name
from .metrics import _invoice_qs, _payment_qs, _refund_qs, centre_scope, money, period_window

ZERO = Decimal('0.00')
DETAIL_LIMIT = 2000

MANAGEMENT_SECTIONS = (
    ('financial', 'Financial — revenue and collections'),
    ('collections', 'Collections by method'),
    ('clinical', 'Clinical operations — workload and turnaround'),
    ('centres', 'Centres — performance'),
    ('referral', 'Referral network — physicians and partners'),
    ('physicians', 'Top referring physicians'),
    ('introducers', 'Staff who introduced referring doctors'),
)
SECTION_KEYS = {key for key, _label in MANAGEMENT_SECTIONS}
SHEET_TITLES = {
    'financial': 'Financial revenue',
    'collections': 'Collections by method',
    'clinical': 'Clinical operations',
    'centres': 'Centre performance',
    'referral': 'Referral network',
    'physicians': 'Referring physicians',
    'introducers': 'Introducing staff',
}

_BUILDERS = {}


def build_management_detail(user, section_keys, *, period='monthly', centre_id=None, start=None, end=None):
    period, start_dt, end_dt, label = period_window(period, start, end)
    _centres, centre_ids, selected = centre_scope(user, centre_id)
    chosen = [key for key, _label in MANAGEMENT_SECTIONS if key in set(section_keys or [])]
    reports = []
    for key in chosen:
        title, headers, rows, note, truncated = _BUILDERS[key](centre_ids, start_dt, end_dt)
        reports.append({
            'key': key,
            'title': title,
            'sheet': SHEET_TITLES[key],
            'headers': headers,
            'rows': rows,
            'note': note,
            'truncated': truncated,
        })
    return {
        'reports': reports,
        'keys': chosen,
        'period': period,
        'period_label': label,
        'start_date': start_dt.date().isoformat(),
        'end_date': end_dt.date().isoformat(),
        'selected_centre': selected,
    }


def _register(key):
    def wrap(fn):
        _BUILDERS[key] = fn
        return fn
    return wrap


def _when(value):
    if not value:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _person(user):
    if not user:
        return ''
    return (user.get_full_name() or '').strip() or user.username


def _limited(qs):
    rows = list(qs[: DETAIL_LIMIT + 1])
    truncated = len(rows) > DETAIL_LIMIT
    return rows[:DETAIL_LIMIT], truncated


def _outstanding(invoice):
    paid = sum((p.amount for p in invoice.payments.all() if p.status == 'CONFIRMED'), ZERO)
    refunded = sum((r.amount for r in invoice.refunds.all() if r.status == 'COMPLETED'), ZERO)
    net_paid = max(ZERO, paid - refunded)
    return money(max(ZERO, (invoice.total or ZERO) - net_paid))


@_register('financial')
def _financial(centre_ids, start_dt, end_dt):
    headers = [
        'Date', 'Centre', 'Record', 'Document', 'Status',
        'Gross charges', 'Discounts', 'Invoice value', 'Collections', 'Refunds', 'Outstanding',
    ]
    invoices, cut_invoices = _limited(
        _invoice_qs(centre_ids, start_dt, end_dt)
        .select_related('visit__centre')
        .prefetch_related('payments', 'refunds')
        .order_by('created_at', 'invoice_number')
    )
    payments, cut_payments = _limited(
        _payment_qs(centre_ids, start_dt, end_dt)
        .select_related('invoice__visit__centre')
        .order_by('created_at', 'receipt_number')
    )
    refunds, cut_refunds = _limited(
        _refund_qs(centre_ids, start_dt, end_dt)
        .select_related('invoice__visit__centre')
        .order_by('created_at')
    )
    rows = []
    for invoice in invoices:
        rows.append([
            _when(invoice.created_at),
            invoice.visit.centre.code,
            'Invoice',
            invoice.invoice_number,
            invoice.get_status_display(),
            money(invoice.subtotal),
            money(invoice.discount_amount),
            money(invoice.total),
            '',
            '',
            _outstanding(invoice),
        ])
    for payment in payments:
        rows.append([
            _when(payment.created_at),
            payment.invoice.visit.centre.code,
            'Payment',
            payment.receipt_number,
            payment.get_method_display(),
            '',
            '',
            '',
            money(payment.amount),
            '',
            '',
        ])
    for refund in refunds:
        centre = refund.invoice.visit.centre.code if refund.invoice_id else ''
        rows.append([
            _when(refund.created_at),
            centre,
            'Refund',
            refund.invoice.invoice_number if refund.invoice_id else '',
            refund.get_status_display(),
            '',
            '',
            '',
            '',
            money(refund.amount),
            '',
        ])
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    note = (
        'Each invoice, confirmed payment, and completed refund in the period. '
        'Outstanding on an invoice row is the current open balance. Patient names are not included.'
    )
    return (
        'Financial — revenue and collections',
        headers,
        rows,
        note,
        cut_invoices or cut_payments or cut_refunds,
    )


@_register('collections')
def _collections(centre_ids, start_dt, end_dt):
    headers = ['Date', 'Centre', 'Receipt', 'Invoice', 'Method', 'Reference', 'Amount', 'Received by']
    payments, truncated = _limited(
        _payment_qs(centre_ids, start_dt, end_dt)
        .select_related('invoice__visit__centre', 'received_by')
        .order_by('method', 'created_at', 'receipt_number')
    )
    rows = [[
        _when(payment.created_at),
        payment.invoice.visit.centre.code,
        payment.receipt_number,
        payment.invoice.invoice_number,
        payment.get_method_display(),
        payment.reference,
        money(payment.amount),
        _person(payment.received_by),
    ] for payment in payments]
    return (
        'Collections by method',
        headers,
        rows,
        'Confirmed payments received in the period, one row per receipt.',
        truncated,
    )


@_register('clinical')
def _clinical(centre_ids, start_dt, end_dt):
    headers = [
        'Sample', 'Centre', 'Department', 'Test', 'Status',
        'Collected', 'Released', 'Turnaround hours', 'TAT status',
    ]
    open_states = [code for code, _label in Sample.STATUS_CHOICES if code not in Sample.TERMINAL_STATUSES]
    qs = Sample.objects.filter(
        Q(status__in=open_states)
        | Q(result__released_at__gte=start_dt, result__released_at__lte=end_dt)
        | Q(rejected_at__gte=start_dt, rejected_at__lte=end_dt)
    ).select_related('centre', 'order__test', 'result').distinct()
    if centre_ids is not None:
        qs = qs.filter(centre_id__in=centre_ids)
    samples, truncated = _limited(qs.order_by('centre__code', 'sample_id'))
    rows = []
    for sample in samples:
        released_at = sample.result.released_at if getattr(sample, 'result', None) else None
        turnaround = ''
        if sample.collected_at and released_at:
            turnaround = round((released_at - sample.collected_at).total_seconds() / 3600, 1)
        if sample.status in Sample.TERMINAL_STATUSES:
            tat_status = sample.get_status_display()
        else:
            try:
                tat_status = compute_tat(sample)['status']
            except Exception:
                tat_status = ''
        rows.append([
            sample.sample_id,
            sample.centre.code,
            sample.order.test.department,
            sample.order.test.name,
            sample.get_status_display(),
            _when(sample.collected_at),
            _when(released_at),
            turnaround,
            tat_status,
        ])
    return (
        'Clinical operations — workload and turnaround',
        headers,
        rows,
        'Open samples, samples released in the period, and samples rejected in the period. Patient names are not included.',
        truncated,
    )


@_register('centres')
def _centres(centre_ids, start_dt, end_dt):
    headers = [
        'Date', 'Centre', 'Invoice', 'Service group', 'Item', 'Quantity', 'Line total',
        'Invoice gross', 'Invoice discount', 'Invoice value',
    ]
    qs = InvoiceItem.objects.exclude(invoice__status='CANCELLED').filter(
        invoice__created_at__gte=start_dt, invoice__created_at__lte=end_dt,
    ).select_related('invoice__visit__centre', 'test__service_group')
    if centre_ids is not None:
        qs = qs.filter(invoice__visit__centre_id__in=centre_ids)
    items, truncated = _limited(qs.order_by('invoice__visit__centre__code', 'invoice__invoice_number', 'created_at'))
    rows = []
    for item in items:
        group = '—'
        if item.test_id and item.test.service_group_id:
            group = item.test.service_group.name
        elif item.imaging_order_id:
            group = 'Imaging'
        rows.append([
            _when(item.invoice.created_at),
            item.invoice.visit.centre.code,
            item.invoice.invoice_number,
            group,
            item.description,
            item.quantity,
            money(item.line_total),
            money(item.invoice.subtotal),
            money(item.invoice.discount_amount),
            money(item.invoice.total),
        ])
    return (
        'Centres — performance',
        headers,
        rows,
        'Each billed line in the period, with the centre and service group it belongs to.',
        truncated,
    )


def _physician_name(doctor):
    if doctor.is_default_placeholder:
        return 'DEFAULT DOCTOR'
    return _doctor_display_name(doctor)


@_register('referral')
def _referral(centre_ids, start_dt, end_dt):
    headers = ['Date', 'Centre', 'Record', 'Doctor code', 'Physician', 'Document', 'Detail', 'Amount']
    attrs = ReferralAttribution.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('doctor', 'visit__centre')
    ledger = CommissionLedgerEntry.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('doctor', 'invoice__visit__centre')
    withdrawals = WithdrawalRequest.objects.filter(status='REQUESTED').select_related('doctor__centre')
    if centre_ids is not None:
        serving = doctor_ids_serving(centre_ids)
        attrs = attrs.filter(visit__centre_id__in=centre_ids)
        ledger = ledger.filter(doctor_id__in=serving)
        withdrawals = withdrawals.filter(doctor_id__in=serving)
    attr_rows, cut_attrs = _limited(attrs.order_by('created_at'))
    ledger_rows, cut_ledger = _limited(ledger.order_by('created_at'))
    withdrawal_rows, cut_withdrawals = _limited(withdrawals.order_by('requested_at'))
    rows = []
    for attr in attr_rows:
        rows.append([
            _when(attr.created_at),
            attr.visit.centre.code,
            'Referral visit',
            attr.doctor.doctor_code,
            _physician_name(attr.doctor),
            attr.visit.visit_id,
            attr.note,
            '',
        ])
    for entry in ledger_rows:
        centre = ''
        document = ''
        if entry.invoice_id:
            centre = entry.invoice.visit.centre.code
            document = entry.invoice.invoice_number
        rows.append([
            _when(entry.created_at),
            centre,
            entry.get_entry_type_display(),
            entry.doctor.doctor_code,
            _physician_name(entry.doctor),
            document,
            entry.description,
            money(entry.amount),
        ])
    for request in withdrawal_rows:
        rows.append([
            _when(request.requested_at),
            request.doctor.centre.code if request.doctor.centre_id else '',
            'Withdrawal requested',
            request.doctor.doctor_code,
            _physician_name(request.doctor),
            '',
            request.get_status_display(),
            money(request.amount),
        ])
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    return (
        'Referral network — physicians and partners',
        headers,
        rows,
        'Referral visits and commission ledger lines in the period, plus withdrawal requests still awaiting payout. Patient names are not included.',
        cut_attrs or cut_ledger or cut_withdrawals,
    )


@_register('physicians')
def _physicians(centre_ids, start_dt, end_dt):
    headers = [
        'Date', 'Centre', 'Doctor code', 'Physician', 'Visit',
        'Invoice value', 'Accruals', 'Reversals', 'Settlements', 'Net commission',
    ]
    qs = ReferralAttribution.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('doctor', 'visit__centre').prefetch_related('visit__invoices__commission_entries')
    if centre_ids is not None:
        qs = qs.filter(visit__centre_id__in=centre_ids)
    attrs, truncated = _limited(qs.order_by('-created_at'))
    rows = []
    for attr in attrs:
        invoices = [invoice for invoice in attr.visit.invoices.all() if invoice.status != 'CANCELLED']
        value = money(sum((invoice.total or ZERO for invoice in invoices), ZERO))
        buckets = {'ACCRUAL': ZERO, 'REVERSAL': ZERO, 'SETTLEMENT': ZERO, 'ADJUSTMENT': ZERO}
        for invoice in invoices:
            for entry in invoice.commission_entries.all():
                if entry.created_at < start_dt or entry.created_at > end_dt:
                    continue
                if entry.entry_type in buckets:
                    buckets[entry.entry_type] += entry.amount
        net = money(sum(buckets.values(), ZERO))
        rows.append([
            _when(attr.created_at),
            attr.visit.centre.code,
            attr.doctor.doctor_code,
            _physician_name(attr.doctor),
            attr.visit.visit_id,
            value,
            money(buckets['ACCRUAL']),
            money(buckets['REVERSAL']),
            money(buckets['SETTLEMENT']),
            net,
        ])
    return (
        'Top referring physicians',
        headers,
        rows,
        'Every referral visit in the period, with that visit’s invoice value and commission. This is not limited to the top 20 shown on the dashboard.',
        truncated,
    )


@_register('introducers')
def _introducers(centre_ids, start_dt, end_dt):
    headers = ['Staff', 'Username', 'Doctor code', 'Doctor', 'Visit date', 'Centre', 'Visit']
    doctors = ReferralDoctor.objects.filter(
        is_default_placeholder=False, onboarded_by__isnull=False,
    ).select_related('onboarded_by', 'centre').order_by('onboarded_by__last_name', 'last_name', 'first_name')
    if centre_ids is not None:
        doctors = doctors.filter(pk__in=doctor_ids_serving(centre_ids))
    doctors, truncated = _limited(doctors)
    visits = ReferralAttribution.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
        doctor_id__in=[doctor.pk for doctor in doctors],
    ).select_related('visit__centre', 'doctor')
    if centre_ids is not None:
        visits = visits.filter(visit__centre_id__in=centre_ids)
    by_doctor = {}
    for attr in visits:
        by_doctor.setdefault(attr.doctor_id, []).append(attr)
    rows = []
    for doctor in doctors:
        staff = doctor.onboarded_by
        staff_name = (staff.get_full_name() or '').strip() or staff.username
        matched = by_doctor.get(doctor.pk) or [None]
        for attr in matched:
            rows.append([
                staff_name,
                staff.username,
                doctor.doctor_code,
                _doctor_display_name(doctor),
                _when(attr.created_at) if attr else '',
                attr.visit.centre.code if attr else '',
                attr.visit.visit_id if attr else '',
            ])
    return (
        'Staff who introduced referring doctors',
        headers,
        rows,
        'Each referring doctor is named. A visit row is added for every referral that doctor sent in the period.',
        truncated,
    )
