"""Enterprise referring-doctor workspace: directory, commission due, visits, payouts."""
from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.access import (
    can_approve_referral_settlement, can_manage_referrals, is_platform_admin,
)
from core.audit import record_audit
from core.paging import PAGE_SIZES, paginate
from referrals.models import CommissionLedgerEntry, WithdrawalRequest
from referrals.reporting import (
    build_attributed_visits, build_commission_due, build_institution_performance,
    build_marketer_performance, build_referral_activity, filter_choices, hub_kpis,
    parse_referral_filters, scoped_doctors,
)
from referrals.services import doctor_ids_serving
from reporting.exports import log_report_export, table_export, tables_export


def _guard(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not can_manage_referrals(request.user):
        raise PermissionDenied('You do not have access to enterprise referrals.')


def _export_query(filters, extra=None):
    params = {
        'period': filters.get('period') or 'monthly',
        'centre': filters.get('centre_id') or '',
        'start': filters.get('start_date') or '',
        'end': filters.get('end_date') or '',
        'doctor': filters.get('doctor_id') or '',
        'institution': filters.get('institution_id') or '',
        'commission_type': filters.get('commission_type') or '',
        'due_status': filters.get('due_status') or '',
        'marketer': filters.get('marketer_id') or '',
        'status': filters.get('status') or 'all',
        'q': filters.get('q') or '',
        'has_bank': filters.get('has_bank') or '',
        'appear_on_reports': filters.get('appear_on_reports') or '',
        'invoice_status': filters.get('invoice_status') or '',
    }
    if extra:
        params.update(extra)
    return urlencode({k: v for k, v in params.items() if v not in (None, '')})


def _base_context(request, filters, *, section):
    choices = filter_choices(request.user, filters)
    return {
        'filters': filters,
        'section': section,
        'export_query': _export_query(filters),
        'can_settle': can_approve_referral_settlement(request.user),
        'is_platform_admin': is_platform_admin(request.user),
        'page_sizes': PAGE_SIZES,
        **choices,
    }


@login_required
@require_http_methods(['GET'])
def referrals_hub(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    kpis = hub_kpis(filters)
    due = build_commission_due({**filters, 'due_status': 'due'})
    pending = WithdrawalRequest.objects.filter(status='REQUESTED').select_related('doctor', 'doctor__centre')
    if filters.get('centre_ids') is not None:
        pending = pending.filter(doctor_id__in=doctor_ids_serving(filters['centre_ids']))
    return render(request, 'enterprise/referrals/hub.html', {
        **_base_context(request, filters, section='hub'),
        'kpis': kpis,
        'top_due': due['detail_rows'][:10],
        'pending_withdrawals': pending.order_by('-requested_at')[:8],
    })


@login_required
@require_http_methods(['GET'])
def referrals_doctors(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    doctors = scoped_doctors(filters, include_placeholders=False)
    balances = {
        str(row['doctor_id']): row['total'] or Decimal('0')
        for row in CommissionLedgerEntry.objects.filter(
            doctor_id__in=doctors.values('pk'),
        ).values('doctor_id').annotate(total=Sum('amount'))
    }
    page = paginate(request, doctors, 'page')
    from referrals.reporting import _commission_preset
    rows = [
        {
            'doctor': doctor,
            'balance': balances.get(str(doctor.pk), Decimal('0.00')),
            'preset': _commission_preset(doctor),
        }
        for doctor in page
    ]
    return render(request, 'enterprise/referrals/doctors.html', {
        **_base_context(request, filters, section='doctors'),
        'directory': page,
        'rows': rows,
    })


@login_required
@require_http_methods(['GET'])
def referrals_commission(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    due = build_commission_due(filters)
    activity = build_referral_activity(filters)
    institutions = build_institution_performance(filters)
    marketers = build_marketer_performance(filters)
    return render(request, 'enterprise/referrals/commission.html', {
        **_base_context(request, filters, section='commission'),
        'due': due,
        'activity': activity,
        'institutions': institutions,
        'marketers': marketers,
    })


@login_required
@require_http_methods(['GET'])
def referrals_visits(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    report = build_attributed_visits(filters)
    return render(request, 'enterprise/referrals/visits.html', {
        **_base_context(request, filters, section='visits'),
        'report': report,
        'attributions': report['objects'],
    })


@login_required
@require_http_methods(['GET', 'POST'])
def referrals_payouts(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    if request.method == 'POST':
        return _handle_payout_action(request, filters)
    status = (request.GET.get('payout_status') or 'REQUESTED').strip().upper()
    if status not in ('REQUESTED', 'APPROVED', 'REJECTED', 'SETTLED', 'ALL'):
        status = 'REQUESTED'
    qs = WithdrawalRequest.objects.select_related(
        'doctor', 'doctor__centre', 'requested_by', 'reviewed_by',
    ).order_by('-requested_at')
    if filters.get('centre_ids') is not None:
        qs = qs.filter(doctor_id__in=doctor_ids_serving(filters['centre_ids']))
    if filters.get('doctor_id'):
        qs = qs.filter(doctor_id=filters['doctor_id'])
    if status != 'ALL':
        qs = qs.filter(status=status)
    if filters.get('q'):
        q = filters['q']
        qs = qs.filter(
            models_q_doctor_search(q)
        )
    balances = {
        str(row['doctor_id']): row['total'] or Decimal('0')
        for row in CommissionLedgerEntry.objects.values('doctor_id').annotate(total=Sum('amount'))
    }
    page = paginate(request, qs, 'page')
    rows = [
        {
            'withdrawal': row,
            'balance': balances.get(str(row.doctor_id), Decimal('0.00')),
        }
        for row in page
    ]
    return render(request, 'enterprise/referrals/payouts.html', {
        **_base_context(request, filters, section='payouts'),
        'payout_status': status,
        'directory': page,
        'rows': rows,
        'export_query': _export_query(filters, {'payout_status': status}),
    })


def models_q_doctor_search(q):
    from django.db.models import Q
    return (
        Q(doctor__doctor_code__icontains=q)
        | Q(doctor__first_name__icontains=q)
        | Q(doctor__last_name__icontains=q)
    )


def _handle_payout_action(request, filters):
    if not can_approve_referral_settlement(request.user):
        messages.error(request, 'You cannot review or settle referral payouts.')
        return redirect('enterprise-referrals-payouts')
    action = request.POST.get('action')
    withdrawal = get_object_or_404(
        WithdrawalRequest.objects.select_related('doctor', 'doctor__centre'),
        pk=request.POST.get('withdrawal'),
    )
    centre_ids = filters.get('centre_ids')
    if centre_ids is not None and not doctor_ids_serving(centre_ids).filter(pk=withdrawal.doctor_id).exists():
        raise PermissionDenied('That withdrawal is outside your centre scope.')
    note = (request.POST.get('review_note') or '')[:2000]
    try:
        if action == 'approve':
            if withdrawal.status != 'REQUESTED':
                raise ValidationError('Only requested withdrawals can be approved.')
            withdrawal.status = 'APPROVED'
            withdrawal.reviewed_by = request.user
            withdrawal.review_note = note
            withdrawal.save(update_fields=['status', 'reviewed_by', 'review_note', 'updated_at'])
            record_audit(
                request=request, actor=request.user, action='REFERRAL_WITHDRAWAL_REVIEWED',
                object_type='WithdrawalRequest', object_id=str(withdrawal.pk),
                centre=withdrawal.doctor.centre, detail={'decision': 'APPROVED'},
            )
            messages.success(request, f'Withdrawal for {withdrawal.doctor.doctor_code} approved.')
        elif action == 'reject':
            if withdrawal.status != 'REQUESTED':
                raise ValidationError('Only requested withdrawals can be rejected.')
            withdrawal.status = 'REJECTED'
            withdrawal.reviewed_by = request.user
            withdrawal.review_note = note or 'Rejected'
            withdrawal.save(update_fields=['status', 'reviewed_by', 'review_note', 'updated_at'])
            record_audit(
                request=request, actor=request.user, action='REFERRAL_WITHDRAWAL_REVIEWED',
                object_type='WithdrawalRequest', object_id=str(withdrawal.pk),
                centre=withdrawal.doctor.centre, detail={'decision': 'REJECTED'},
            )
            messages.success(request, f'Withdrawal for {withdrawal.doctor.doctor_code} rejected.')
        elif action == 'settle':
            if withdrawal.status != 'APPROVED':
                raise ValidationError('Approve the withdrawal before settling.')
            proof = request.FILES.get('proof_of_payment')
            if proof:
                withdrawal.proof_of_payment = proof
            if not withdrawal.proof_of_payment:
                raise ValidationError('Upload proof of payment before marking as settled.')
            with transaction.atomic():
                withdrawal.status = 'SETTLED'
                withdrawal.settled_at = timezone.now()
                withdrawal.reviewed_by = withdrawal.reviewed_by or request.user
                if note:
                    withdrawal.review_note = note
                withdrawal.save()
                CommissionLedgerEntry.objects.create(
                    doctor=withdrawal.doctor,
                    entry_type='SETTLEMENT',
                    amount=-withdrawal.amount,
                    description=f'Settlement for withdrawal {withdrawal.pk}',
                    created_by=request.user,
                )
            record_audit(
                request=request, actor=request.user, action='REFERRAL_WITHDRAWAL_SETTLED',
                object_type='WithdrawalRequest', object_id=str(withdrawal.pk),
                centre=withdrawal.doctor.centre, detail={'amount': str(withdrawal.amount)},
            )
            try:
                from communications.messaging import notify_payout_settled
                notify_payout_settled(withdrawal, user=request.user)
            except Exception:
                pass
            messages.success(request, f'Settled ₦{withdrawal.amount} for {withdrawal.doctor.doctor_code}.')
        else:
            raise ValidationError('Choose a payout action.')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
    suffix = f'?{_export_query(filters, {"payout_status": request.GET.get("payout_status") or "REQUESTED"})}'
    return redirect(f'/enterprise/referrals/payouts/{suffix}')


@login_required
@require_http_methods(['GET'])
def referrals_export(request):
    _guard(request)
    filters = parse_referral_filters(request.GET, request.user)
    fmt = (request.GET.get('format') or 'csv').lower()
    kind = (request.GET.get('kind') or 'commission_due').strip()
    builders = {
        'commission_due': build_commission_due,
        'activity': build_referral_activity,
        'institutions': build_institution_performance,
        'marketers': build_marketer_performance,
        'visits': build_attributed_visits,
        'doctors': None,
    }
    centre_label = filters['selected_centre'].code if filters.get('selected_centre') else ''
    if kind == 'doctors':
        doctors = scoped_doctors(filters, include_placeholders=False)
        balances = {
            str(row['doctor_id']): row['total'] or Decimal('0')
            for row in CommissionLedgerEntry.objects.filter(
                doctor_id__in=doctors.values('pk'),
            ).values('doctor_id').annotate(total=Sum('amount'))
        }
        headers = [
            'Doctor code', 'Physician', 'Facility', 'Centre', 'Commission', 'Active',
            'Outstanding balance', 'Bank', 'Account name', 'Account number', 'Marketer',
        ]
        rows = []
        for doctor in doctors:
            marketer = ''
            if doctor.onboarded_by_id:
                marketer = doctor.onboarded_by.get_username()
            rows.append([
                doctor.doctor_code,
                f'{doctor.first_name} {doctor.last_name}'.strip(),
                doctor.facility_name or '—',
                doctor.centre.code,
                (
                    f'{doctor.commission_value}%'
                    if doctor.commission_type == 'PERCENT'
                    else f'₦{doctor.commission_value} fixed'
                ),
                'Yes' if doctor.is_active else 'No',
                balances.get(str(doctor.pk), Decimal('0.00')),
                doctor.bank_name or '—',
                doctor.account_name or '—',
                doctor.account_number or '—',
                marketer or '—',
            ])
        title = 'Referring doctors directory'
        report = {'title': title, 'headers': headers, 'rows': rows, 'meta': {}}
    elif kind == 'commission_pack':
        due = build_commission_due(filters)
        activity = build_referral_activity(filters)
        institutions = build_institution_performance(filters)
        log_report_export(
            request, report_type='commission_due_pack', fmt=fmt,
            period_label=filters['period_label'], centre=centre_label,
            row_count=len(due['rows']) + len(activity['rows']),
        )
        return tables_export(
            'Referral commission pack',
            [
                {'title': due['title'], 'headers': due['headers'], 'rows': due['rows']},
                {'title': activity['title'], 'headers': activity['headers'], 'rows': activity['rows']},
                {'title': institutions['title'], 'headers': institutions['headers'], 'rows': institutions['rows']},
            ],
            fmt=fmt,
            basename=f"hfdn-referrals-commission-{filters['period']}-{filters['start_date'].replace('-', '')}",
            meta_lines=[f"{filters['period_label']} · {filters['start_date']} to {filters['end_date']}"],
        )
    else:
        builder = builders.get(kind) or build_commission_due
        report = builder(filters)
        title = report['title']

    log_report_export(
        request, report_type=kind, fmt=fmt,
        period_label=filters['period_label'], centre=centre_label,
        row_count=len(report['rows']),
        include_patient_ids=bool(report.get('meta', {}).get('include_patient_ids')),
    )
    return table_export(
        report['title'], report['headers'], report['rows'],
        fmt=fmt,
        basename=f"hfdn-referrals-{kind}-{filters['period']}-{filters['start_date'].replace('-', '')}",
        meta_lines=[f"{filters['period_label']} · {filters['start_date']} to {filters['end_date']}"],
    )
