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
    ae_title = models.CharField(max_length=16, blank=True)
    host = models.GenericIPAddressField(null=True, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    wado_url = models.URLField(blank=True, help_text='Optional external PACS or WADO viewer address.')
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
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_exams_started')
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_exams_ended')
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default='ASSIGNED')
    acquisition_notes = models.TextField(blank=True)

    class Meta:
        permissions = [('perform_exam', 'Can start and end an imaging exam')]

    @property
    def centre(self):
        return self.order.visit.centre

class RadiologyReport(TimeStampedModel):
    STATUSES = [('DRAFT', 'Draft'), ('FINAL', 'Final'), ('AMENDED', 'Amended')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.OneToOneField(RadiologyExam, on_delete=models.PROTECT, related_name='report')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='radiology_reports_authored')
    status = models.CharField(max_length=10, choices=STATUSES, default='DRAFT')
    findings = models.TextField(blank=True)
    impression = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_reports_finalized')
    last_override_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='radiology_reports_overridden')
    last_override_at = models.DateTimeField(null=True, blank=True)
    last_override_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        permissions = [
            ('finalize_report', 'Can finalise a radiology report'),
            ('override_report', 'Can amend a final radiology report'),
        ]

    @property
    def centre(self):
        return self.exam.order.visit.centre

class RadiologyReportRevision(TimeStampedModel):
    report = models.ForeignKey(RadiologyReport, on_delete=models.PROTECT, related_name='revisions')
    version = models.PositiveIntegerField()
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
