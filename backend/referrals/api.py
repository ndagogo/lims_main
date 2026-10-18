from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from core.access import ACCOUNTS_ROLES, require_any_role
from core.models import AuditEvent
from .models import ReferralDoctor, ReferralAttribution, CommissionLedgerEntry, WithdrawalRequest
from .serializers import ReferralDoctorSerializer, ReferralAttributionSerializer, CommissionLedgerEntrySerializer, WithdrawalRequestSerializer

def _user_can_see_doctor(user, doctor):
    from referrals.services import doctor_ids_serving
    ids = user.centre_access.filter(can_view=True).values('centre_id')
    return doctor_ids_serving(ids).filter(pk=doctor.pk).exists()

class ReferralDoctorViewSet(viewsets.ModelViewSet):
    serializer_class=ReferralDoctorSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self):
        qs=ReferralDoctor.objects.select_related('centre','onboarded_by').prefetch_related('centres')
        if not self.request.user.is_superuser:
            from referrals.services import doctor_ids_serving
            ids=self.request.user.centre_access.filter(can_view=True).values('centre_id')
            qs=qs.filter(pk__in=doctor_ids_serving(ids))
        return qs
    def perform_create(self, serializer):
        centre=serializer.validated_data['centre']
        if not self.request.user.is_superuser and not self.request.user.centre_access.filter(centre=centre,can_view=True,can_edit=True).exists():
            raise PermissionDenied('You do not have edit access to this centre.')
        obj=serializer.save(); AuditEvent.objects.create(actor=self.request.user,action='REFERRAL_DOCTOR_CREATED',object_type='ReferralDoctor',object_id=str(obj.pk),path=self.request.path,method=self.request.method,centre=obj.centre)

class ReferralAttributionViewSet(viewsets.ModelViewSet):
    serializer_class=ReferralAttributionSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self):
        qs=ReferralAttribution.objects.select_related('visit','doctor','doctor__centre')
        if not self.request.user.is_superuser:
            from referrals.services import doctor_ids_serving
            ids=self.request.user.centre_access.filter(can_view=True).values('centre_id')
            qs=qs.filter(doctor_id__in=doctor_ids_serving(ids))
        return qs

class CommissionLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=CommissionLedgerEntrySerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self):
        qs=CommissionLedgerEntry.objects.select_related('doctor','invoice')
        if not self.request.user.is_superuser:
            from referrals.services import doctor_ids_serving
            ids=self.request.user.centre_access.filter(can_view=True).values('centre_id')
            qs=qs.filter(doctor_id__in=doctor_ids_serving(ids))
        return qs
    @action(detail=False,methods=['get'])
    def balances(self, request):
        qs=self.get_queryset().values('doctor_id','doctor__doctor_code','doctor__first_name','doctor__last_name').annotate(balance=Sum('amount')).order_by('doctor__last_name')
        return Response(list(qs))

class WithdrawalRequestViewSet(viewsets.ModelViewSet):
    serializer_class=WithdrawalRequestSerializer; permission_classes=[IsAuthenticated]; http_method_names=['get','post','head','options']
    def get_queryset(self):
        qs=WithdrawalRequest.objects.select_related('doctor','doctor__centre')
        if not self.request.user.is_superuser:
            from referrals.services import doctor_ids_serving
            ids=self.request.user.centre_access.filter(can_view=True).values('centre_id')
            qs=qs.filter(doctor_id__in=doctor_ids_serving(ids))
        return qs
    def perform_create(self, serializer):
        doctor=serializer.validated_data['doctor']
        if not self.request.user.is_superuser and not _user_can_see_doctor(self.request.user, doctor):
            raise PermissionDenied('You do not have access to this centre.')
        current=doctor.ledger_entries.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        if serializer.validated_data['amount'] > current:
            raise PermissionDenied('Requested amount exceeds the available ledger balance.')
        # PDF: withdrawals may only be withdrawn after a 7-day hold on new commission credits.
        from datetime import timedelta
        from django.utils import timezone
        hold_until = timezone.now() - timedelta(days=7)
        recent = doctor.ledger_entries.filter(amount__gt=0, created_at__gt=hold_until).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        available = current - recent
        if serializer.validated_data['amount'] > available:
            raise PermissionDenied('Amount exceeds the balance available after the 7-day commission hold.')
        obj=serializer.save(requested_by=self.request.user)
        AuditEvent.objects.create(actor=self.request.user,action='REFERRAL_WITHDRAWAL_REQUESTED',object_type='WithdrawalRequest',object_id=str(obj.pk),path=self.request.path,method=self.request.method,centre=obj.doctor.centre,detail={'amount':str(obj.amount)})
        from communications.messaging import notify_withdrawal_request
        notify_withdrawal_request(obj, user=self.request.user)
    @action(detail=True,methods=['post'])
    def review(self, request, pk=None):
        obj=self.get_object()
        require_any_role(request.user, ACCOUNTS_ROLES, perm='referrals.approve_referral_settlement')
        if obj.status!='REQUESTED': return Response({'detail':'Only requested withdrawals can be reviewed.'},status=409)
        decision=request.data.get('decision'); note=str(request.data.get('note',''))[:2000]
        if decision not in ('APPROVED','REJECTED'): return Response({'detail':'decision must be APPROVED or REJECTED.'},status=400)
        if decision=='APPROVED' and obj.amount <= 0: return Response({'detail':'Amount must be positive.'},status=400)
        obj.status=decision; obj.reviewed_by=request.user; obj.review_note=note; obj.save(update_fields=['status','reviewed_by','review_note','updated_at'])
        AuditEvent.objects.create(actor=request.user,action='REFERRAL_WITHDRAWAL_REVIEWED',object_type='WithdrawalRequest',object_id=str(obj.pk),path=request.path,method=request.method,centre=obj.doctor.centre,detail={'decision':decision})
        return Response(self.get_serializer(obj).data)
    @action(detail=True,methods=['post'],parser_classes=[])
    def settle(self, request, pk=None):
        obj=self.get_object()
        require_any_role(request.user, ACCOUNTS_ROLES, perm='referrals.approve_referral_settlement')
        if obj.status!='APPROVED': return Response({'detail':'Withdrawal must be approved before settlement.'},status=409)
        if not obj.proof_of_payment: return Response({'detail':'Upload proof of payment in Django Admin before marking as settled.'},status=400)
        obj.status='SETTLED'; obj.settled_at=timezone.now(); obj.save(update_fields=['status','settled_at','updated_at'])
        CommissionLedgerEntry.objects.create(doctor=obj.doctor,entry_type='SETTLEMENT',amount=-obj.amount,description=f'Settlement for withdrawal {obj.pk}',created_by=request.user)
        AuditEvent.objects.create(actor=request.user,action='REFERRAL_WITHDRAWAL_SETTLED',object_type='WithdrawalRequest',object_id=str(obj.pk),path=request.path,method=request.method,centre=obj.doctor.centre,detail={'amount':str(obj.amount)})
        from communications.messaging import notify_payout_settled
        notify_payout_settled(obj, user=request.user)
        return Response(self.get_serializer(obj).data)
