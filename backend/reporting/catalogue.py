"""Report catalogue builders — dimensional revenue and operational analytics.

Management aggregates avoid patient names/UHIDs. Detail statement rows that need
identifiers are marked include_patient_ids=True for audited statement exports only.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from billing.models import Invoice, InvoiceItem, Payment
from inventory.models import ReagentSku, StockMovement
from laboratory.models import LabOrder
from patients.models import Visit
from radiology.models import ImagingOrder, RadiologyReport
from referrals.models import CommissionLedgerEntry, ReferralAttribution

from .metrics import centre_scope, money, period_window

ZERO = Decimal('0.00')

CATALOGUE_REPORTS = (
    ('revenue_centre', 'Revenue by centre'),
    ('revenue_physician', 'Revenue by referral physician'),
    ('revenue_partner', 'Revenue by corporate / HMO partner'),
    ('revenue_service_group', 'Revenue by service group'),
    ('revenue_test', 'Revenue by test'),
    ('revenue_department', 'Revenue by department'),
    ('commission', 'Referral commission accruals / reversals / payouts'),
    ('hmo_statements', 'HMO & credit-client statements'),
    ('demographics', 'Patient demographics & gender'),
    ('utilisation', 'Service utilisation'),
    ('radiology', 'Radiology volumes by modality'),
    ('inventory', 'Inventory usage, wastage & expiry'),
)


def build_catalogue_report(user, report_key, *, period='monthly', centre_id=None, start=None, end=None):
    period, start_dt, end_dt, label = period_window(period, start, end)
    centres, centre_ids, selected = centre_scope(user, centre_id)
    builders = {
        'revenue_centre': _revenue_by_centre,
        'revenue_physician': _revenue_by_physician,
        'revenue_partner': _revenue_by_partner,
        'revenue_service_group': _revenue_by_service_group,
        'revenue_test': _revenue_by_test,
        'revenue_department': _revenue_by_department,
        'commission': _commission_report,
        'hmo_statements': _hmo_statements,
        'demographics': _demographics,
        'utilisation': _utilisation,
        'radiology': _radiology_volumes,
        'inventory': _inventory_report,
    }
    key = report_key if report_key in builders else 'revenue_centre'
    title, headers, rows, meta = builders[key](centre_ids, start_dt, end_dt)
    return {
        'report_key': key,
        'report_title': title,
        'headers': headers,
        'rows': rows,
        'meta': meta,
        'include_patient_ids': bool(meta.get('include_patient_ids')),
        'period': period,
        'period_label': label,
        'start_date': start_dt.date().isoformat(),
        'end_date': end_dt.date().isoformat(),
        'centres': centres,
        'selected_centre': selected,
        'centre_id': str(selected.pk) if selected else '',
        'catalogue_reports': CATALOGUE_REPORTS,
        'period_choices': __import__('billing.reports', fromlist=['PERIOD_CHOICES']).PERIOD_CHOICES,
    }


def _scoped_items(centre_ids, start_dt, end_dt):
    qs = InvoiceItem.objects.filter(
        invoice__created_at__gte=start_dt,
        invoice__created_at__lte=end_dt,
    ).exclude(invoice__status='CANCELLED')
    if centre_ids is not None:
        qs = qs.filter(invoice__visit__centre_id__in=centre_ids)
    return qs


def _revenue_by_centre(centre_ids, start_dt, end_dt):
    qs = Invoice.objects.exclude(status='CANCELLED').filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    )
    if centre_ids is not None:
        qs = qs.filter(visit__centre_id__in=centre_ids)
    headers = ['Centre', 'Invoices', 'Gross charges', 'Discounts', 'Invoice value']
    rows = []
    for row in qs.values('visit__centre__code', 'visit__centre__name').annotate(
        n=Count('id'), gross=Sum('subtotal'), disc=Sum('discount_amount'), value=Sum('total'),
    ).order_by('visit__centre__code'):
        rows.append([
            f"{row['visit__centre__code']} — {row['visit__centre__name']}",
            row['n'], money(row['gross']), money(row['disc']), money(row['value']),
        ])
    return 'Revenue by centre', headers, rows, {}


def _revenue_by_physician(centre_ids, start_dt, end_dt):
    attrs = ReferralAttribution.objects.filter(
        visit__invoices__created_at__gte=start_dt,
        visit__invoices__created_at__lte=end_dt,
    ).exclude(visit__invoices__status='CANCELLED')
    if centre_ids is not None:
        attrs = attrs.filter(visit__centre_id__in=centre_ids)
    headers = ['Doctor code', 'Physician', 'Visits', 'Invoice value']
    rows = []
    for row in attrs.values(
        'doctor__doctor_code', 'doctor__first_name', 'doctor__last_name', 'doctor__is_default_placeholder',
    ).annotate(
        visits=Count('visit_id', distinct=True),
        value=Sum('visit__invoices__total'),
    ).order_by('-value')[:100]:
        name = (
            'DEFAULT DOCTOR' if row['doctor__is_default_placeholder']
            else f"{row['doctor__first_name']} {row['doctor__last_name']}".strip()
        )
        rows.append([row['doctor__doctor_code'], name, row['visits'], money(row['value'])])
    return 'Revenue by referral physician', headers, rows, {}


def _revenue_by_partner(centre_ids, start_dt, end_dt):
    qs = Invoice.objects.exclude(status='CANCELLED').filter(
        created_at__gte=start_dt, created_at__lte=end_dt, visit__client__isnull=False,
    )
    if centre_ids is not None:
        qs = qs.filter(visit__centre_id__in=centre_ids)
    headers = ['Partner', 'Kind', 'Centre', 'Invoices', 'Invoice value']
    rows = []
    for row in qs.values(
        'visit__client__name', 'visit__client__kind', 'visit__centre__code',
    ).annotate(n=Count('id'), value=Sum('total')).order_by('-value')[:100]:
        rows.append([
            row['visit__client__name'], row['visit__client__kind'], row['visit__centre__code'],
            row['n'], money(row['value']),
        ])
    return 'Revenue by corporate / HMO partner', headers, rows, {}


def _revenue_by_service_group(centre_ids, start_dt, end_dt):
    items = _scoped_items(centre_ids, start_dt, end_dt).filter(test__isnull=False)
    headers = ['Service group', 'Lines', 'Gross charges']
    rows = []
    for row in items.values('test__service_group__name').annotate(
        n=Count('id'), gross=Sum('line_total'),
    ).order_by('-gross')[:100]:
        rows.append([row['test__service_group__name'] or '—', row['n'], money(row['gross'])])
    return 'Revenue by service group', headers, rows, {}


def _revenue_by_test(centre_ids, start_dt, end_dt):
    items = _scoped_items(centre_ids, start_dt, end_dt).filter(test__isnull=False)
    headers = ['Test code', 'Test', 'Lines', 'Gross charges']
    rows = []
    for row in items.values('test__code', 'test__name').annotate(
        n=Count('id'), gross=Sum('line_total'),
    ).order_by('-gross')[:150]:
        rows.append([row['test__code'], row['test__name'], row['n'], money(row['gross'])])
    return 'Revenue by test', headers, rows, {}


def _revenue_by_department(centre_ids, start_dt, end_dt):
    items = _scoped_items(centre_ids, start_dt, end_dt).filter(test__isnull=False)
    headers = ['Department', 'Lines', 'Gross charges']
    rows = []
    for row in items.values('test__department').annotate(
        n=Count('id'), gross=Sum('line_total'),
    ).order_by('-gross')[:100]:
        rows.append([row['test__department'] or '—', row['n'], money(row['gross'])])
    return 'Revenue by department', headers, rows, {}


def _commission_report(centre_ids, start_dt, end_dt):
    qs = CommissionLedgerEntry.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        from referrals.services import doctor_ids_serving
        qs = qs.filter(doctor_id__in=doctor_ids_serving(centre_ids))
    headers = ['Doctor code', 'Physician', 'Accruals', 'Reversals', 'Settlements', 'Net']
    buckets = {}
    for row in qs.values(
        'doctor__doctor_code', 'doctor__first_name', 'doctor__last_name', 'entry_type',
    ).annotate(total=Sum('amount')):
        code = row['doctor__doctor_code']
        if code not in buckets:
            buckets[code] = {
                'code': code,
                'name': f"{row['doctor__first_name']} {row['doctor__last_name']}".strip(),
                'ACCRUAL': ZERO, 'REVERSAL': ZERO, 'SETTLEMENT': ZERO, 'ADJUSTMENT': ZERO,
            }
        buckets[code][row['entry_type']] = money(row['total'])
    rows = []
    for b in sorted(buckets.values(), key=lambda x: x['code']):
        net = b['ACCRUAL'] + b['REVERSAL'] + b['SETTLEMENT'] + b['ADJUSTMENT']
        rows.append([b['code'], b['name'], b['ACCRUAL'], b['REVERSAL'], b['SETTLEMENT'], money(net)])
    return 'Referral commission ledger', headers, rows, {}


def _hmo_statements(centre_ids, start_dt, end_dt):
    """Partner statements: aggregate by organisation client — invoice totals and collections.

    Detail uses partner + visit_id (no patient name/UHID in management default view).
    """
    invoices = Invoice.objects.exclude(status='CANCELLED').filter(
        created_at__gte=start_dt, created_at__lte=end_dt, visit__client__isnull=False,
    ).select_related('visit__client', 'visit__centre').prefetch_related('payments', 'refunds')
    if centre_ids is not None:
        invoices = invoices.filter(visit__centre_id__in=centre_ids)

    headers = [
        'Partner', 'Kind', 'Centre', 'Visit', 'Invoice', 'Invoice value',
        'Collected', 'Refunded', 'Balance', 'Status',
    ]
    rows = []
    for inv in invoices.order_by('visit__client__name', '-created_at')[:500]:
        paid = sum((p.amount for p in inv.payments.all() if p.status == 'CONFIRMED'), ZERO)
        refunded = sum((r.amount for r in inv.refunds.all() if r.status == 'COMPLETED'), ZERO)
        balance = money(max(ZERO, (inv.total or ZERO) - max(ZERO, paid - refunded)))
        rows.append([
            inv.visit.client.name,
            inv.visit.client.kind,
            inv.visit.centre.code,
            inv.visit.visit_id,
            inv.invoice_number,
            money(inv.total),
            money(paid),
            money(refunded),
            balance,
            inv.status,
        ])
    # Also include CREDIT / HMO payment method totals without partners
    credit_payments = Payment.objects.filter(
        status='CONFIRMED', method__in=['HMO', 'CREDIT'],
        created_at__gte=start_dt, created_at__lte=end_dt,
    )
    if centre_ids is not None:
        credit_payments = credit_payments.filter(invoice__visit__centre_id__in=centre_ids)
    meta = {
        'hmo_credit_collections': money(credit_payments.aggregate(t=Sum('amount'))['t']),
        'include_patient_ids': False,
    }
    return 'HMO & credit-client statements', headers, rows, meta


def _demographics(centre_ids, start_dt, end_dt):
    visits = Visit.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        visits = visits.filter(centre_id__in=centre_ids)
    headers = ['Sex', 'Visits', 'Distinct patients']
    rows = []
    for row in visits.values('patient__sex').annotate(
        visits=Count('id'), patients=Count('patient_id', distinct=True),
    ).order_by('patient__sex'):
        rows.append([row['patient__sex'] or 'Unspecified', row['visits'], row['patients']])
    # Age bands from date_of_birth when present
    age_headers_extra = []
    today = timezone.localdate()
    bands = {'0-17': 0, '18-39': 0, '40-59': 0, '60+': 0, 'Unknown': 0}
    for dob in visits.values_list('patient__date_of_birth', flat=True).distinct()[:5000]:
        if not dob:
            bands['Unknown'] += 1
            continue
        years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if years < 18:
            bands['0-17'] += 1
        elif years < 40:
            bands['18-39'] += 1
        elif years < 60:
            bands['40-59'] += 1
        else:
            bands['60+'] += 1
    for band, count in bands.items():
        age_headers_extra.append([f'Age {band}', count, '—'])
    rows.extend(age_headers_extra)
    return 'Patient demographics & gender', headers, rows, {}


def _utilisation(centre_ids, start_dt, end_dt):
    orders = LabOrder.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        orders = orders.filter(visit__centre_id__in=centre_ids)
    headers = ['Centre', 'Department', 'Service group', 'Orders']
    rows = []
    for row in orders.values(
        'visit__centre__code', 'test__department', 'test__service_group__name',
    ).annotate(n=Count('id')).order_by('-n')[:150]:
        rows.append([
            row['visit__centre__code'],
            row['test__department'] or '—',
            row['test__service_group__name'] or '—',
            row['n'],
        ])
    return 'Service utilisation', headers, rows, {}


def _radiology_volumes(centre_ids, start_dt, end_dt):
    orders = ImagingOrder.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        orders = orders.filter(visit__centre_id__in=centre_ids)
    headers = ['Modality type', 'Order status', 'Orders']
    rows = []
    for row in orders.values('modality__modality_type', 'status').annotate(n=Count('id')).order_by(
        'modality__modality_type', 'status',
    ):
        rows.append([row['modality__modality_type'], row['status'], row['n']])

    reports = RadiologyReport.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        reports = reports.filter(exam__order__visit__centre_id__in=centre_ids)
    for row in reports.values('status').annotate(n=Count('id')).order_by('status'):
        rows.append(['(report)', row['status'], row['n']])
    return 'Radiology volumes by modality & status', headers, rows, {}


def _inventory_report(centre_ids, start_dt, end_dt):
    moves = StockMovement.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        moves = moves.filter(sku__centre_id__in=centre_ids)
    headers = ['Centre', 'SKU', 'Name', 'Type', 'Quantity', 'Lot', 'Expires']
    rows = []
    for m in moves.select_related('sku', 'sku__centre').order_by('-created_at')[:500]:
        rows.append([
            m.sku.centre.code,
            m.sku.sku_code,
            m.sku.name,
            m.movement_type,
            money(m.quantity),
            m.lot_number or '—',
            m.expires_on.isoformat() if m.expires_on else '—',
        ])
    # Expiry peek (next 60 days) — operational snapshot
    soon = timezone.localdate() + timedelta(days=60)
    skus = ReagentSku.objects.filter(is_active=True)
    if centre_ids is not None:
        skus = skus.filter(centre_id__in=centre_ids)
    expiring = (
        StockMovement.objects.filter(
            sku__in=skus, expires_on__isnull=False, expires_on__lte=soon, expires_on__gte=timezone.localdate(),
        )
        .values('sku__centre__code', 'sku__sku_code', 'sku__name', 'lot_number', 'expires_on')
        .annotate(qty=Sum('quantity'))
        .order_by('expires_on')[:100]
    )
    meta = {
        'usage': money(moves.filter(movement_type='ISSUE').aggregate(t=Sum('quantity'))['t']),
        'wastage_expire': money(moves.filter(movement_type='EXPIRE').aggregate(t=Sum('quantity'))['t']),
        'expiring_lots': list(expiring),
    }
    return 'Inventory usage, wastage & expiry', headers, rows, meta
