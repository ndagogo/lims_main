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
        qs = ServiceGroup.objects.filter(name__iexact=self.name)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            raise ValidationError({'name': 'Hey, Service group already exists.'})


class Test(TimeStampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=160)
    department = models.CharField(max_length=80, default='PATHOLOGY')
    department_code = models.CharField(max_length=1, default='1')
    service_group = models.ForeignKey(
        ServiceGroup, on_delete=models.PROTECT, related_name='tests',
    )
    # Denormalized from service_group for sample ID generation and legacy filters.
    service_group_code = models.CharField(max_length=1, default='1')
    specimen = models.CharField(max_length=80, blank=True, help_text='Specimen type, e.g. EDTA whole blood.')
    collection_requirements = models.TextField(blank=True, help_text='Phlebotomy / collection requirements.')
    sample_volume = models.CharField(max_length=40, blank=True, help_text='Required sample volume, e.g. 3 mL.')
    container = models.CharField(max_length=80, blank=True, help_text='Container / tube type.')
    handling_instructions = models.TextField(blank=True, help_text='Transport and handling instructions.')
    default_workflow_code = models.CharField(
        max_length=40, blank=True, default='',
        help_text='Preferred workflow template code (e.g. NORMAL). Assignments still take precedence.',
    )
    tat_target_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Catalogue turnaround target in minutes. Dedicated TatRule may override.',
    )
    is_outsourceable = models.BooleanField(default=False)
    default_outsource = models.BooleanField(
        default=False,
        help_text='When true, new orders for this test default to outsourced pricing/path if configured.',
    )
    comments_required = models.BooleanField(
        default=False,
        help_text='Require a narrative/comment before result submission.',
    )
    default_template = models.ForeignKey(
        'ResultTemplate', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='default_for_tests',
    )
    is_active = models.BooleanField(default=True)

    def clean(self):
        if not self.service_group_id:
            raise ValidationError({'service_group': 'Each test must be mapped to a service group.'})
        if self.default_template_id and self.default_template.test_id != self.pk:
            raise ValidationError({'default_template': 'Default template must belong to this test.'})

    def save(self, *args, **kwargs):
        if not self.service_group_id:
            raise ValidationError({'service_group': 'Each test must be mapped to a service group.'})
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
    STATUS_CHOICES=[
        ('PENDING_COLLECTION','Pending collection'),('COLLECTED','Collected'),('BATCHED','Batched'),
        ('RECEIVED','Department received'),('PROCESSING','Processing'),('RESULT_ENTERED','Result entered'),
        ('UNDER_REVIEW','Under review'),('AUTHENTICATED','Authenticated'),('RELEASED','Released'),
        ('REJECTED','Rejected'),('SUPERSEDED','Superseded by recollection'),('CANCELLED','Cancelled'),
    ]
    DISPOSITIONS=[
        ('', '—'),
        ('REJECTED', 'Rejected'),
        ('UNSUITABLE', 'Unsuitable'),
        ('SUPERSEDED', 'Superseded'),
    ]
    TERMINAL_STATUSES = frozenset({'RELEASED', 'CANCELLED', 'REJECTED', 'SUPERSEDED'})
    sample_id=models.CharField(max_length=8,unique=True,editable=False)
    order=models.ForeignKey(LabOrder,on_delete=models.PROTECT,related_name='samples')
    collected_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='samples_collected')
    collected_at=models.DateTimeField(null=True,blank=True)
    collector_ip=models.GenericIPAddressField(null=True,blank=True)
    collector_browser=models.CharField(max_length=255,blank=True)
    specimen_type=models.CharField(max_length=80,blank=True,help_text='Snapshot of specimen type at collection.')
    status=models.CharField(max_length=24,choices=STATUS_CHOICES,default='PENDING_COLLECTION')
    recollection_of=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='recollections')
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT,related_name='samples')
    rejection_reason=models.TextField(blank=True)
    disposition=models.CharField(max_length=12,choices=DISPOSITIONS,blank=True,default='')
    corrective_action=models.TextField(blank=True,help_text='Corrective action taken after rejection or unsuitable sample.')
    rejected_by=models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='samples_rejected',
    )
    rejected_at=models.DateTimeField(null=True,blank=True)
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


class SampleBarcodePrint(TimeStampedModel):
    """Print history for sample barcodes — does not alter the sample lifecycle."""
    SOURCES = [
        ('LABEL_PAGE', 'Label page'),
        ('AUTO_PRINT', 'Auto-print after collect'),
        ('API', 'API'),
        ('MANUAL', 'Manual record'),
    ]
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name='barcode_prints')
    barcode_value = models.CharField(max_length=32, help_text='Value encoded in the barcode (usually sample_id).')
    printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='sample_barcode_prints',
    )
    printed_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=16, choices=SOURCES, default='LABEL_PAGE')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-printed_at']

    def __str__(self):
        return f'{self.barcode_value} @ {self.printed_at}'

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
    RESULT_TYPES = [
        ('NUMBER', 'Numeric with unit'),
        ('TEXT', 'Free-text interpretation'),
        ('CHOICE', 'Single-select'),
        ('MULTI_CHOICE', 'Multi-select'),
        ('BOOLEAN', 'Yes / No'),
        ('QUALITATIVE', 'Qualitative (e.g. Positive/Negative)'),
        ('CALCULATED', 'Calculated parameter'),
        ('TABLE', 'Table / differential row'),
        ('COMMENT', 'Comment / specimen note'),
        ('SECTION', 'Approved report section heading'),
    ]
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name='parameters')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    result_type = models.CharField(max_length=12, choices=RESULT_TYPES, default='NUMBER')
    unit = models.CharField(max_length=40, blank=True)
    decimal_places = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Decimal precision for numeric results. Empty = no forced rounding.',
    )
    reference_range = models.CharField(
        max_length=120, blank=True,
        help_text='Legacy display fallback. Prefer age/sex ReferenceRange rows.',
    )
    choices = models.JSONField(
        default=list, blank=True,
        help_text='Allowed values for CHOICE / MULTI_CHOICE / QUALITATIVE, e.g. ["Positive","Negative"].',
    )
    calculation = models.CharField(
        max_length=255, blank=True,
        help_text='Simple expression using sibling parameter codes, e.g. NEUT_ABS = WBC * NEUT_PCT / 100.',
    )
    section_code = models.CharField(
        max_length=40, blank=True, default='',
        help_text='Group key for differentials / tables (e.g. DIFF).',
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_required = models.BooleanField(default=True)
    require_comment_if_abnormal = models.BooleanField(default=False)
    critical_comment_required = models.BooleanField(
        default=False,
        help_text='When a critical flag fires, require a documented comment.',
    )

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [models.UniqueConstraint(fields=['test', 'code'], name='uniq_test_parameter_code')]

    def __str__(self):
        return f'{self.test.code} / {self.name}'


class ResultTemplate(TimeStampedModel):
    STATUS = [
        ('DRAFT', 'Draft'),
        ('PUBLISHED', 'Published'),
        ('RETIRED', 'Retired'),
    ]
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name='result_templates')
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS, default='DRAFT')
    instructions = models.TextField(blank=True)
    report_header = models.TextField(blank=True, help_text='Approved header section for clinical reports.')
    report_footer = models.TextField(blank=True, help_text='Approved footer / disclaimer section.')
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='result_templates_published',
    )
    cloned_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='clones',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['test', 'name', 'version'], name='uniq_result_template_version'),
        ]
        ordering = ['test__code', 'name', '-version']

    def __str__(self):
        return f'{self.test.code} / {self.name} v{self.version}'


class ResultTemplateField(TimeStampedModel):
    """Ordered membership of catalogue parameters in a versioned result template."""
    template = models.ForeignKey(ResultTemplate, on_delete=models.CASCADE, related_name='fields')
    parameter = models.ForeignKey(TestParameter, on_delete=models.PROTECT, related_name='template_fields')
    display_order = models.PositiveSmallIntegerField(default=0)
    is_required = models.BooleanField(
        null=True, blank=True,
        help_text='Override parameter.is_required when set.',
    )
    show_reference_range = models.BooleanField(default=True)
    include_in_report = models.BooleanField(default=True)
    section_label = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['display_order', 'parameter__name']
        constraints = [
            models.UniqueConstraint(fields=['template', 'parameter'], name='uniq_template_parameter'),
        ]

    def clean(self):
        if self.parameter_id and self.template_id and self.parameter.test_id != self.template.test_id:
            raise ValidationError({'parameter': 'Parameter must belong to the same test as the template.'})


class LabResult(TimeStampedModel):
    STATUS_CHOICES=[('DRAFT','Draft'),('SUBMITTED','Submitted for review'),('REVIEWED','Reviewed'),('AUTHENTICATED','Authenticated'),('RELEASED','Released'),('AMENDED','Amended')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    sample=models.OneToOneField(Sample,on_delete=models.PROTECT,related_name='result')
    template=models.ForeignKey(ResultTemplate,null=True,blank=True,on_delete=models.PROTECT)
    template_version=models.PositiveIntegerField(null=True,blank=True,editable=False)
    configuration_snapshot=models.JSONField(
        default=dict, blank=True,
        help_text='Frozen template, parameters and reference ranges at authentication.',
    )
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
    comment=models.TextField(blank=True)
    reference_display=models.CharField(max_length=120,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['result','parameter'],name='uniq_result_parameter')]

class ReferenceRange(TimeStampedModel):
    SEX = [('', 'Any'), ('M', 'Male'), ('F', 'Female')]
    parameter = models.ForeignKey(TestParameter, on_delete=models.CASCADE, related_name='reference_ranges')
    population_label = models.CharField(
        max_length=80, blank=True,
        help_text='Laboratory-approved population label, e.g. Adult, Neonate.',
    )
    sex = models.CharField(max_length=1, blank=True, choices=SEX)
    age_min_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    age_max_years = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    display_text = models.CharField(max_length=120, blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

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


class TatPauseReason(TimeStampedModel):
    """Documented reasons that may pause the TAT clock."""
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    pauses_clock = models.BooleanField(
        default=True,
        help_text='When false the reason is logged but does not stop the TAT clock.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class TatRule(TimeStampedModel):
    """Expected turnaround and escalation for a test or service group."""
    CLOCK_START = [
        ('REGISTRATION', 'Visit registration'),
        ('PAYMENT', 'Payment confirmation'),
        ('COLLECTION', 'Sample collection'),
        ('RECEIVE', 'Department receiving'),
    ]
    CLOCK_END = [
        ('RELEASE', 'Controlled result release'),
        ('AUTHENTICATE', 'Result authentication'),
        ('REVIEW', 'First review complete'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    test = models.ForeignKey(
        Test, null=True, blank=True, on_delete=models.CASCADE, related_name='tat_rules',
    )
    service_group = models.ForeignKey(
        ServiceGroup, null=True, blank=True, on_delete=models.CASCADE, related_name='tat_rules',
    )
    department = models.CharField(
        max_length=80, blank=True, default='',
        help_text='Optional department filter when matching service-group rules.',
    )
    clock_start = models.CharField(max_length=20, choices=CLOCK_START, default='PAYMENT')
    clock_end = models.CharField(max_length=20, choices=CLOCK_END, default='RELEASE')
    expected_minutes = models.PositiveIntegerField(
        help_text='Target turnaround in minutes (pause time excluded when pauses are allowed).',
    )
    warning_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Optional early warning threshold. Defaults to 80% of expected.',
    )
    escalation_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Minutes after which an overdue escalation alert is raised. Defaults to expected.',
    )
    escalation_roles = models.JSONField(
        default=list, blank=True,
        help_text='Group names notified / responsible for escalation.',
    )
    allow_pause = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(test__isnull=False) | models.Q(service_group__isnull=False),
                name='tat_rule_requires_test_or_group',
            ),
        ]

    def __str__(self):
        target = self.test.code if self.test_id else (self.service_group.name if self.service_group_id else '?')
        return f'{self.name} ({target}: {self.expected_minutes}m)'

    def clean(self):
        if not self.test_id and not self.service_group_id:
            raise ValidationError('Bind the rule to a test or a service group.')
        if self.expected_minutes < 1:
            raise ValidationError({'expected_minutes': 'Expected TAT must be at least one minute.'})


class TatPause(TimeStampedModel):
    """Documented pause on a sample's TAT clock."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='tat_pauses')
    reason = models.ForeignKey(TatPauseReason, on_delete=models.PROTECT, related_name='pauses')
    note = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tat_pauses_started',
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tat_pauses_ended',
    )
    start_ip = models.GenericIPAddressField(null=True, blank=True)
    start_browser = models.CharField(max_length=255, blank=True)
    end_ip = models.GenericIPAddressField(null=True, blank=True)
    end_browser = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-started_at']

    @property
    def is_open(self):
        return self.ended_at is None


class TatEscalation(TimeStampedModel):
    """Configurable escalation / overdue alert for a sample."""
    LEVELS = [
        ('WARNING', 'Warning'),
        ('OVERDUE', 'Overdue'),
        ('ESCALATED', 'Escalated'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name='tat_escalations')
    rule = models.ForeignKey(TatRule, null=True, blank=True, on_delete=models.SET_NULL, related_name='escalations')
    level = models.CharField(max_length=12, choices=LEVELS)
    message = models.CharField(max_length=255, blank=True)
    elapsed_minutes = models.PositiveIntegerField(default=0)
    expected_minutes = models.PositiveIntegerField(default=0)
    fired_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tat_escalations_acked',
    )
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-fired_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sample', 'level'],
                condition=models.Q(acknowledged_at__isnull=True),
                name='uniq_open_tat_escalation_level',
            ),
        ]
