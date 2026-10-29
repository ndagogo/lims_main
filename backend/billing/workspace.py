"""Accounts screen actions. Each one calls the billing API so the screen cannot bypass it."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from billing.api import DiscountRequestViewSet, InvoiceViewSet, PaymentViewSet
from billing.models import DiscountRequest, Invoice, Payment
from billing.reports import build_accountant_report, export_report
from core.access import can_approve_discount, can_reprint_invoice, is_accountant
from core.models import UserCentreAccess
from core.paging import paginate
from core.staff_api import call_action, call_create, describe
from laboratory.models import Tariff, TestPrice
from patients.models import Visit


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def handle_accounts_post(request):
    action = request.POST.get('action')
    user = request.user
    if action == 'create_invoice':
        status, body = call_create(InvoiceViewSet, user, {'visit': request.POST.get('visit'), 'payer_name': request.POST.get('payer_name', '')})
        return _finish(status, body, 'Invoice created.')
    if action == 'add_test':
        test_id = request.POST.get('test')
        tariff_id = request.POST.get('tariff')
        choice = request.POST.get('price_choice') or ''
        if '|' in choice:
            tariff_id, test_id = choice.split('|', 1)
        status, body = call_action(InvoiceViewSet, 'add_test', user, request.POST.get('invoice'), {
            'test': test_id, 'tariff': tariff_id, 'quantity': request.POST.get('quantity') or 1,
        })
        return _finish(status, body, 'Test added at the tariff price.')
    if action == 'pay':
        status, body = call_create(PaymentViewSet, user, {
            'invoice': request.POST.get('invoice'), 'amount': request.POST.get('amount'), 'method': request.POST.get('method') or 'CASH',
            'reference': request.POST.get('reference', ''),
        })
        if 200 <= status < 300:
            return True, f'Payment recorded. Receipt {body.get("receipt_number")} is ready to print.', body.get('id')
        return False, describe(body)
    if action == 'discount':
        status, body = call_create(DiscountRequestViewSet, user, {
            'invoice': request.POST.get('invoice'), 'discount_type': request.POST.get('discount_type') or 'PERCENT',
            'requested_value': request.POST.get('requested_value'), 'reason': request.POST.get('reason', ''),
        })
        return _finish(status, body, 'Discount request submitted.')
    if action == 'review_discount':
        if not can_approve_discount(user):
            return False, 'Only an accountant with discount approval privilege can review discounts.'
        status, body = call_action(DiscountRequestViewSet, 'review', user, request.POST.get('discount'), {
            'decision': request.POST.get('decision'), 'note': request.POST.get('note', ''),
        })
        return _finish(status, body, 'Discount review saved.')
    if action == 'reverse':
        if not is_accountant(user):
            return False, 'Only an accountant can reverse a payment.'
        status, body = call_action(PaymentViewSet, 'reverse', user, request.POST.get('payment'), {'reason': request.POST.get('reason', '')})
        return _finish(status, body, 'Payment reversed. The receipt amount was left unchanged.')
    if action == 'cancel':
        if not is_accountant(user):
            return False, 'Only an accountant can cancel an invoice.'
        status, body = call_action(InvoiceViewSet, 'cancel', user, request.POST.get('invoice'), {'reason': request.POST.get('reason', '')})
        return _finish(status, body, 'Invoice cancelled.')
    return False, 'Choose an accounts action.'


def _decorate(rows):
    for invoice in rows:
        paid = sum((payment.amount for payment in invoice.payments.all() if payment.status == 'CONFIRMED'), Decimal('0'))
        invoice.amount_paid = paid
        invoice.balance = (invoice.total or Decimal('0')) - paid
        invoice.balance_input = f'{invoice.balance:.2f}'
        pending = None
        for request_row in invoice.discount_requests.all():
            if request_row.status == 'PENDING':
                pending = request_row
                break
        invoice.pending_discount = pending
        invoice.discount_locked = pending is not None
    return rows


def receipt_payment(user, pk):
    payment = get_object_or_404(
        Payment.objects.select_related(
            'invoice__visit__patient', 'invoice__visit__centre', 'invoice__visit__client',
            'received_by', 'reversed_by',
        ).prefetch_related('invoice__items', 'invoice__payments'),
        pk=pk,
    )
    centre = payment.invoice.visit.centre
    allowed = user.is_superuser or UserCentreAccess.objects.filter(
        user=user, centre=centre, centre__is_active=True, can_view=True,
    ).exists()
    if not allowed:
        raise Http404()
    invoice = payment.invoice
    paid = sum((row.amount for row in invoice.payments.all() if row.status == 'CONFIRMED'), Decimal('0'))
    invoice.amount_paid = paid
    invoice.balance = (invoice.total or Decimal('0')) - paid
    return payment


def build_accounts_context(request):
    user = request.user
    access = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True)
    centre_ids = access.values_list('centre_id', flat=True)
    invoices = Invoice.objects.select_related('visit__patient', 'visit__centre').prefetch_related('items__test', 'items__imaging_order__procedure', 'payments', 'discount_requests').order_by('-created_at')
    payments = Payment.objects.select_related('invoice__visit__patient', 'invoice__visit__centre').order_by('-created_at')
    discounts = DiscountRequest.objects.select_related('invoice__visit__patient', 'invoice__visit__centre', 'invoice').order_by('-created_at')
    visits = Visit.objects.select_related('patient', 'centre').order_by('-created_at')
    if not user.is_superuser:
        invoices = invoices.filter(visit__centre_id__in=centre_ids)
        payments = payments.filter(invoice__visit__centre_id__in=centre_ids)
        discounts = discounts.filter(invoice__visit__centre_id__in=centre_ids)
        visits = visits.filter(centre_id__in=centre_ids)
    query = (request.GET.get('q') or '').strip()
    open_invoices = invoices.filter(status__in=['UNPAID', 'PART_PAID'])
    if query:
        open_invoices = open_invoices.filter(
            Q(visit__patient__first_name__icontains=query)
            | Q(visit__patient__last_name__icontains=query)
            | Q(visit__patient__other_names__icontains=query)
            | Q(visit__patient__uhid__icontains=query)
            | Q(visit__patient__phone_number__icontains=query)
            | Q(visit__visit_id__icontains=query)
            | Q(invoice_number__icontains=query)
        )
    # Cashier may collect only when no discount request is waiting on review.
    payable_dues = open_invoices.exclude(discount_requests__status='PENDING')
    due_page = paginate(request, payable_dues, 'dpage')
    _decorate(due_page.object_list)
    selected = None
    due_id = (request.GET.get('due') or '').strip()
    if due_id:
        try:
            selected = open_invoices.filter(pk=due_id).first()
        except (ValueError, ValidationError):
            selected = None
        if selected:
            _decorate([selected])
    accountant = is_accountant(user)
    approve_discount = can_approve_discount(user)
    my_requests = None
    if not approve_discount:
        my_requests = paginate(
            request,
            discounts.filter(requested_by=user).select_related(
                'invoice__visit__patient', 'invoice__visit__centre', 'reviewed_by',
            ).order_by('-created_at'),
            'mypage',
        )
    pending_page = None
    if approve_discount:
        pending_page = paginate(
            request,
            discounts.filter(status='PENDING').select_related(
                'invoice__visit__patient', 'invoice__visit__centre', 'requested_by',
            ).prefetch_related('invoice__payments'),
            'gpage',
        )
        _decorate_discounts(pending_page.object_list)
    reprint = can_reprint_invoice(user)
    return {
        'invoices': paginate(request, invoices, 'ipage') if (accountant or reprint) else None,
        'payments': paginate(request, payments, 'rpage'),
        'discounts': pending_page,
        'my_discount_requests': my_requests,
        'visits': visits[:40],
        'prices': TestPrice.objects.filter(tariff__is_active=True, test__is_active=True).select_related('test', 'tariff').order_by('test__name'),
        'tariffs': Tariff.objects.filter(is_active=True),
        'dues': due_page,
        'due_query': query,
        'selected_due': selected,
        'payment_methods': ['CASH', 'POS', 'TRANSFER', 'CARD', 'HMO', 'CREDIT'],
        'can_approve_discount': approve_discount,
        'can_cancel_invoice': accountant,
        'can_reverse_payment': accountant,
        'can_reprint_invoice': reprint,
        'is_accountant': accountant,
    }


def invoice_for_reprint(user, pk):
    """Return a centre-visible invoice for reprint, or raise Http404."""
    if not can_reprint_invoice(user):
        raise PermissionDenied('reprint_invoice privilege is required.')
    invoice = get_object_or_404(
        Invoice.objects.select_related(
            'visit__patient', 'visit__centre', 'visit__client', 'created_by', 'cancelled_by',
        ).prefetch_related('items__test', 'items__imaging_order__procedure', 'payments'),
        pk=pk,
    )
    if not user.is_superuser:
        allowed = UserCentreAccess.objects.filter(
            user=user, centre=invoice.visit.centre, centre__is_active=True, can_view=True,
        ).exists()
        if not allowed:
            raise Http404()
    _decorate([invoice])
    return invoice


def _decorate_discounts(rows):
    for discount in rows:
        invoice = discount.invoice
        subtotal = invoice.subtotal or Decimal('0')
        if discount.discount_type == 'PERCENT':
            discount.estimated_amount = (subtotal * (discount.requested_value or Decimal('0')) / Decimal('100')).quantize(Decimal('0.01'))
        else:
            discount.estimated_amount = discount.requested_value or Decimal('0')
        discount.invoice_balance = (invoice.total or Decimal('0')) - sum(
            (payment.amount for payment in invoice.payments.all() if payment.status == 'CONFIRMED'),
            Decimal('0'),
        )
    return rows


def build_accountant_context(request):
    if not is_accountant(request.user):
        raise PermissionDenied('Only an accountant can open financial reports.')
    report = build_accountant_report(
        request.user,
        period=request.GET.get('period') or 'daily',
        centre_id=request.GET.get('centre') or None,
        start=request.GET.get('start'),
        end=request.GET.get('end'),
    )
    report['can_approve_discount'] = can_approve_discount(request.user)
    report['is_accountant'] = True
    centre_ids = None
    if not request.user.is_superuser:
        centre_ids = UserCentreAccess.objects.filter(
            user=request.user, centre__is_active=True, can_view=True,
        ).values_list('centre_id', flat=True)
    pending = DiscountRequest.objects.filter(status='PENDING').select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'requested_by',
    ).prefetch_related('invoice__payments').order_by('-created_at')
    history = DiscountRequest.objects.select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'requested_by', 'reviewed_by',
    ).prefetch_related('invoice__payments').order_by('-created_at')
    if centre_ids is not None:
        pending = pending.filter(invoice__visit__centre_id__in=centre_ids)
        history = history.filter(invoice__visit__centre_id__in=centre_ids)
    status_filter = (request.GET.get('dstatus') or '').strip().upper()
    if status_filter in {'PENDING', 'APPROVED', 'REJECTED', 'CANCELLED'}:
        history = history.filter(status=status_filter)
    pending_page = paginate(request, pending, 'gpage')
    history_page = paginate(request, history, 'dhpage')
    _decorate_discounts(pending_page.object_list)
    _decorate_discounts(history_page.object_list)
    report['pending_discounts'] = pending_page
    report['discount_history'] = history_page
    report['discount_status_filter'] = status_filter
    return report


def export_accountant_report(request):
    if not is_accountant(request.user):
        raise PermissionDenied('Only an accountant can export financial reports.')
    report = build_accountant_report(
        request.user,
        period=request.GET.get('period') or 'daily',
        centre_id=request.GET.get('centre') or None,
        start=request.GET.get('start'),
        end=request.GET.get('end'),
    )
    fmt = request.GET.get('format') or 'csv'
    dataset = request.GET.get('dataset') or 'payments'
    from reporting.exports import log_report_export
    centre = report['selected_centre'].code if report.get('selected_centre') else ''
    row_map = {
        'payments': report.get('payment_rows') or [],
        'invoices': report.get('invoice_rows') or [],
        'discounts': report.get('discount_rows') or [],
        'visits': report.get('visit_rows') or [],
        'summary': list((report.get('kpis') or {}).keys()),
    }
    include_ids = dataset in ('payments', 'invoices', 'discounts', 'visits')
    log_report_export(
        request,
        report_type=f'accountant_{dataset}',
        fmt=fmt,
        period_label=report.get('period_label', ''),
        centre=centre,
        row_count=len(row_map.get(dataset, [])),
        include_patient_ids=include_ids,
    )
    return export_report(report, fmt=fmt, dataset=dataset)
