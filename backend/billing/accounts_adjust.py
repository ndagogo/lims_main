"""Staff screens for per-test refunds and bill edit."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q

from billing.adjustments import (
    _cancel_linked_order, _money, cancel_or_refund_item, invoice_balance,
    invoice_net_paid, replace_invoice_item,
)
from billing.models import BillAmendment, Invoice, InvoiceItem, Refund
from core.access import can_edit_bill, can_refund_payment
from core.models import AuditEvent, UserCentreAccess
from core.paging import paginate
from laboratory.models import Tariff, TestPrice
from patients.models import Visit
from radiology.models import ImagingPrice


def _centre_ids(user):
    if user.is_superuser:
        return None
    return list(
        UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True)
        .values_list('centre_id', flat=True)
    )


def _visible_visits(user):
    visits = Visit.objects.select_related('patient', 'centre').prefetch_related(
        Prefetch(
            'invoices',
            queryset=Invoice.objects.exclude(status='CANCELLED').prefetch_related(
                'items__test', 'items__imaging_order__procedure', 'payments', 'refunds',
            ),
        ),
    ).order_by('-created_at')
    centre_ids = _centre_ids(user)
    if centre_ids is not None:
        visits = visits.filter(centre_id__in=centre_ids)
    return visits


def find_visits(user, query):
    query = (query or '').strip()
    visits = _visible_visits(user)
    if not query:
        return visits.none()
    return visits.filter(
        Q(visit_id__icontains=query)
        | Q(patient__uhid__icontains=query)
        | Q(patient__phone_number__icontains=query)
        | Q(patient__first_name__icontains=query)
        | Q(patient__last_name__icontains=query)
        | Q(invoices__invoice_number__icontains=query)
    ).distinct()


def _decorate_visit(visit):
    for invoice in visit.invoices.all():
        paid = invoice_net_paid(invoice)
        invoice.amount_paid = paid
        invoice.balance = invoice_balance(invoice)
        invoice.active_items = [item for item in invoice.items.all()]
    return visit


def handle_refund_post(request):
    if not can_refund_payment(request.user):
        return False, 'payment_refund privilege is required.'
    action = request.POST.get('action')
    if action != 'cancel_refund_item':
        return False, 'Choose a refund action.'
    try:
        refund = cancel_or_refund_item(
            user=request.user,
            invoice_item_id=request.POST.get('invoice_item'),
            mode=request.POST.get('mode'),
            reason=request.POST.get('reason'),
        )
    except (ValidationError, PermissionDenied) as exc:
        messages = getattr(exc, 'messages', None)
        return False, ' '.join(messages) if messages else str(exc)
    mode_label = 'Cancelled and refunded' if refund.refund_mode == 'CANCEL_AND_REFUND' else 'Cancelled'
    amount = f' ₦{refund.amount:.2f}' if refund.amount else ''
    return True, f'{mode_label}: {refund.item_description}{amount}.'


def build_refunds_context(request):
    if not can_refund_payment(request.user):
        raise PermissionDenied('payment_refund privilege is required.')
    query = (request.GET.get('q') or '').strip()
    matches = find_visits(request.user, query) if query else _visible_visits(request.user).none()
    selected = None
    visit_id = (request.GET.get('visit') or '').strip()
    if visit_id:
        selected = _visible_visits(request.user).filter(pk=visit_id).first()
        if selected:
            _decorate_visit(selected)
    centre_ids = _centre_ids(request.user)
    history = Refund.objects.select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'authorised_by', 'payment',
    ).order_by('-created_at')
    if centre_ids is not None:
        history = history.filter(
            Q(invoice__visit__centre_id__in=centre_ids)
            | Q(payment__invoice__visit__centre_id__in=centre_ids)
        )
    return {
        'query': query,
        'matches': paginate(request, matches, 'vpage'),
        'selected_visit': selected,
        'refund_history': paginate(request, history, 'hpage'),
    }


def handle_bill_edit_post(request):
    if not can_edit_bill(request.user):
        return False, 'bill_edit / bill_modify privilege is required.'
    action = request.POST.get('action')
    if action == 'replace_item':
        try:
            amendment = replace_invoice_item(
                user=request.user,
                invoice_item_id=request.POST.get('invoice_item'),
                replacement_kind=request.POST.get('replacement_kind'),
                replacement_id=request.POST.get('replacement_id'),
                tariff_id=request.POST.get('tariff'),
                reason=request.POST.get('reason'),
            )
        except (ValidationError, PermissionDenied) as exc:
            messages = getattr(exc, 'messages', None)
            return False, ' '.join(messages) if messages else str(exc)
        return True, f'Bill updated: {amendment.description} (Δ ₦{amendment.amount_delta:.2f}).'
    if action == 'remove_item':
        try:
            description = _remove_bill_item(request.user, request.POST.get('invoice_item'), request.POST.get('reason'))
        except (ValidationError, PermissionDenied) as exc:
            messages = getattr(exc, 'messages', None)
            return False, ' '.join(messages) if messages else str(exc)
        return True, f'Removed from bill: {description}.'
    return False, 'Choose a bill edit action.'


def _remove_bill_item(user, invoice_item_id, reason):
    """Remove a line for bill_edit users without requiring payment_refund."""
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required.')
    with transaction.atomic():
        item = InvoiceItem.objects.select_related(
            'invoice__visit__centre', 'test', 'lab_order', 'imaging_order',
        ).select_for_update().filter(pk=invoice_item_id).first()
        if item is None:
            raise ValidationError('Invoice line not found.')
        invoice = Invoice.objects.select_for_update().get(pk=item.invoice_id)
        if invoice.status == 'CANCELLED':
            raise ValidationError('Cannot edit a cancelled invoice.')
        line_total = _money(item.line_total)
        description = item.description or (item.test.name if item.test_id else str(item.pk))
        _cancel_linked_order(item)
        item.delete()
        invoice.recalculate()
        amendment = BillAmendment.objects.create(
            invoice=invoice,
            action='REMOVE_ITEM',
            description=f'Removed: {description}',
            amount_delta=-line_total,
            reason=reason,
            authorised_by=user,
            removed_description=description,
            snapshot={'description': description, 'line_total': str(line_total)},
        )
        AuditEvent.objects.create(
            actor=user, action='BILL_EDIT_REMOVE', object_type='BillAmendment',
            object_id=str(amendment.pk), centre=invoice.visit.centre,
            detail={'invoice': invoice.invoice_number, 'item': description, 'reason': reason},
        )
        return description


def build_bill_edit_context(request):
    if not can_edit_bill(request.user):
        raise PermissionDenied('bill_edit / bill_modify privilege is required.')
    query = (request.GET.get('q') or '').strip()
    matches = find_visits(request.user, query) if query else _visible_visits(request.user).none()
    selected = None
    visit_id = (request.GET.get('visit') or '').strip()
    if visit_id:
        selected = _visible_visits(request.user).filter(pk=visit_id).first()
        if selected:
            _decorate_visit(selected)
    centre_ids = _centre_ids(request.user)
    history = BillAmendment.objects.select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'authorised_by',
    ).filter(action__in=['REMOVE_ITEM', 'REPLACE_ITEM', 'ADD_ITEM', 'ADJUST_PRICE']).order_by('-created_at')
    if centre_ids is not None:
        history = history.filter(invoice__visit__centre_id__in=centre_ids)
    test_catalog = []
    for row in TestPrice.objects.filter(tariff__is_active=True, test__is_active=True).select_related('test', 'tariff'):
        test_catalog.append({
            'id': str(row.test_id), 'tariff': str(row.tariff_id), 'kind': 'laboratory',
            'code': row.test.code, 'name': row.test.name, 'price': float(row.price),
        })
    for row in ImagingPrice.objects.filter(tariff__is_active=True, procedure__is_active=True).select_related('procedure', 'tariff'):
        test_catalog.append({
            'id': str(row.procedure_id), 'tariff': str(row.tariff_id), 'kind': 'radiology',
            'code': row.procedure.code, 'name': row.procedure.name, 'price': float(row.price),
        })
    return {
        'query': query,
        'matches': paginate(request, matches, 'vpage'),
        'selected_visit': selected,
        'amendment_history': paginate(request, history, 'hpage'),
        'tariffs': Tariff.objects.filter(is_active=True).order_by('name'),
        'test_catalog': test_catalog,
    }
