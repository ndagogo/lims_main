from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from core.access import ACCOUNTS_ROLES, require_any_role, require_workspace
from core.audit import record_audit
from core.models import UserCentreAccess
from core.permissions import HasCentreAccess
from communications.messaging import notify_discount_approvers, notify_discount_decision
from .eligibility import release_eligibility_payload
from .models import Invoice, InvoiceItem, Payment, DiscountRequest, BillAmendment, Refund
from .serializers import (
    InvoiceSerializer, InvoiceItemSerializer, PaymentSerializer, DiscountRequestSerializer,
    BillAmendmentSerializer, RefundSerializer,
)

def centre_qs(qs, user, path):
    if user.is_superuser: return qs
    return qs.filter(**{f'{path}__centre__user_access__user':user, f'{path}__centre__user_access__can_view':True, f'{path}__centre__is_active':True}).distinct()

class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class=InvoiceSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]; search_fields=['invoice_number','visit__visit_id']; filterset_fields=['status','visit']
    def get_queryset(self): return centre_qs(Invoice.objects.select_related('visit__centre','visit__patient').prefetch_related('items__test','payments'), self.request.user, 'visit')
    def create(self, request, *args, **kwargs):
        require_workspace(request.user, 'accounts')
        visit_id=request.data.get('visit')
        from patients.models import Visit
        visit=Visit.objects.filter(pk=visit_id).select_related('centre').first()
        if not visit: raise ValidationError({'visit':'Visit not found.'})
        if not request.user.is_superuser and not UserCentreAccess.objects.filter(user=request.user,centre=visit.centre,can_register=True).exists(): raise PermissionDenied('No billing/order-entry access for this centre.')
        invoice=Invoice.objects.create(visit=visit,payer_name=request.data.get('payer_name',''),created_by=request.user,status='UNPAID')
        return Response(self.get_serializer(invoice).data,status=status.HTTP_201_CREATED)
    @action(detail=True, methods=['post'])
    def add_test(self, request, pk=None):
        require_workspace(request.user, 'accounts')
        invoice=self.get_object()
        if invoice.status in ('PAID','CANCELLED'): raise ValidationError('Cannot change a paid or cancelled invoice.')
        test_id=request.data.get('test'); tariff_id=request.data.get('tariff'); order_id=request.data.get('lab_order')
        from laboratory.models import Test, Tariff, TestPrice, LabOrder
        test=Test.objects.filter(pk=test_id,is_active=True).first()
        tariff=Tariff.objects.filter(pk=tariff_id,is_active=True).first() if tariff_id else None
        if not test: raise ValidationError({'test':'Select a valid active test.'})
        price=TestPrice.objects.filter(test=test,tariff=tariff).first() if tariff else None
        if not price: raise ValidationError({'price':'No price is configured for this test and tariff. Contact the System Administrator.'})
        order=LabOrder.objects.filter(pk=order_id,visit=invoice.visit,test=test).first() if order_id else None
        with transaction.atomic():
            if order is None:
                order=LabOrder.objects.create(visit=invoice.visit,test=test,tariff=tariff,price=price.price,status='ORDERED')
            item=InvoiceItem.objects.create(invoice=invoice,test=test,tariff=tariff,lab_order=order,unit_price=price.price,outsource_price=price.outsource_price,quantity=int(request.data.get('quantity',1)))
            if order: order.price=price.price; order.tariff=tariff; order.save(update_fields=['price','tariff','updated_at'])
        return Response(InvoiceItemSerializer(item).data,status=201)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        invoice=self.get_object()
        require_any_role(request.user, ACCOUNTS_ROLES, perm='billing.cancel_invoice')
        if invoice.status == 'CANCELLED':
            raise ValidationError('This invoice is already cancelled.')
        if invoice.payments.filter(status='CONFIRMED').exists():
            raise ValidationError('Reverse each confirmed payment before cancelling this invoice.')
        reason=(request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A cancellation reason is required.'})
        invoice.status='CANCELLED'
        invoice.cancel_reason=reason
        invoice.cancelled_by=request.user
        invoice.cancelled_at=timezone.now()
        invoice.save(update_fields=['status', 'cancel_reason', 'cancelled_by', 'cancelled_at', 'updated_at'])
        visit = invoice.visit
        record_audit(
            request=request, actor=request.user, action='INVOICE_CANCELLED',
            object_type='Invoice', object_id=str(invoice.pk), centre=visit.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            reason=reason, previous_values={'status': 'OPEN'}, new_values={'status': 'CANCELLED'},
            detail={'invoice': invoice.invoice_number, 'reason': reason, 'visit_id': visit.visit_id},
            category='BILLING',
        )
        return Response(self.get_serializer(invoice).data)

class InvoiceItemViewSet(viewsets.ModelViewSet):
    serializer_class=InvoiceItemSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]
    def get_queryset(self): return centre_qs(InvoiceItem.objects.select_related('invoice__visit__centre','test'),self.request.user,'invoice__visit')
    def perform_create(self, serializer):
        require_workspace(self.request.user, 'accounts')
        invoice=serializer.validated_data['invoice']
        if not self.request.user.is_superuser and not UserCentreAccess.objects.filter(user=self.request.user,centre=invoice.visit.centre,can_register=True).exists(): raise PermissionDenied('No access to this invoice centre.')
        if invoice.status in ('PAID','CANCELLED'): raise ValidationError('Cannot add items to a paid or cancelled invoice.')
        serializer.save()
    def perform_update(self, serializer):
        item=self.get_object()
        target_invoice=serializer.validated_data.get('invoice', item.invoice)
        if target_invoice.visit.centre_id != item.invoice.visit.centre_id: raise ValidationError('Invoice item cannot be moved between centres.')
        if item.invoice.status == 'PAID': raise ValidationError('Paid invoice items cannot be edited; use a controlled reversal workflow.')
        serializer.save()
    def perform_destroy(self, instance):
        if instance.invoice.status == 'PAID': raise ValidationError('Paid invoice items cannot be deleted; use a controlled reversal workflow.')
        instance.delete(); instance.invoice.recalculate()

class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class=PaymentSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]; http_method_names=['get','post','head','options']
    def get_queryset(self): return centre_qs(Payment.objects.select_related('invoice__visit__centre'),self.request.user,'invoice__visit')
    def perform_create(self, serializer):
        require_workspace(self.request.user, 'accounts')
        invoice=serializer.validated_data['invoice']; centre=invoice.visit.centre
        if not self.request.user.is_superuser and not UserCentreAccess.objects.filter(user=self.request.user,centre=centre,can_edit=True).exists(): raise PermissionDenied('Billing permission required.')
        payment = serializer.save(received_by=self.request.user)
        visit = invoice.visit
        record_audit(
            request=self.request, actor=self.request.user, action='PAYMENT_RECORDED',
            object_type='Payment', object_id=str(payment.pk), centre=centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            new_values={'amount': str(payment.amount), 'method': payment.method, 'status': payment.status},
            detail={
                'invoice': invoice.invoice_number, 'receipt': payment.receipt_number,
                'amount': str(payment.amount), 'method': payment.method, 'visit_id': visit.visit_id,
            },
            category='BILLING',
        )
        from communications.messaging import notify_payment_receipt
        notify_payment_receipt(payment, user=self.request.user)
    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        payment=self.get_object()
        require_any_role(request.user, ACCOUNTS_ROLES, perm='billing.reverse_payment')
        centre=payment.invoice.visit.centre
        if not request.user.is_superuser and not UserCentreAccess.objects.filter(user=request.user, centre=centre, can_edit=True).exists():
            raise PermissionDenied('Billing permission required.')
        if payment.status != 'CONFIRMED':
            raise ValidationError('Only a confirmed payment can be reversed.')
        reason=(request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A reversal reason is required.'})
        payment.status='REVERSED'
        payment.reversal_reason=reason
        payment.reversed_by=request.user
        payment.reversed_at=timezone.now()
        payment.save()
        visit = payment.invoice.visit
        record_audit(
            request=request, actor=request.user, action='PAYMENT_REVERSED',
            object_type='Payment', object_id=str(payment.pk), centre=centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            reason=reason, previous_values={'status': 'CONFIRMED'}, new_values={'status': 'REVERSED'},
            detail={'receipt': payment.receipt_number, 'amount': str(payment.amount), 'reason': reason, 'visit_id': visit.visit_id},
            category='BILLING',
        )
        return Response(self.get_serializer(payment).data)

class DiscountRequestViewSet(viewsets.ModelViewSet):
    serializer_class=DiscountRequestSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]; http_method_names=['get','post','head','options']
    def get_queryset(self): return centre_qs(DiscountRequest.objects.select_related('invoice__visit__centre'),self.request.user,'invoice__visit')
    def perform_create(self, serializer):
        require_workspace(self.request.user, 'accounts')
        invoice=serializer.validated_data['invoice']
        if not self.request.user.is_superuser and not UserCentreAccess.objects.filter(user=self.request.user,centre=invoice.visit.centre,can_register=True).exists(): raise PermissionDenied('Cannot request a discount at this centre.')
        discount = serializer.save(requested_by=self.request.user)
        notify_discount_approvers(discount, user=self.request.user)
        visit = invoice.visit
        record_audit(
            request=self.request, actor=self.request.user, action='DISCOUNT_REQUESTED',
            object_type='DiscountRequest', object_id=str(discount.pk), centre=visit.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            reason=discount.reason or '',
            new_values={'discount_type': discount.discount_type, 'requested_value': str(discount.requested_value)},
            detail={'invoice': invoice.invoice_number, 'visit_id': visit.visit_id, 'reason': discount.reason},
            category='BILLING',
        )
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        discount=self.get_object()
        require_any_role(request.user, ACCOUNTS_ROLES, perm='billing.approve_discount')
        if discount.status != 'PENDING': raise ValidationError('Only pending requests can be reviewed.')
        decision=request.data.get('decision')
        if decision not in ('APPROVED','REJECTED'): raise ValidationError({'decision':'Use APPROVED or REJECTED.'})
        with transaction.atomic():
            invoice=Invoice.objects.select_for_update().get(pk=discount.invoice_id)
            if decision == 'APPROVED':
                value=discount.requested_value
                amount=(invoice.subtotal*value/Decimal('100')) if discount.discount_type=='PERCENT' else value
                if amount > invoice.subtotal: raise ValidationError('Discount cannot exceed invoice subtotal.')
                invoice.discount_amount=amount; invoice.recalculate()
                discount.applied_amount=amount
            discount.status=decision; discount.reviewed_by=request.user; discount.review_note=request.data.get('note','')
            discount.save(update_fields=['status','reviewed_by','review_note','applied_amount','updated_at'])
            visit = invoice.visit
            record_audit(
                request=request, actor=request.user, action=f'DISCOUNT_{decision}',
                object_type='DiscountRequest', object_id=str(discount.pk), centre=visit.centre,
                patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
                reason=discount.review_note or discount.reason or '',
                previous_values={'status': 'PENDING'}, new_values={'status': decision, 'applied_amount': str(discount.applied_amount)},
                detail={'invoice': invoice.invoice_number, 'amount': str(discount.applied_amount), 'visit_id': visit.visit_id},
                category='BILLING',
            )
        notify_discount_decision(discount, user=request.user)
        return Response(self.get_serializer(discount).data)


class BillAmendmentViewSet(viewsets.ModelViewSet):
    serializer_class = BillAmendmentSerializer
    permission_classes = [IsAuthenticated, HasCentreAccess]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return centre_qs(BillAmendment.objects.select_related('invoice__visit__centre'), self.request.user, 'invoice__visit')

    def perform_create(self, serializer):
        require_any_role(self.request.user, ACCOUNTS_ROLES, perm='billing.bill_edit')
        invoice = serializer.validated_data['invoice']
        if invoice.status == 'CANCELLED':
            raise ValidationError('Cannot amend a cancelled invoice.')
        amendment = serializer.save(authorised_by=self.request.user)
        visit = invoice.visit
        record_audit(
            request=self.request, actor=self.request.user, action='BILL_AMENDMENT',
            object_type='BillAmendment', object_id=str(amendment.pk), centre=visit.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            reason=getattr(amendment, 'reason', '') or '',
            new_values={'action': amendment.action, 'delta': str(amendment.amount_delta)},
            detail={'action': amendment.action, 'delta': str(amendment.amount_delta), 'visit_id': visit.visit_id},
            category='BILLING',
        )


class RefundViewSet(viewsets.ModelViewSet):
    serializer_class = RefundSerializer
    permission_classes = [IsAuthenticated, HasCentreAccess]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = Refund.objects.select_related(
            'payment__invoice__visit__centre', 'invoice__visit__centre',
        )
        if self.request.user.is_superuser:
            return qs
        from django.db.models import Q
        return qs.filter(
            Q(payment__invoice__visit__centre__user_access__user=self.request.user,
              payment__invoice__visit__centre__user_access__can_view=True,
              payment__invoice__visit__centre__is_active=True)
            | Q(invoice__visit__centre__user_access__user=self.request.user,
                invoice__visit__centre__user_access__can_view=True,
                invoice__visit__centre__is_active=True)
        ).distinct()

    def perform_create(self, serializer):
        require_any_role(self.request.user, ACCOUNTS_ROLES, perm='billing.payment_refund')
        payment = serializer.validated_data.get('payment')
        invoice = serializer.validated_data.get('invoice')
        if payment is None and invoice is None:
            raise ValidationError('A payment or invoice is required.')
        if payment is not None and payment.status != 'CONFIRMED':
            raise ValidationError('Only confirmed payments can be refunded.')
        centre = (payment.invoice.visit.centre if payment else invoice.visit.centre)
        refund = serializer.save(authorised_by=self.request.user, status='COMPLETED', completed_at=timezone.now())
        from referrals.services import sync_commission
        sync_commission(payment.invoice if payment else invoice)
        visit = (payment.invoice.visit if payment else invoice.visit)
        record_audit(
            request=self.request, actor=self.request.user, action='REFUND_ISSUED',
            object_type='Refund', object_id=str(refund.pk), centre=centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '', visit_ref=visit.visit_id,
            new_values={'amount': str(refund.amount)},
            detail={
                'amount': str(refund.amount), 'receipt': getattr(payment, 'receipt_number', ''),
                'visit_id': visit.visit_id,
            },
            category='BILLING',
        )


from rest_framework.views import APIView


class ReleaseEligibilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        visit_id = request.query_params.get('visit')
        if not visit_id:
            raise ValidationError({'visit': 'Required.'})
        from patients.models import Visit
        visit = Visit.objects.filter(pk=visit_id).first()
        if not visit:
            raise ValidationError({'visit': 'Not found.'})
        if not request.user.is_superuser and not UserCentreAccess.objects.filter(
            user=request.user, centre=visit.centre, can_view=True,
        ).exists():
            raise PermissionDenied('No access to this centre.')
        return Response(release_eligibility_payload(visit))
