"""Management dashboard — four panels over transactional records."""
from decimal import Decimal

from django.db.models import Count, Sum

from billing.models import Invoice
from laboratory.models import LabOrder, LabResult, Sample
from laboratory.tat import pending_work_breakdown, quality_rates, tat_dashboard_payload
from referrals.models import CommissionLedgerEntry, ReferralAttribution, ReferralDoctor, WithdrawalRequest
from referrals.services import doctor_ids_serving

from .metrics import centre_scope, financial_kpis, money

ZERO = Decimal('0.00')


def build_management_dashboard(user, *, period='monthly', centre_id=None, start=None, end=None):
    finance = financial_kpis(user, period=period, centre_id=centre_id, start=start, end=end)
    start_dt, end_dt = finance['start'], finance['end']
    _, centre_ids, selected = centre_scope(user, centre_id)

    clinical = _clinical_ops(user, centre_ids, start_dt, end_dt)
    centres_panel = _centre_performance(centre_ids, start_dt, end_dt)
    referral = _referral_network(centre_ids, start_dt, end_dt)

    return {
        **finance,
        'financial': finance['kpis'],
        'clinical': clinical,
        'centres_panel': centres_panel,
        'referral': referral,
        'selected_centre': selected,
    }


def _clinical_ops(user, centre_ids, start_dt, end_dt):
    samples = Sample.objects.all()
    results = LabResult.objects.filter(status='RELEASED', released_at__gte=start_dt, released_at__lte=end_dt)
    orders = LabOrder.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        samples = samples.filter(centre_id__in=centre_ids)
        results = results.filter(sample__centre_id__in=centre_ids)
        orders = orders.filter(visit__centre_id__in=centre_ids)

    open_samples = samples.exclude(status__in=['RELEASED', 'CANCELLED', 'REJECTED', 'SUPERSEDED']).count()
    released = results.count()
    pending_tests = orders.exclude(status__in=['CANCELLED', 'COMPLETED']).count()

    window_days = max(1, (end_dt.date() - start_dt.date()).days + 1)
    quality = quality_rates(user, days=min(window_days, 90))
    pending = pending_work_breakdown(user)
    tat = tat_dashboard_payload(user, scan=False)
    overdue = tat.get('overdue_total') or 0
    warnings = tat.get('warning_total') or 0
    dept_rows = pending.get('by_department') or []
    if dept_rows and isinstance(dept_rows[0], dict) and 'key' in dept_rows[0]:
        pending_by_department = [
            {'department': row['key'], 'count': row['count']}
            for row in sorted(dept_rows, key=lambda r: -r['count'])[:12]
        ]
    else:
        pending_by_department = []

    return {
        'pending_tests': pending_tests,
        'open_samples': open_samples,
        'overdue_samples': overdue,
        'warning_samples': warnings,
        'released_reports': released,
        'rejections': quality.get('rejections', 0),
        'recollections': quality.get('recollections', 0),
        'rejection_rate': quality.get('rejection_rate', 0),
        'recollection_rate': quality.get('recollection_rate', 0),
        'pending_by_department': pending_by_department,
    }


def _centre_performance(centre_ids, start_dt, end_dt):
    invoices = Invoice.objects.exclude(status='CANCELLED').filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    )
    if centre_ids is not None:
        invoices = invoices.filter(visit__centre_id__in=centre_ids)

    by_centre = []
    for row in invoices.values(
        'visit__centre__code', 'visit__centre__name',
    ).annotate(
        invoice_count=Count('id'),
        gross=Sum('subtotal'),
        discounts=Sum('discount_amount'),
        invoice_value=Sum('total'),
    ).order_by('visit__centre__code'):
        by_centre.append({
            'code': row['visit__centre__code'],
            'name': row['visit__centre__name'],
            'invoice_count': row['invoice_count'],
            'gross_charges': money(row['gross']),
            'discounts': money(row['discounts']),
            'invoice_value': money(row['invoice_value']),
        })

    # Service mix from lab orders in period
    orders = LabOrder.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        orders = orders.filter(visit__centre_id__in=centre_ids)
    service_mix = [
        {
            'service_group': row['test__service_group__name'] or '—',
            'count': row['count'],
        }
        for row in orders.values('test__service_group__name').annotate(count=Count('id')).order_by('-count')[:15]
    ]
    return {'by_centre': by_centre, 'service_mix': service_mix}


def _referral_network(centre_ids, start_dt, end_dt):
    attributions = ReferralAttribution.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('doctor')
    if centre_ids is not None:
        attributions = attributions.filter(visit__centre_id__in=centre_ids)

    volume_by_doctor = {}
    for row in attributions.values(
        'doctor_id', 'doctor__doctor_code', 'doctor__first_name', 'doctor__last_name',
        'doctor__is_default_placeholder',
    ).annotate(visits=Count('id')).order_by('-visits')[:25]:
        volume_by_doctor[str(row['doctor_id'])] = {
            'doctor_code': row['doctor__doctor_code'],
            'name': (
                'DEFAULT DOCTOR' if row['doctor__is_default_placeholder']
                else f"{row['doctor__first_name']} {row['doctor__last_name']}".strip()
            ),
            'visits': row['visits'],
            'accruals': ZERO,
            'reversals': ZERO,
            'settlements': ZERO,
            'net': ZERO,
        }

    ledger = CommissionLedgerEntry.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if centre_ids is not None:
        ledger = ledger.filter(doctor_id__in=doctor_ids_serving(centre_ids))

    accruals = money(ledger.filter(entry_type='ACCRUAL').aggregate(t=Sum('amount'))['t'])
    reversals = money(ledger.filter(entry_type='REVERSAL').aggregate(t=Sum('amount'))['t'])
    settlements = money(ledger.filter(entry_type='SETTLEMENT').aggregate(t=Sum('amount'))['t'])
    net = money(ledger.aggregate(t=Sum('amount'))['t'])

    for row in ledger.values('doctor_id', 'entry_type').annotate(total=Sum('amount')):
        key = str(row['doctor_id'])
        if key not in volume_by_doctor:
            continue
        amount = money(row['total'])
        if row['entry_type'] == 'ACCRUAL':
            volume_by_doctor[key]['accruals'] += amount
        elif row['entry_type'] == 'REVERSAL':
            volume_by_doctor[key]['reversals'] += amount
        elif row['entry_type'] == 'SETTLEMENT':
            volume_by_doctor[key]['settlements'] += amount
        volume_by_doctor[key]['net'] += amount

    pending_withdrawals = WithdrawalRequest.objects.filter(status='REQUESTED')
    if centre_ids is not None:
        pending_withdrawals = pending_withdrawals.filter(doctor_id__in=doctor_ids_serving(centre_ids))
    pending_liability = money(pending_withdrawals.aggregate(t=Sum('amount'))['t'])

    doctors = sorted(volume_by_doctor.values(), key=lambda r: (-r['visits'], r['name']))[:20]
    return {
        'referral_visits': attributions.count(),
        'accruals': accruals,
        'reversals': reversals,
        'settlements': settlements,
        'commission_net': net,
        'pending_withdrawal_count': pending_withdrawals.count(),
        'pending_payout_liability': pending_liability,
        'doctors': doctors,
        'marketers': _marketer_recruitment(centre_ids, start_dt, end_dt),
    }


def _doctor_display_name(doctor):
    title = (doctor.title or '').strip()
    full = f'{doctor.first_name} {doctor.last_name}'.strip()
    return f'{title} {full}'.strip() if title else (full or doctor.doctor_code)


def _marketer_recruitment(centre_ids, start_dt, end_dt):
    """Staff ranked by how many referring doctors they introduced, with those doctors named."""
    doctors = ReferralDoctor.objects.filter(
        is_default_placeholder=False, onboarded_by__isnull=False,
    ).select_related('onboarded_by').order_by('last_name', 'first_name', 'doctor_code')
    if centre_ids is not None:
        doctors = doctors.filter(pk__in=doctor_ids_serving(centre_ids))
    by_staff = {}
    for doctor in doctors:
        staff = doctor.onboarded_by
        bucket = by_staff.get(staff.pk)
        if bucket is None:
            first = (staff.first_name or '').strip()
            last = (staff.last_name or '').strip()
            bucket = {
                'name': f'{first} {last}'.strip() or staff.username,
                'username': staff.username,
                'doctors': 0,
                'doctor_names': [],
                'visits': 0,
                '_sort_last': last.lower(),
            }
            by_staff[staff.pk] = bucket
        bucket['doctors'] += 1
        bucket['doctor_names'].append(_doctor_display_name(doctor))
    attributions = ReferralAttribution.objects.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
        doctor__onboarded_by__isnull=False, doctor__is_default_placeholder=False,
    )
    if centre_ids is not None:
        attributions = attributions.filter(visit__centre_id__in=centre_ids)
    visit_counts = {
        row['doctor__onboarded_by_id']: row['visits']
        for row in attributions.values('doctor__onboarded_by_id').annotate(visits=Count('id'))
    }
    for staff_id, bucket in by_staff.items():
        bucket['visits'] = visit_counts.get(staff_id, 0)
    ranked = sorted(
        by_staff.values(),
        key=lambda row: (-row['doctors'], row['_sort_last'], row['username']),
    )[:20]
    for row in ranked:
        row.pop('_sort_last', None)
    return ranked
