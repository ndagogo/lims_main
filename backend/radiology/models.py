import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from core.models import TimeStampedModel, Centre
from patients.models import Visit

class ImagingModality(TimeStampedModel):
    MODALITY_TYPES = [
        ('XRAY', 'X-ray'), ('CT', 'CT'), ('MRI', 'MRI'), ('US', 'Ultrasound'),
        ('MAMMO', 'Mammography'), ('ECG', 'ECG'), ('OTHER', 'Other'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='imaging_modalities')
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    modality_type = models.CharField(max_length=10, choices=MODALITY_TYPES)
    ae_title = models.CharField(max_length=16, blank=True, help_text='DICOM AE title of this modality workstation.')
    host = models.GenericIPAddressField(null=True, blank=True, help_text='IP address of the modality workstation.')
    port = models.PositiveIntegerField(null=True, blank=True, help_text='DICOM port on the modality workstation.')
    wado_url = models.URLField(blank=True, help_text='Optional external PACS or WADO viewer address.')
    worklist_enabled = models.BooleanField(
        default=True,
        help_text='Paid radiology worklist rows for this room are offered to the modality.',
    )
    last_echo_ok = models.BooleanField(null=True, blank=True)
    last_echo_at = models.DateTimeField(null=True, blank=True)
    last_echo_message = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['centre__name', 'name']
        constraints = [models.UniqueConstraint(fields=['centre', 'code'], name='uniq_modality_code_per_centre')]

    def __str__(self):
        return f'{self.centre.code} / {self.code} — {self.name}'

class ImagingOrder(TimeStampedModel):
    PRIORITIES = [('ROUTINE', 'Routine'), ('URGENT', 'Urgent'), ('STAT', 'STAT')]
    STATUSES = [('ORDERED', 'Ordered'), ('SCHEDULED', 'Scheduled'), ('IN_PROGRESS', 'In progress'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='imaging_orders')
    modality = models.ForeignKey(ImagingModality, on_delete=models.PROTECT, related_name='orders')
    procedure = models.ForeignKey('ImagingProcedure', null=True, blank=True, on_delete=models.PROTECT, related_name='orders')
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='ROUTINE')
    stat = models.ForeignKey(
        'laboratory.StatType', null=True, blank=True, on_delete=models.PROTECT, related_name='imaging_orders',
    )
    clinical_history = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='imaging_orders_requested')
    status = models.CharField(max_length=12, choices=STATUSES, default='ORDERED')
    scheduled_at = models.DateTimeField(null=True, blank=True)

    @property
    def centre(self):
        return self.visit.centre

    def clean(self):
        if self.visit_id and self.modality_id and self.visit.centre_id != self.modality.centre_id:
            raise ValidationError({'modality': 'The modality must belong to the same centre as the visit.'})

    def __str__(self):
        return f'{self.visit.visit_id} / {self.modality.code}'

class RadiologyExam(TimeStampedModel):
    STATUSES = [('ASSIGNED', 'Assigned'), ('IN_PROGRESS', 'In progress'), ('ACQUIRED', 'Acquired'), ('CANCELLED', 'Cancelled')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(ImagingOrder, on_delete=models.PROTECT, related_name='exam')
    assigned_radiographer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_exams_assigned')
    assigned_radiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_exams_reporting',
    )
    reporting_substitute = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_exams_substitute',
        help_text='Authorised substitute reporting radiologist under controlled policy.',
    )
    substitute_reason = models.TextField(blank=True)
    substitute_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_substitutes_authorized',
    )
    substitute_authorized_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_exams_started')
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_exams_ended')
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default='ASSIGNED')
    acquisition_notes = models.TextField(blank=True)
    pacs_study_uid = models.CharField(max_length=128, blank=True, default='')
    pacs_series_uids = models.JSONField(default=list, blank=True)
    pacs_reconciled_at = models.DateTimeField(null=True, blank=True)
    mwl_status = models.CharField(max_length=20, blank=True, default='')
    pacs_uploaded_at = models.DateTimeField(null=True, blank=True)
    pacs_uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_exams_uploaded',
    )
    radiologist_assigned_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set when the assigned radiologist did not accept before the window ended.',
    )

    class Meta:
        permissions = [
            ('perform_exam', 'Can start and end an imaging exam'),
            ('authorize_reporting_substitute', 'Can authorise a reporting substitute radiologist'),
        ]

    @property
    def centre(self):
        return self.order.visit.centre


class ExamSupplyUse(TimeStampedModel):
    """Consumables recorded when a radiographer ends an exam."""
    exam = models.ForeignKey(RadiologyExam, on_delete=models.CASCADE, related_name='supplies')
    item_name = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['item_name']


class RadiologistAcceptRecord(TimeStampedModel):
    """Whether an assigned radiologist accepted a study inside the platform window."""
    OUTCOMES = [('ACCEPTED', 'Accepted'), ('MISSED', 'Not accepted in time')]
    exam = models.ForeignKey(RadiologyExam, on_delete=models.PROTECT, related_name='accept_records')
    radiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='radiology_accept_records',
    )
    assigned_at = models.DateTimeField()
    closed_at = models.DateTimeField()
    window_minutes = models.PositiveIntegerField()
    outcome = models.CharField(max_length=12, choices=OUTCOMES)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-closed_at']


class RadiologyDeskSetting(TimeStampedModel):
    """How long an assigned radiologist has to accept a study before it can be reassigned."""
    accept_minutes = models.PositiveIntegerField(default=30)

    def __str__(self):
        return f'Accept within {self.accept_minutes} minutes'


class RadiologyExamActivity(TimeStampedModel):
    """Immutable exam history (Start Exam / End Exam / assign / PACS)."""
    exam = models.ForeignKey(RadiologyExam, on_delete=models.PROTECT, related_name='activities')
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=12, blank=True)
    to_status = models.CharField(max_length=12, blank=True)
    note = models.TextField(blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']


class PacsFailure(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(RadiologyExam, on_delete=models.CASCADE, related_name='pacs_failures')
    reason = models.CharField(max_length=500)
    resolved_at = models.DateTimeField(null=True, blank=True)


class RadiologyReportTemplate(TimeStampedModel):
    """Versioned reporting layouts — platform catalogue or personal radiologist variants."""
    SCOPE = [('PLATFORM', 'Platform'), ('PERSONAL', 'Personal')]
    STATUS = [('DRAFT', 'Draft'), ('PUBLISHED', 'Published'), ('RETIRED', 'Retired')]
    VISIBILITY = [
        ('PUBLIC', 'Public (Shared with Everyone)'),
        ('PRIVATE', 'Private (Only Me)'),
    ]
    modality_type = models.CharField(max_length=10, choices=ImagingModality.MODALITY_TYPES)
    name = models.CharField(max_length=120)
    variant_name = models.CharField(max_length=120, blank=True, help_text='Optional variant label, e.g. Adult / Paediatric.')
    body_part = models.CharField(max_length=120, blank=True, help_text='Anatomical focus, e.g. Chest, Abdomen.')
    visibility = models.CharField(max_length=10, choices=VISIBILITY, default='PUBLIC')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS, default='DRAFT')
    scope = models.CharField(max_length=10, choices=SCOPE, default='PLATFORM')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name='radiology_report_templates',
        help_text='Null for platform templates; set for personal radiologist variants.',
    )
    procedure = models.ForeignKey(
        'ImagingProcedure', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='report_templates',
    )
    instructions = models.TextField(blank=True)
    report_header = models.TextField(blank=True)
    indication_boilerplate = models.TextField(blank=True, help_text='Clinical indication / clinical information.')
    technique_boilerplate = models.TextField(blank=True, help_text='Acquisition / technique section.')
    findings_boilerplate = models.TextField(blank=True)
    impression_boilerplate = models.TextField(blank=True)
    recommendations_boilerplate = models.TextField(blank=True)
    report_footer = models.TextField(blank=True)
    body_html = models.TextField(
        blank=True,
        help_text='Rich-text report body (HTML) composed in the template builder.',
    )
    extra_fields = models.JSONField(
        default=list, blank=True,
        help_text='Approved modality-specific fields: [{"code":"BI_RADS","label":"BI-RADS","boilerplate":""}].',
    )
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_templates_published',
    )
    cloned_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='clones',
    )

    class Meta:
        ordering = ['modality_type', 'name', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['modality_type', 'name', 'version'],
                condition=models.Q(owner__isnull=True),
                name='uniq_platform_rad_report_template',
            ),
            models.UniqueConstraint(
                fields=['modality_type', 'name', 'version', 'owner'],
                condition=models.Q(owner__isnull=False),
                name='uniq_personal_rad_report_template',
            ),
        ]

    def __str__(self):
        who = self.owner_id or 'platform'
        return f'{self.modality_type} / {self.name} v{self.version} ({who})'


class RadiologyReport(TimeStampedModel):
    STATUSES = [
        ('DRAFT', 'Draft'),
        ('FINAL', 'Reported'),
        ('APPROVED', 'Approved'),
        ('RELEASED', 'Dispatched'),
        ('AMENDED', 'Dispatched (amended)'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.OneToOneField(RadiologyExam, on_delete=models.PROTECT, related_name='report')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='radiology_reports_authored')
    template = models.ForeignKey(
        RadiologyReportTemplate, null=True, blank=True, on_delete=models.PROTECT,
        related_name='reports',
    )
    template_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    configuration_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text='Frozen template content at sign-off.',
    )
    status = models.CharField(max_length=10, choices=STATUSES, default='DRAFT')
    indication = models.TextField(blank=True)
    technique = models.TextField(blank=True)
    findings = models.TextField(blank=True)
    impression = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    extra_values = models.JSONField(
        default=dict, blank=True,
        help_text='Values for template extra_fields keyed by field code.',
    )
    addendum = models.TextField(
        blank=True,
        help_text='Cumulative formal addenda after sign/release (append-only via controlled action).',
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_reports_finalized')
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_reports_approved',
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='radiology_reports_released',
    )
    last_override_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_reports_overridden')
    last_override_at = models.DateTimeField(null=True, blank=True)
    last_override_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        permissions = [
            ('finalize_report', 'Can finalise a radiology report'),
            ('release_report', 'Can release a signed radiology report'),
            ('approve_result', 'Can approve a reported radiology result'),
            ('authenticate_result', 'Can authenticate a reported radiology result'),
            ('dispatch_result', 'Can dispatch an approved radiology result'),
            ('modify_result', 'Can return a radiology result for modification'),
            ('override_report', 'Can amend a final radiology report'),
            ('add_report_addendum', 'Can add a formal addendum to a signed radiology report'),
        ]

    @property
    def centre(self):
        return self.exam.order.visit.centre

class RadiologyReportRevision(TimeStampedModel):
    CHANGE_TYPES = [
        ('AMENDMENT', 'Amendment'),
        ('ADDENDUM', 'Addendum'),
    ]
    report = models.ForeignKey(RadiologyReport, on_delete=models.PROTECT, related_name='revisions')
    version = models.PositiveIntegerField()
    change_type = models.CharField(max_length=12, choices=CHANGE_TYPES, default='AMENDMENT')
    snapshot = models.JSONField(default=dict)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.TextField()

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['report', 'version'], name='uniq_radiology_report_version')]


class ImagingProcedure(TimeStampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=160)
    modality_type = models.CharField(max_length=10, choices=ImagingModality.MODALITY_TYPES)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.code} — {self.name}'


class ImagingPrice(TimeStampedModel):
    tariff = models.ForeignKey('laboratory.Tariff', on_delete=models.PROTECT)
    procedure = models.ForeignKey(ImagingProcedure, on_delete=models.PROTECT, related_name='prices')
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tariff', 'procedure'], name='uniq_imaging_price')]


class ImagingFile(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(RadiologyExam, on_delete=models.PROTECT, related_name='files')
    kind = models.CharField(max_length=10)
    file = models.FileField(upload_to='radiology/%Y/%m/')
    original_name = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    @property
    def centre(self):
        return self.exam.order.visit.centre
