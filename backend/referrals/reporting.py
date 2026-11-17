"""Shared referral / commission report builders for enterprise workspace and catalogue."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, Q, Sum

from billing.models import Invoice
from reporting.metrics import centre_scope, money, period_window
from referrals.models import (
    CommissionLedgerEntry, ReferralAttribution, ReferralDoctor, ReferralInstitution, WithdrawalRequest,
)
from referrals.services import doctor_ids_serving

ZERO = Decimal('0.00')


def _doctor_name(doctor) -> str:
    if getattr(doctor, 'is_default_placeholder', False):
        return 'DEFAULT DOCTOR'
    title = (doctor.title or '').strip()
    full = f'{doctor.first_name} {doctor.last_name}'.strip()
    return f'{title} {full}'.strip() if title else (full or doctor.doctor_code)


def _commission_preset(doctor) -> str:
    value = money(doctor.commission_value)
    if doctor.commission_type == 'PERCENT':
        text = format(value, 'f').rstrip('0').rstrip('.')
        return f'{text}%'
    return f'₦{value} fixed'


def parse_referral_filters(request_get, user):
    """Parse GET filters shared by enterprise referral screens and exports."""
    period = request_get.get('period') or 'monthly'
    centre_id = request_get.get('centre') or None
    start = request_get.get('start')
    end = request_get.get('end')
    period, start_dt, end_dt, label = period_window(period, start, end)
    centres, centre_ids, selected = centre_scope(user, centre_id)
    doctor_id = (request_get.get('doctor') or '').strip() or None
    institution_id = (request_get.get('institution') or '').strip() or None
    commission_type = (request_get.get('commission_type') or '').strip().upper() or None
    if commission_type not in ('PERCENT', 'FIXED'):
        commission_type = None
    due_status = (request_get.get('due_status') or '').strip().lower() or None
    if due_status not in ('due', 'zero', 'credit'):
        due_status = None
    marketer_id = (request_get.get('marketer') or '').strip() or None
    status = (request_get.get('status') or 'all').strip().lower()
    q = (request_get.get('q') or '').strip()
    has_bank = (request_get.get('has_bank') or '').strip().lower()
    appear = (request_get.get('appear_on_reports') or '').strip().lower()
    invoice_status = (request_get.get('invoice_status') or '').strip().upper() or None
    if invoice_status and invoice_status not in dict(Invoice._meta.get_field('status').choices):
        invoice_status = None
    return {
        'period': period,
        'period_label': label,
        'start_dt': start_dt,
        'end_dt': end_dt,
        'start_date': start_dt.date().isoformat(),
        'end_date': end_dt.date().isoformat(),
        'centres': centres,
        'centre_ids': centre_ids,
        'selected_centre': selected,
        'centre_id': str(selected.pk) if selected else '',
        'doctor_id': doctor_id,
        'institution_id': institution_id,
        'commission_type': commission_type,
        'due_status': due_status,
        'marketer_id': marketer_id,
        'status': status,
        'q': q,
        'has_bank': has_bank,
        'appear_on_reports': appear,
        'invoice_status': invoice_status,
    }


def scoped_doctors(filters, *, include_placeholders=False):
    qs = ReferralDoctor.objects.select_related('centre', 'onboarded_by').prefetch_related('centres')
    if not include_placeholders:
        qs = qs.filter(is_default_placeholder=False)
    centre_ids = filters.get('centre_ids')
    if centre_ids is not None:
        qs = qs.filter(pk__in=doctor_ids_serving(centre_ids))
    if filters.get('doctor_id'):
        qs = qs.filter(pk=filters['doctor_id'])
    if filters.get('commission_type'):
        qs = qs.filter(commission_type=filters['commission_type'])
    if filters.get('marketer_id'):
        qs = qs.filter(onboarded_by_id=filters['marketer_id'])
    if filters.get('institution_id'):
        qs = qs.filter(institutions__pk=filters['institution_id'])
    status = filters.get('status') or 'all'
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    if filters.get('has_bank') == 'yes':
        qs = qs.exclude(Q(bank_name='') | Q(account_number=''))
    elif filters.get('has_bank') == 'no':
        qs = qs.filter(Q(bank_name='') | Q(account_number=''))
    if filters.get('appear_on_reports') == 'yes':
        qs = qs.filter(appear_on_reports=True)
    elif filters.get('appear_on_reports') == 'no':
        qs = qs.filter(appear_on_reports=False)
    q = filters.get('q') or ''
    if q:
        qs = qs.filter(
            Q(doctor_code__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phone__icontains=q)
            | Q(facility_name__icontains=q)
            | Q(email__icontains=q)
        )
    return qs.order_by('last_name', 'first_name', 'doctor_code')


def doctor_balances(doctor_ids=None):
    qs = CommissionLedgerEntry.objects.all()
    if doctor_ids is not None:
        qs = qs.filter(doctor_id__in=doctor_ids)
    return {
        str(row['doctor_id']): money(row['total'])
        for row in qs.values('doctor_id').annotate(total=Sum('amount'))
    }


def period_ledger_buckets(filters):
    qs = CommissionLedgerEntry.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
    ).select_related('doctor')
    centre_ids = filters.get('centre_ids')
    if centre_ids is not None:
        qs = qs.filter(doctor_id__in=doctor_ids_serving(centre_ids))
    if filters.get('doctor_id'):
        qs = qs.filter(doctor_id=filters['doctor_id'])
    if filters.get('commission_type'):
        qs = qs.filter(doctor__commission_type=filters['commission_type'])
    if filters.get('institution_id'):
        doctor_ids = ReferralAttribution.objects.filter(
            institutions__pk=filters['institution_id'],
            created_at__gte=filters['start_dt'],
            created_at__lte=filters['end_dt'],
        ).values_list('doctor_id', flat=True).distinct()
        qs = qs.filter(doctor_id__in=doctor_ids)
    return qs


def build_commission_due(filters):
    """Period commission panel + outstanding payable balance per doctor."""
    doctors = list(scoped_doctors(filters, include_placeholders=False))
    doctor_ids = [d.pk for d in doctors]
    balances = doctor_balances(doctor_ids)
    ledger = period_ledger_buckets(filters).filter(doctor_id__in=doctor_ids)

    period = {
        str(row['doctor_id']): {
            'ACCRUAL': ZERO, 'REVERSAL': ZERO, 'SETTLEMENT': ZERO, 'ADJUSTMENT': ZERO,
        }
        for row in [{'doctor_id': pk} for pk in doctor_ids]
    }
    for row in ledger.values('doctor_id', 'entry_type').annotate(total=Sum('amount')):
        key = str(row['doctor_id'])
        period.setdefault(key, {'ACCRUAL': ZERO, 'REVERSAL': ZERO, 'SETTLEMENT': ZERO, 'ADJUSTMENT': ZERO})
        period[key][row['entry_type']] = money(row['total'])

    attributions = ReferralAttribution.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
        doctor_id__in=doctor_ids,
    )
    if filters.get('centre_ids') is not None:
        attributions = attributions.filter(visit__centre_id__in=filters['centre_ids'])
    if filters.get('institution_id'):
        attributions = attributions.filter(institutions__pk=filters['institution_id'])

    visit_counts = {
        str(row['doctor_id']): row['n']
        for row in attributions.values('doctor_id').annotate(n=Count('id'))
    }
    paid_totals = {
        str(row['visit__referral_attribution__doctor_id']): money(row['total'])
        for row in Invoice.objects.filter(
            status='PAID',
            visit__referral_attribution__doctor_id__in=doctor_ids,
            created_at__gte=filters['start_dt'],
            created_at__lte=filters['end_dt'],
        ).values('visit__referral_attribution__doctor_id').annotate(total=Sum('total'))
    }

    headers = [
        'Doctor code', 'Physician', 'Facility', 'Commission', 'Visits', 'Paid invoice total',
        'Period accruals', 'Period reversals', 'Period settlements', 'Period net',
        'Outstanding balance', 'Bank', 'Account name', 'Account number',
    ]
    rows = []
    detail_rows = []
    for doctor in doctors:
        key = str(doctor.pk)
        bucket = period.get(key, {'ACCRUAL': ZERO, 'REVERSAL': ZERO, 'SETTLEMENT': ZERO, 'ADJUSTMENT': ZERO})
        period_net = money(
            bucket['ACCRUAL'] + bucket['REVERSAL'] + bucket['SETTLEMENT'] + bucket.get('ADJUSTMENT', ZERO)
        )
        balance = balances.get(key, ZERO)
        due = filters.get('due_status')
        if due == 'due' and balance <= 0:
            continue
        if due == 'zero' and balance != 0:
            continue
        if due == 'credit' and balance >= 0:
            continue
        rows.append([
            doctor.doctor_code,
            _doctor_name(doctor),
            doctor.facility_name or '—',
            _commission_preset(doctor),
            visit_counts.get(key, 0),
            paid_totals.get(key, ZERO),
            bucket['ACCRUAL'],
            bucket['REVERSAL'],
            bucket['SETTLEMENT'],
            period_net,
            balance,
            doctor.bank_name or '—',
            doctor.account_name or '—',
            doctor.account_number or '—',
        ])
        detail_rows.append({
            'doctor': doctor,
            'visits': visit_counts.get(key, 0),
            'paid_total': paid_totals.get(key, ZERO),
            'accruals': bucket['ACCRUAL'],
            'reversals': bucket['REVERSAL'],
            'settlements': bucket['SETTLEMENT'],
            'period_net': period_net,
            'balance': balance,
        })

    totals = {
        'doctors': len(rows),
        'period_net': money(sum((r[9] for r in rows), ZERO)),
        'outstanding': money(sum((r[10] for r in rows), ZERO)),
        'visits': sum((r[4] for r in rows), 0),
    }
    return {
        'title': 'Referral commission due',
        'headers': headers,
        'rows': rows,
        'detail_rows': detail_rows,
        'totals': totals,
        'meta': {'include_patient_ids': False},
    }


def build_referral_activity(filters):
    """Ledger lines with visit/invoice identifiers (no patient PII)."""
    qs = period_ledger_buckets(filters).select_related(
        'doctor', 'invoice', 'invoice__visit', 'invoice__visit__centre',
    ).order_by('-created_at')
    if filters.get('due_status'):
        # Activity is line-level; due_status applies to doctors only — no-op here.
        pass
    headers = [
        'Date', 'Doctor code', 'Physician', 'Entry', 'Amount', 'Invoice', 'Visit', 'Centre',
        'Institutions', 'Description',
    ]
    rows = []
    for entry in qs[:1000]:
        invoice = entry.invoice
        visit = invoice.visit if invoice else None
        institutions = '—'
        if visit is not None:
            try:
                names = list(visit.referral_attribution.institutions.values_list('name', flat=True))
                institutions = ', '.join(names) if names else '—'
            except Exception:
                institutions = '—'
        rows.append([
            entry.created_at.strftime('%Y-%m-%d %H:%M'),
            entry.doctor.doctor_code,
            _doctor_name(entry.doctor),
            entry.get_entry_type_display(),
            money(entry.amount),
            invoice.invoice_number if invoice else '—',
            visit.visit_id if visit else '—',
            visit.centre.code if visit else '—',
            institutions,
            entry.description,
        ])
    return {
        'title': 'Referral activity detail',
        'headers': headers,
        'rows': rows,
        'meta': {'include_patient_ids': False},
    }


def build_institution_performance(filters):
    attrs = ReferralAttribution.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
    ).prefetch_related('institutions').select_related('doctor', 'visit')
    if filters.get('centre_ids') is not None:
        attrs = attrs.filter(visit__centre_id__in=filters['centre_ids'])
    if filters.get('doctor_id'):
        attrs = attrs.filter(doctor_id=filters['doctor_id'])
    if filters.get('institution_id'):
        attrs = attrs.filter(institutions__pk=filters['institution_id'])
    if filters.get('commission_type'):
        attrs = attrs.filter(doctor__commission_type=filters['commission_type'])

    buckets = {}
    for attr in attrs:
        institutions = list(attr.institutions.all()) or [None]
        for inst in institutions:
            key = (inst.pk if inst else None, attr.doctor_id)
            if key not in buckets:
                buckets[key] = {
                    'institution': inst.name if inst else '— Direct / none —',
                    'doctor_code': attr.doctor.doctor_code,
                    'doctor_name': _doctor_name(attr.doctor),
                    'visits': 0,
                    'paid_revenue': ZERO,
                    'commission': ZERO,
                }
            buckets[key]['visits'] += 1

    # Paid revenue + commission for attributed paid invoices in period
    invoices = Invoice.objects.filter(
        status='PAID',
        created_at__gte=filters['start_dt'],
        created_at__lte=filters['end_dt'],
        visit__referral_attribution__isnull=False,
    ).select_related('visit__referral_attribution__doctor').prefetch_related(
        'visit__referral_attribution__institutions', 'commission_entries',
    )
    if filters.get('centre_ids') is not None:
        invoices = invoices.filter(visit__centre_id__in=filters['centre_ids'])
    if filters.get('doctor_id'):
        invoices = invoices.filter(visit__referral_attribution__doctor_id=filters['doctor_id'])
    if filters.get('institution_id'):
        invoices = invoices.filter(visit__referral_attribution__institutions__pk=filters['institution_id'])

    for inv in invoices.distinct():
        attr = inv.visit.referral_attribution
        institutions = list(attr.institutions.all()) or [None]
        commission = money(
            sum(
                (e.amount for e in inv.commission_entries.all() if e.entry_type == 'ACCRUAL'),
                ZERO,
            )
        )
        for inst in institutions:
            key = (inst.pk if inst else None, attr.doctor_id)
            if key not in buckets:
                buckets[key] = {
                    'institution': inst.name if inst else '— Direct / none —',
                    'doctor_code': attr.doctor.doctor_code,
                    'doctor_name': _doctor_name(attr.doctor),
                    'visits': 0,
                    'paid_revenue': ZERO,
                    'commission': ZERO,
                }
            buckets[key]['paid_revenue'] = money(buckets[key]['paid_revenue'] + (inv.total or ZERO))
            buckets[key]['commission'] = money(buckets[key]['commission'] + commission)

    headers = ['Institution', 'Doctor code', 'Physician', 'Visits', 'Paid revenue', 'Commission accrued']
    rows = [
        [b['institution'], b['doctor_code'], b['doctor_name'], b['visits'], b['paid_revenue'], b['commission']]
        for b in sorted(buckets.values(), key=lambda x: (-x['visits'], x['institution'], x['doctor_code']))
    ]
    return {
        'title': 'Institution referral performance',
        'headers': headers,
        'rows': rows,
        'meta': {'include_patient_ids': False},
    }


def build_marketer_performance(filters):
    doctors = ReferralDoctor.objects.filter(
        is_default_placeholder=False, onboarded_by__isnull=False,
    ).select_related('onboarded_by')
    if filters.get('centre_ids') is not None:
        doctors = doctors.filter(pk__in=doctor_ids_serving(filters['centre_ids']))
    if filters.get('marketer_id'):
        doctors = doctors.filter(onboarded_by_id=filters['marketer_id'])

    by_staff = {}
    for doctor in doctors:
        staff = doctor.onboarded_by
        key = str(staff.pk)
        if key not in by_staff:
            by_staff[key] = {
                'staff': staff.get_username(),
                'name': getattr(staff, 'display_name', None) or staff.get_full_name() or staff.get_username(),
                'doctors': 0,
                'doctor_codes': [],
                'period_visits': 0,
                'period_commission': ZERO,
            }
        by_staff[key]['doctors'] += 1
        by_staff[key]['doctor_codes'].append(doctor.doctor_code)

    attrs = ReferralAttribution.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
        doctor__onboarded_by__isnull=False, doctor__is_default_placeholder=False,
    )
    if filters.get('centre_ids') is not None:
        attrs = attrs.filter(visit__centre_id__in=filters['centre_ids'])
    if filters.get('marketer_id'):
        attrs = attrs.filter(doctor__onboarded_by_id=filters['marketer_id'])
    for row in attrs.values('doctor__onboarded_by_id').annotate(n=Count('id')):
        key = str(row['doctor__onboarded_by_id'])
        if key in by_staff:
            by_staff[key]['period_visits'] = row['n']

    ledger = CommissionLedgerEntry.objects.filter(
        entry_type='ACCRUAL',
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
        doctor__onboarded_by__isnull=False,
    )
    if filters.get('centre_ids') is not None:
        ledger = ledger.filter(doctor_id__in=doctor_ids_serving(filters['centre_ids']))
    if filters.get('marketer_id'):
        ledger = ledger.filter(doctor__onboarded_by_id=filters['marketer_id'])
    for row in ledger.values('doctor__onboarded_by_id').annotate(total=Sum('amount')):
        key = str(row['doctor__onboarded_by_id'])
        if key in by_staff:
            by_staff[key]['period_commission'] = money(row['total'])

    headers = ['Staff', 'Username', 'Doctors onboarded', 'Doctor codes', 'Period visits', 'Period commission']
    rows = [
        [
            b['name'], b['staff'], b['doctors'], ', '.join(b['doctor_codes'][:12]),
            b['period_visits'], b['period_commission'],
        ]
        for b in sorted(by_staff.values(), key=lambda x: (-x['period_commission'], -x['doctors'], x['name']))
    ]
    return {
        'title': 'Introducing staff / marketers',
        'headers': headers,
        'rows': rows,
        'meta': {'include_patient_ids': False},
    }


def build_attributed_visits(filters):
    qs = ReferralAttribution.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
    ).select_related(
        'doctor', 'visit', 'visit__centre', 'recorded_by',
    ).prefetch_related('institutions', 'visit__invoices').order_by('-created_at')
    if filters.get('centre_ids') is not None:
        qs = qs.filter(visit__centre_id__in=filters['centre_ids'])
    if filters.get('doctor_id'):
        qs = qs.filter(doctor_id=filters['doctor_id'])
    if filters.get('institution_id'):
        qs = qs.filter(institutions__pk=filters['institution_id'])
    if filters.get('commission_type'):
        qs = qs.filter(doctor__commission_type=filters['commission_type'])
    if filters.get('invoice_status'):
        qs = qs.filter(visit__invoices__status=filters['invoice_status'])
    q = filters.get('q') or ''
    if q:
        qs = qs.filter(
            Q(visit__visit_id__icontains=q)
            | Q(visit__invoices__invoice_number__icontains=q)
            | Q(doctor__doctor_code__icontains=q)
            | Q(doctor__first_name__icontains=q)
            | Q(doctor__last_name__icontains=q)
        )

    headers = [
        'Attributed', 'Visit', 'Centre', 'Doctor code', 'Physician', 'Institutions',
        'Invoice', 'Invoice status', 'Invoice total', 'Channel',
    ]
    rows = []
    objects = []
    for attr in qs.distinct()[:500]:
        visit = attr.visit
        invoice = visit.invoices.order_by('-created_at').first()
        institutions = ', '.join(attr.institutions.values_list('name', flat=True)) or '—'
        rows.append([
            attr.created_at.strftime('%Y-%m-%d %H:%M'),
            visit.visit_id,
            visit.centre.code,
            attr.doctor.doctor_code,
            _doctor_name(attr.doctor),
            institutions,
            invoice.invoice_number if invoice else '—',
            invoice.status if invoice else '—',
            money(invoice.total) if invoice else ZERO,
            visit.referral_source or '—',
        ])
        objects.append(attr)
    return {
        'title': 'Attributed referral visits',
        'headers': headers,
        'rows': rows,
        'objects': objects,
        'meta': {'include_patient_ids': False},
    }


def hub_kpis(filters):
    due = build_commission_due({**filters, 'due_status': None, 'doctor_id': None, 'q': '', 'status': 'active'})
    pending = WithdrawalRequest.objects.filter(status='REQUESTED')
    if filters.get('centre_ids') is not None:
        pending = pending.filter(doctor_id__in=doctor_ids_serving(filters['centre_ids']))
    visits = ReferralAttribution.objects.filter(
        created_at__gte=filters['start_dt'], created_at__lte=filters['end_dt'],
    )
    if filters.get('centre_ids') is not None:
        visits = visits.filter(visit__centre_id__in=filters['centre_ids'])
    activity = build_referral_activity(filters)
    return {
        'active_doctors': scoped_doctors({**filters, 'status': 'active', 'q': ''}).count(),
        'period_visits': visits.count(),
        'period_net': due['totals']['period_net'],
        'outstanding': due['totals']['outstanding'],
        'pending_payouts': pending.count(),
        'pending_liability': money(pending.aggregate(t=Sum('amount'))['t']),
        'ledger_lines': len(activity['rows']),
    }


def filter_choices(user, filters):
    doctors = scoped_doctors({**filters, 'doctor_id': None, 'q': '', 'status': 'all'}, include_placeholders=False)
    institutions = ReferralInstitution.objects.filter(is_active=True, doctor__in=doctors).order_by('name')
    marketers = (
        ReferralDoctor.objects.filter(onboarded_by__isnull=False, is_default_placeholder=False)
        .values('onboarded_by_id', 'onboarded_by__username', 'onboarded_by__first_name', 'onboarded_by__last_name')
        .distinct()
    )
    if filters.get('centre_ids') is not None:
        marketers = marketers.filter(pk__in=doctor_ids_serving(filters['centre_ids']))
    return {
        'doctor_choices': doctors[:500],
        'institution_choices': institutions[:500],
        'marketer_choices': list(marketers[:200]),
        'period_choices': __import__('billing.reports', fromlist=['PERIOD_CHOICES']).PERIOD_CHOICES,
        'invoice_status_choices': Invoice._meta.get_field('status').choices,
    }
