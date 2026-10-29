"""Per-test cancel/refund and bill edit (remove/replace) with balanced totals."""
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import can_edit_bill, can_refund_payment
from core.models import AuditEvent
from laboratory.models import LabOrder, Test, TestPrice
from radiology.models import ImagingOrder, ImagingPrice

from .models import BillAmendment, Invoice, InvoiceItem, Payment, Refund


def _money(value):
    return (value or Decimal('0.00')).quantize(Decimal('0.01'))


def invoice_paid_total(invoice):
    return _money(
        Payment.objects.filter(invoice_id=invoice.pk, status='CONFIRMED').aggregate(total=Sum('amount'))['total']
    )


def invoice_refunded_total(invoice):
    return _money(
        Refund.objects.filter(invoice_id=invoice.pk, status='COMPLETED').aggregate(total=Sum('amount'))['total']
    )


def invoice_net_paid(invoice):
    return _money(invoice_paid_total(invoice) - invoice_refunded_total(invoice))


def invoice_balance(invoice):
    return _money(_money(invoice.total) - invoice_net_paid(invoice))


def _cancel_linked_order(item):
    if item.lab_order_id:
        order = item.lab_order
        if order.status != 'CANCELLED':
            order.status = 'CANCELLED'
            order.save(update_fields=['status', 'updated_at'])
            for sample in order.samples.exclude(status='CANCELLED'):
                sample.status = 'CANCELLED'
                sample.save(update_fields=['status', 'updated_at'])
    if item.imaging_order_id:
        order = item.imaging_order
        if order.status != 'CANCELLED':
            order.status = 'CANCELLED'
            order.save(update_fields=['status', 'updated_at'])
        exam = getattr(order, 'exam', None)
        if exam is not None and exam.status != 'CANCELLED':
            exam.status = 'CANCELLED'
            exam.save(update_fields=['status', 'updated_at'])


def cancel_or_refund_item(*, user, invoice_item_id, mode, reason):
    """Cancel a billed test; optionally refund its line total when money was collected."""
    if not can_refund_payment(user):
        raise PermissionDenied('payment_refund privilege is required.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required.')
    mode = (mode or '').strip().upper()
    if mode not in ('CANCEL_ONLY', 'CANCEL_AND_REFUND'):
        raise ValidationError('Choose Cancel only or Cancel and refund.')

    with transaction.atomic():
        item = InvoiceItem.objects.select_related(
            'invoice__visit__patient', 'invoice__visit__centre', 'test', 'lab_order', 'imaging_order',
        ).select_for_update().filter(pk=invoice_item_id).first()
        if item is None:
            raise ValidationError('Invoice line not found.')
        invoice = Invoice.objects.select_for_update().get(pk=item.invoice_id)
        if invoice.status == 'CANCELLED':
            raise ValidationError('Cannot adjust a cancelled invoice.')
        if Refund.objects.filter(invoice_item=item, status='COMPLETED').exists():
            raise ValidationError('This test has already been refunded.')

        line_total = _money(item.line_total)
        description = item.description or (item.test.name if item.test_id else str(item.pk))
        snapshot = {
            'invoice_item_id': str(item.pk),
            'description': description,
            'unit_price': str(item.unit_price),
            'quantity': item.quantity,
            'line_total': str(line_total),
            'test_id': str(item.test_id) if item.test_id else None,
            'lab_order_id': str(item.lab_order_id) if item.lab_order_id else None,
            'imaging_order_id': str(item.imaging_order_id) if item.imaging_order_id else None,
        }
        _cancel_linked_order(item)
        item.delete()
        invoice.recalculate()
        invoice.refresh_from_db()

        refund_amount = Decimal('0.00')
        payment = None
        if mode == 'CANCEL_AND_REFUND':
            net_paid = invoice_net_paid(invoice)
            # Cap refund to what was actually collected and still available.
            refund_amount = min(line_total, net_paid)
            if refund_amount <= 0:
                raise ValidationError('No confirmed payment remains to refund for this visit. Use Cancel only.')
            payment = (
                Payment.objects.filter(invoice=invoice, status='CONFIRMED')
                .order_by('-created_at')
                .first()
            )
            if payment is None:
                raise ValidationError('No confirmed payment is available to attach this refund.')

        refund = Refund.objects.create(
            payment=payment,
            invoice=invoice,
            invoice_item=None,
            amount=refund_amount,
            reason=reason,
            refund_mode=mode,
            status='COMPLETED',
            authorised_by=user,
            completed_at=timezone.now(),
            item_description=description,
        )
        BillAmendment.objects.create(
            invoice=invoice,
            action='REMOVE_ITEM',
            description=f'Cancelled: {description}',
            amount_delta=-line_total,
            reason=reason,
            authorised_by=user,
            invoice_item=None,
            removed_description=description,
            snapshot={**snapshot, 'refund_id': str(refund.pk), 'mode': mode},
        )
        invoice.recalculate()
        from core.audit import record_audit
        visit = invoice.visit
        record_audit(
            actor=user,
            action='TEST_CANCEL_REFUND' if mode == 'CANCEL_AND_REFUND' else 'TEST_CANCELLED',
            object_type='Refund',
            object_id=str(refund.pk),
            centre=visit.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '',
            visit_ref=visit.visit_id,
            reason=reason,
            detail={
                'invoice': invoice.invoice_number,
                'item': description,
                'mode': mode,
                'amount': str(refund_amount),
                'reason': reason,
                'visit_id': visit.visit_id,
            },
            category='BILLING',
        )
        try:
            from referrals.services import sync_commission
            sync_commission(invoice)
        except Exception:
            pass
        return refund


def replace_invoice_item(*, user, invoice_item_id, replacement_kind, replacement_id, tariff_id, reason):
    """Remove one billed test and replace it with another; totals must balance after recalculation."""
    if not can_edit_bill(user):
        raise PermissionDenied('bill_edit / bill_modify privilege is required.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required for bill edit.')
    kind = (replacement_kind or '').strip().lower()
    if kind not in ('laboratory', 'radiology'):
        raise ValidationError('Choose a laboratory or radiology replacement.')
    if not replacement_id or not tariff_id:
        raise ValidationError('Select the replacement test and tariff.')

    with transaction.atomic():
        item = InvoiceItem.objects.select_related(
            'invoice__visit__patient', 'invoice__visit__centre', 'test', 'lab_order', 'imaging_order',
        ).select_for_update().filter(pk=invoice_item_id).first()
        if item is None:
            raise ValidationError('Invoice line not found.')
        invoice = Invoice.objects.select_for_update().get(pk=item.invoice_id)
        if invoice.status == 'CANCELLED':
            raise ValidationError('Cannot edit a cancelled invoice.')
        if Refund.objects.filter(invoice_item=item, status='COMPLETED').exists():
            raise ValidationError('A refunded line cannot be replaced.')

        old_total = _money(item.line_total)
        removed_description = item.description or (item.test.name if item.test_id else str(item.pk))
        old_snapshot = {
            'invoice_item_id': str(item.pk),
            'description': removed_description,
            'line_total': str(old_total),
            'test_id': str(item.test_id) if item.test_id else None,
        }
        visit = invoice.visit
        _cancel_linked_order(item)
        item.delete()

        if kind == 'laboratory':
            price = TestPrice.objects.select_related('test', 'tariff').filter(
                test_id=replacement_id, tariff_id=tariff_id, test__is_active=True, tariff__is_active=True,
            ).first()
            if price is None:
                raise ValidationError('No price is configured for that laboratory test and tariff.')
            from laboratory.stat_catalog import default_stat
            order = LabOrder.objects.create(
                visit=visit, test=price.test, tariff=price.tariff, price=price.price, status='ORDERED',
                stat=default_stat(),
            )
            try:
                from workflows.services import bind_workflow
                bind_workflow(order)
            except Exception:
                pass
            new_item = InvoiceItem.objects.create(
                invoice=invoice, test=price.test, tariff=price.tariff, lab_order=order,
                unit_price=price.price, outsource_price=price.outsource_price, quantity=1,
            )
            added_description = price.test.name
        else:
            price = ImagingPrice.objects.select_related('procedure', 'tariff').filter(
                procedure_id=replacement_id, tariff_id=tariff_id,
                procedure__is_active=True, tariff__is_active=True,
            ).first()
            if price is None:
                raise ValidationError('No price is configured for that radiology procedure and tariff.')
            from radiology.models import ImagingModality, RadiologyExam
            procedure = price.procedure
            modality = ImagingModality.objects.filter(
                centre=visit.centre, modality_type=procedure.modality_type, is_active=True,
            ).order_by('name').first()
            if modality is None:
                raise ValidationError(
                    f'{procedure.name} needs an active {procedure.get_modality_type_display()} room at this centre.'
                )
            from laboratory.stat_catalog import default_stat
            order = ImagingOrder.objects.create(
                visit=visit, modality=modality, procedure=procedure,
                clinical_history=visit.clinical_history, requested_by=user, status='ORDERED',
                stat=default_stat(),
            )
            RadiologyExam.objects.create(order=order)
            new_item = InvoiceItem.objects.create(
                invoice=invoice, imaging_order=order, tariff=price.tariff,
                unit_price=price.price, quantity=1,
            )
            added_description = procedure.name

        new_total = _money(new_item.line_total)
        delta = _money(new_total - old_total)
        invoice.recalculate()
        invoice.refresh_from_db()

        # Financial sanity: totals must equal the sum of remaining lines.
        expected_subtotal = _money(
            InvoiceItem.objects.filter(invoice=invoice).aggregate(total=Sum('line_total'))['total']
        )
        if _money(invoice.subtotal) != expected_subtotal:
            raise ValidationError('Bill edit aborted: invoice subtotal does not match line items.')

        net_paid = invoice_net_paid(invoice)
        balance = _money(_money(invoice.total) - net_paid)
        if balance < 0 and abs(balance) > Decimal('0.01'):
            # Over-collection after a cheaper replacement is allowed; surface it clearly.
            pass

        amendment = BillAmendment.objects.create(
            invoice=invoice,
            action='REPLACE_ITEM',
            description=f'Replaced {removed_description} with {added_description}',
            amount_delta=delta,
            reason=reason,
            authorised_by=user,
            invoice_item=new_item,
            removed_description=removed_description,
            added_description=added_description,
            snapshot={
                'old': old_snapshot,
                'new': {
                    'invoice_item_id': str(new_item.pk),
                    'description': added_description,
                    'line_total': str(new_total),
                    'kind': kind,
                    'replacement_id': str(replacement_id),
                    'tariff_id': str(tariff_id),
                },
                'balance_after': str(balance),
                'net_paid': str(net_paid),
                'invoice_total': str(invoice.total),
            },
        )
        from core.audit import record_audit
        record_audit(
            actor=user,
            action='BILL_EDIT_REPLACE',
            object_type='BillAmendment',
            object_id=str(amendment.pk),
            centre=visit.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '',
            visit_ref=visit.visit_id,
            reason=reason,
            previous_values={'item': removed_description},
            new_values={'item': added_description, 'delta': str(delta)},
            detail={
                'invoice': invoice.invoice_number,
                'removed': removed_description,
                'added': added_description,
                'delta': str(delta),
                'balance_after': str(balance),
                'reason': reason,
                'visit_id': visit.visit_id,
            },
            category='BILLING',
        )
        try:
            from referrals.services import sync_commission
            sync_commission(invoice)
        except Exception:
            pass
        return amendment
