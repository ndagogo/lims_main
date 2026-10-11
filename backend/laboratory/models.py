import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from core.models import TimeStampedModel, Centre, SequenceCounter
from patients.models import Visit

class ServiceGroup(TimeStampedModel):
    """Named lab service group; single-digit code is embedded in sample IDs."""
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=1, help_text='Single digit used in sample IDs')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'code']
        constraints = [
            models.UniqueConstraint(fields=['name', 'code'], name='uniq_service_group_name_code'),
        ]

    def __str__(self):
        return f'{self.code} — {self.name}'

    def clean(self):
        if self.code and not self.code.isdigit():
            raise ValidationError({'code': 'Service group code must be a single digit.'})


class Test(TimeStampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=160)
    department = models.CharField(max_length=80, default='PATHOLOGY')
    department_code = models.CharField(max_length=1, default='1')
    service_group = models.ForeignKey(
        ServiceGroup, null=True, blank=True, on_delete=models.PROTECT, related_name='tests',
    )
    # Denormalized from service_group for sample ID generation and legacy filters.
    service_group_code = models.CharField(max_length=1, default='1')
    specimen = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.service_group_id:
            self.service_group_code = self.service_group.code
        super().save(*args, **kwargs)

    def __str__(self): return f'{self.code} — {self.name}'

class Tariff(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

class TestPrice(TimeStampedModel):
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT)
    test = models.ForeignKey(Test, on_delete=models.PROTECT)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    outsource_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    class Meta: unique_together=[('tariff','test')]

class LabOrder(TimeStampedModel):
    STATUS_CHOICES=[('ORDERED','Ordered'),('COLLECTION_PENDING','Collection pending'),('IN_PROGRESS','In progress'),('COMPLETED','Completed'),('CANCELLED','Cancelled')]
    id=models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit=models.ForeignKey(Visit,on_delete=models.PROTECT,related_name='lab_orders')
    test=models.ForeignKey(Test,on_delete=models.PROTECT)
    tariff=models.ForeignKey(Tariff,null=True,on_delete=models.PROTECT)
    price=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    status=models.CharField(max_length=24,choices=STATUS_CHOICES,default='ORDERED')
    def __str__(self): return f'{self.visit.visit_id} / {self.test.code}'

class SampleCodeConfig(TimeStampedModel):
    centre=models.OneToOneField(Centre,on_delete=models.PROTECT,related_name='sample_code_config')
    numeric_code=models.CharField(max_length=1, unique=True, help_text='One digit assigned to this centre, e.g. 2 for NOWAS')
    is_active=models.BooleanField(default=True)
    def __str__(self): return f'{self.centre.code}: {self.numeric_code}'

class Sample(TimeStampedModel):
    STATUS_CHOICES=[('PENDING_COLLECTION','Pending collection'),('COLLECTED','Collected'),('BATCHED','Batched'),('RECEIVED','Department received'),('PROCESSING','Processing'),('RESULT_ENTERED','Result entered'),('UNDER_REVIEW','Under review'),('AUTHENTICATED','Authenticated'),('RELEASED','Released'),('REJECTED','Rejected'),('CANCELLED','Cancelled')]
    sample_id=models.CharField(max_length=8,unique=True,editable=False)
    order=models.ForeignKey(LabOrder,on_delete=models.PROTECT,related_name='samples')
    collected_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='samples_collected')
    collected_at=models.DateTimeField(null=True,blank=True)
    collector_ip=models.GenericIPAddressField(null=True,blank=True)
    collector_browser=models.CharField(max_length=255,blank=True)
    status=models.CharField(max_length=24,choices=STATUS_CHOICES,default='PENDING_COLLECTION')
    recollection_of=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='recollections')
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT,related_name='samples')
    rejection_reason=models.TextField(blank=True)
    def save(self,*args,**kwargs):
        if not self.sample_id:
            with transaction.atomic():
                config=SampleCodeConfig.objects.select_for_update().get(centre=self.centre,is_active=True)
                dept=self.order.test.department_code
                group=self.order.test.service_group_code
                if not (dept.isdigit() and group.isdigit()): raise ValidationError('Department and service group codes must be single digits.')
                key=f'sample-{config.numeric_code}-{dept}-{group}'
                counter,_=SequenceCounter.objects.select_for_update().get_or_create(key=key)
                counter.value+=1; counter.save(update_fields=['value'])
                if counter.value > 99999: raise ValidationError('Sample sequence exhausted for this centre/department/service group.')
                self.sample_id=f'{config.numeric_code}{dept}{group}{counter.value:05d}'
                super().save(*args,**kwargs)
        else: super().save(*args,**kwargs)
    @property
    def patient(self): return self.order.visit.patient
    def __str__(self): return self.sample_id

class SampleActivity(TimeStampedModel):
    sample=models.ForeignKey(Sample,on_delete=models.PROTECT,related_name='activities')
    action=models.CharField(max_length=40)
    from_status=models.CharField(max_length=24,blank=True)
    to_status=models.CharField(max_length=24,blank=True)
    note=models.TextField(blank=True)
    performed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    ip_address=models.GenericIPAddressField(null=True,blank=True)
    browser=models.CharField(max_length=255,blank=True)
    class Meta: ordering=['created_at']

class SampleBatch(TimeStampedModel):
    STATUS_CHOICES=[('OPEN','Open'),('DISPATCHED','Dispatched'),('RECEIVED','Received'),('CLOSED','Closed')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    batch_code=models.CharField(max_length=32,unique=True,editable=False)
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT,related_name='sample_batches')
    department=models.CharField(max_length=80)
    status=models.CharField(max_length=12,choices=STATUS_CHOICES,default='OPEN')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='batches_created')
    dispatched_at=models.DateTimeField(null=True,blank=True)
    received_at=models.DateTimeField(null=True,blank=True)
    received_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='batches_received')
    def save(self,*args,**kwargs):
        if not self.batch_code:
            with transaction.atomic():
                counter,_=SequenceCounter.objects.select_for_update().get_or_create(key=f'batch-{self.centre.code}')
                counter.value+=1; counter.save(update_fields=['value'])
                self.batch_code=f'{self.centre.code}-B{counter.value:07d}'
        super().save(*args,**kwargs)
    def __str__(self): return self.batch_code

class BatchSample(TimeStampedModel):
    batch=models.ForeignKey(SampleBatch,on_delete=models.PROTECT,related_name='items')
    sample=models.ForeignKey(Sample,on_delete=models.PROTECT,related_name='batch_memberships')
    added_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    class Meta: constraints=[models.UniqueConstraint(fields=['batch','sample'],name='uniq_sample_per_batch')]

class SampleProcessing(TimeStampedModel):
    sample=models.ForeignKey(Sample,on_delete=models.PROTECT,related_name='processing_records')
    action=models.CharField(max_length=100)
    notes=models.TextField(blank=True)
    performed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    started_at=models.DateTimeField(default=timezone.now)
    completed_at=models.DateTimeField(null=True,blank=True)

class TestParameter(TimeStampedModel):
    RESULT_TYPES=[('TEXT','Text'),('NUMBER','Number'),('CHOICE','Choice'),('BOOLEAN','Boolean')]
    test=models.ForeignKey(Test,on_delete=models.PROTECT,related_name='parameters')
    code=models.CharField(max_length=40)
    name=models.CharField(max_length=100)
    result_type=models.CharField(max_length=12,choices=RESULT_TYPES,default='TEXT')
    unit=models.CharField(max_length=40,blank=True)
    reference_range=models.CharField(max_length=120,blank=True)
    display_order=models.PositiveSmallIntegerField(default=0)
    is_required=models.BooleanField(default=True)
    class Meta: ordering=['display_order','name']; constraints=[models.UniqueConstraint(fields=['test','code'],name='uniq_test_parameter_code')]
    def __str__(self): return f'{self.test.code} / {self.name}'

class ResultTemplate(TimeStampedModel):
    test=models.ForeignKey(Test,on_delete=models.PROTECT,related_name='result_templates')
    name=models.CharField(max_length=120)
    version=models.PositiveIntegerField(default=1)
    instructions=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['test','name','version'],name='uniq_result_template_version')]

class LabResult(TimeStampedModel):
    STATUS_CHOICES=[('DRAFT','Draft'),('SUBMITTED','Submitted for review'),('REVIEWED','Reviewed'),('AUTHENTICATED','Authenticated'),('RELEASED','Released'),('AMENDED','Amended')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    sample=models.OneToOneField(Sample,on_delete=models.PROTECT,related_name='result')
    template=models.ForeignKey(ResultTemplate,null=True,blank=True,on_delete=models.PROTECT)
    status=models.CharField(max_length=20,choices=STATUS_CHOICES,default='DRAFT')
    narrative=models.TextField(blank=True)
    entered_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='lab_results_entered')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='lab_results_reviewed')
    authenticated_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='lab_results_authenticated')
    released_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='lab_results_released')
    entered_at=models.DateTimeField(default=timezone.now)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    authenticated_at=models.DateTimeField(null=True,blank=True)
    released_at=models.DateTimeField(null=True,blank=True)
    version=models.PositiveIntegerField(default=1)

    class Meta:
        permissions = [
            ('authenticate_labresult', 'Can authenticate a laboratory result'),
            ('release_labresult', 'Can release a laboratory result'),
            ('amend_labresult', 'Can amend a released laboratory result'),
        ]

class ResultValue(TimeStampedModel):
    result=models.ForeignKey(LabResult,on_delete=models.PROTECT,related_name='values')
    parameter=models.ForeignKey(TestParameter,on_delete=models.PROTECT)
    value=models.TextField(blank=True)
    flag=models.CharField(max_length=20,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['result','parameter'],name='uniq_result_parameter')]

class ReferenceRange(TimeStampedModel):
    SEX = [('', 'Any'), ('M', 'Male'), ('F', 'Female')]
    parameter = models.ForeignKey(TestParameter, on_delete=models.CASCADE, related_name='reference_ranges')
    sex = models.CharField(max_length=1, blank=True, choices=SEX)
    age_min_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    age_max_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    display_text = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['parameter__display_order', 'sex', 'age_min_years']


class QcMaterial(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='qc_materials')
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name='qc_materials')
    name = models.CharField(max_length=120)
    lot_number = models.CharField(max_length=80, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.name} ({self.lot_number})'


class QcTarget(TimeStampedModel):
    material = models.ForeignKey(QcMaterial, on_delete=models.CASCADE, related_name='targets')
    parameter = models.ForeignKey(TestParameter, on_delete=models.PROTECT)
    low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['material', 'parameter'], name='uniq_qc_target')]


class QcRun(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.ForeignKey(QcMaterial, on_delete=models.PROTECT, related_name='runs')
    parameter = models.ForeignKey(TestParameter, on_delete=models.PROTECT)
    value = models.CharField(max_length=80)
    acceptable = models.BooleanField(default=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    run_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-run_at']


class ResultReview(TimeStampedModel):
    result=models.ForeignKey(LabResult,on_delete=models.PROTECT,related_name='reviews')
    action=models.CharField(max_length=20,choices=[('SUBMITTED','Submitted'),('APPROVED','Approved'),('REJECTED','Rejected'),('AUTHENTICATED','Authenticated'),('RELEASED','Released'),('AMENDED','Amended')])
    reviewer=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    note=models.TextField(blank=True)
    snapshot=models.JSONField(default=dict,blank=True)
