import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from core.models import TimeStampedModel, Centre
from patients.models import Visit
from billing.models import Invoice

class ReferralDoctor(TimeStampedModel):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT,related_name='referral_doctors')
    doctor_code=models.CharField(max_length=40,unique=True)
    title=models.CharField(max_length=30,blank=True)
    first_name=models.CharField(max_length=100)
    last_name=models.CharField(max_length=100)
    facility_name=models.CharField(max_length=180,blank=True)
    phone=models.CharField(max_length=30,blank=True)
    email=models.EmailField(blank=True)
    commission_type=models.CharField(max_length=10,choices=[('PERCENT','Percentage'),('FIXED','Fixed per eligible invoice')],default='PERCENT')
    commission_value=models.DecimalField(max_digits=10,decimal_places=2,validators=[MinValueValidator(Decimal('0'))])
    is_active=models.BooleanField(default=True)
    notes=models.TextField(blank=True)
    class Meta: ordering=['last_name','first_name']
    def __str__(self): return f'{self.doctor_code} — {self.first_name} {self.last_name}'

class ReferralInstitution(TimeStampedModel):
    """A hospital, clinic, or organisation through which one referring doctor sends patients."""
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    doctor=models.ForeignKey(ReferralDoctor,on_delete=models.CASCADE,related_name='institutions')
    name=models.CharField(max_length=180)
    is_active=models.BooleanField(default=True)
    class Meta:
        ordering=['name']
        constraints=[models.UniqueConstraint(fields=['doctor','name'],name='unique_doctor_institution_name')]
    def __str__(self): return self.name

class ReferralAttribution(TimeStampedModel):
    visit=models.OneToOneField(Visit,on_delete=models.PROTECT,related_name='referral_attribution')
    doctor=models.ForeignKey(ReferralDoctor,on_delete=models.PROTECT,related_name='attributions')
    institutions=models.ManyToManyField(ReferralInstitution,blank=True,related_name='attributions')
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    note=models.CharField(max_length=255,blank=True)

class CommissionLedgerEntry(TimeStampedModel):
    TYPES=[('ACCRUAL','Accrual'),('REVERSAL','Reversal'),('ADJUSTMENT','Adjustment'),('SETTLEMENT','Settlement')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    doctor=models.ForeignKey(ReferralDoctor,on_delete=models.PROTECT,related_name='ledger_entries')
    invoice=models.ForeignKey(Invoice,null=True,blank=True,on_delete=models.PROTECT,related_name='commission_entries')
    entry_type=models.CharField(max_length=12,choices=TYPES)
    amount=models.DecimalField(max_digits=12,decimal_places=2,help_text='Positive accrual, negative reversal/settlement.')
    currency=models.CharField(max_length=3,default='NGN')
    description=models.CharField(max_length=255)
    source_entry=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='reversals')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    class Meta: ordering=['created_at']; permissions=[('approve_referral_settlement','Can approve referral settlement')]
    def save(self,*args,**kwargs):
        if self.pk and CommissionLedgerEntry.objects.filter(pk=self.pk).exists():
            raise ValueError('Ledger entries are immutable. Create a reversal or adjustment instead.')
        super().save(*args,**kwargs)

class WithdrawalRequest(TimeStampedModel):
    STATUS=[('REQUESTED','Requested'),('APPROVED','Approved'),('REJECTED','Rejected'),('SETTLED','Settled')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    doctor=models.ForeignKey(ReferralDoctor,on_delete=models.PROTECT,related_name='withdrawals')
    amount=models.DecimalField(max_digits=12,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    requested_at=models.DateTimeField(auto_now_add=True)
    status=models.CharField(max_length=12,choices=STATUS,default='REQUESTED')
    proof_of_payment=models.FileField(upload_to='referral-settlements/%Y/%m/',null=True,blank=True)
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='referral_withdrawals_requested')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='referral_withdrawals_reviewed')
    review_note=models.TextField(blank=True)
    settled_at=models.DateTimeField(null=True,blank=True)
    class Meta: ordering=['-requested_at']
    def __str__(self): return f'{self.doctor.doctor_code} / {self.amount} / {self.status}'
