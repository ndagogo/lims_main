import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

class User(AbstractUser):
    """Platform user. Django Groups/Permissions provide role-based access."""
    TITLE_CHOICES = [
        ('', '—'), ('Mr', 'Mr'), ('Mrs', 'Mrs'), ('Miss', 'Miss'), ('Ms', 'Ms'),
        ('Dr', 'Dr'), ('Prof', 'Prof'),
    ]
    SEX_CHOICES = [
        ('', 'Not specified'), ('Female', 'Female'), ('Male', 'Male'), ('Other', 'Other'),
    ]
    MARITAL_CHOICES = [
        ('', 'Not specified'),
        ('SINGLE', 'Single'),
        ('MARRIED', 'Married'),
        ('DIVORCED', 'Divorced'),
        ('WIDOWED', 'Widowed'),
        ('SEPARATED', 'Separated'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=180, blank=True)
    title = models.CharField(max_length=20, blank=True, default='', choices=TITLE_CHOICES)
    other_names = models.CharField(max_length=120, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=20, blank=True, choices=SEX_CHOICES)
    marital_status = models.CharField(max_length=20, blank=True, choices=MARITAL_CHOICES)
    address = models.CharField(max_length=255, blank=True)
    must_change_password = models.BooleanField(default=False)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_password_change = models.DateTimeField(null=True, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True, default='')
    avatar = models.ImageField(
        upload_to='avatars/%Y/%m/', blank=True, null=True,
        help_text='Staff profile photograph.',
    )
    job_title = models.CharField(
        max_length=120, blank=True,
        help_text='Professional designation, e.g. Consultant Radiologist.',
    )
    staff_id = models.CharField(max_length=40, blank=True, help_text='Employee / staff number.')
    department = models.CharField(max_length=120, blank=True)
    specialty = models.CharField(max_length=120, blank=True)
    qualifications = models.CharField(
        max_length=255, blank=True,
        help_text='Credentials shown on reports, e.g. MBBS, FWACS.',
    )
    license_number = models.CharField(
        max_length=80, blank=True,
        help_text='Regulatory registration number (e.g. MDCN).',
    )
    license_authority = models.CharField(max_length=120, blank=True)
    phone_work = models.CharField(max_length=30, blank=True)
    phone_mobile = models.CharField(max_length=30, blank=True)
    office_extension = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True, help_text='Short professional profile.')
    signature = models.ImageField(
        upload_to='signatures/%Y/%m/', blank=True, null=True,
        help_text='Handwritten or scanned signature shown on authenticated reports.',
    )
    signature_title = models.CharField(
        max_length=120, blank=True,
        help_text='Legacy title under signature; prefer job_title. Kept in sync from profile.',
    )

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

    @property
    def professional_title(self):
        return (self.job_title or self.signature_title or '').strip()

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Centre(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to='centre-logos/%Y/%m/', blank=True, null=True)
    timezone = models.CharField(max_length=64, default='Africa/Lagos')
    is_active = models.BooleanField(default=True)
    class Meta:
        ordering = ['name']
        permissions = [
            ('manage_centre_users', 'Can manage centre user access'),
            ('access_platform_controls', 'Can access sensitive platform configuration controls'),
            ('set_result_water_mark', 'Can set the result watermark text'),
        ]
    def __str__(self): return f'{self.code} — {self.name}'


class CentreReportFormat(TimeStampedModel):
    """Per-centre look-and-feel for laboratory and radiology result printouts."""
    LAYOUTS = [
        ('CLASSIC', 'Classic (centred letterhead)'),
        ('COMPACT', 'Compact (dense clinical)'),
        ('FORMAL', 'Formal (ruled sections)'),
        ('MODERN', 'Modern (accent bar)'),
    ]
    FONTS = [
        ('Helvetica', 'Helvetica (sans)'),
        ('Times-Roman', 'Times (serif)'),
        ('Courier', 'Courier (mono)'),
    ]
    centre = models.OneToOneField(Centre, on_delete=models.CASCADE, related_name='report_format')
    title_lab = models.CharField(max_length=120, default='Laboratory Report')
    title_radiology = models.CharField(max_length=120, default='Radiology Report')
    header_text = models.TextField(blank=True, help_text='Optional line under the centre name.')
    footer_text = models.TextField(
        blank=True,
        default='Released copies are authorised for clinical use.',
    )
    show_logo = models.BooleanField(default=True)
    show_centre_address = models.BooleanField(default=True)
    show_centre_contact = models.BooleanField(default=True)
    primary_color = models.CharField(max_length=7, default='#102033')
    accent_color = models.CharField(max_length=7, default='#0f766e')
    layout_style = models.CharField(max_length=12, choices=LAYOUTS, default='CLASSIC')
    font_family = models.CharField(max_length=20, choices=FONTS, default='Helvetica')
    body_font_size = models.PositiveSmallIntegerField(default=10)
    margin_mm = models.PositiveSmallIntegerField(default=18)
    show_authenticator_signature = models.BooleanField(default=True)
    signature_label = models.CharField(max_length=80, default='Authenticated by')
    watermark_text = models.CharField(
        max_length=80,
        blank=True,
        default='UNVERIFIED',
        help_text='Drawn on signed (FINAL) results until approval. Approved copies use APPROVED. Requires set_result_water_mark to change.',
    )

    def __str__(self):
        return f'Report format · {self.centre.code}'

class UserCentreAccess(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='centre_access')
    centre = models.ForeignKey(Centre, on_delete=models.CASCADE, related_name='user_access')
    can_view = models.BooleanField(default=True)
    can_register = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='centre_access_grants')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','centre'], name='unique_user_centre_access')]
        ordering = ['centre__name']
    def save(self, *args, **kwargs):
        # Keep one default centre per user.
        super().save(*args, **kwargs)
        if self.is_default:
            UserCentreAccess.objects.filter(user=self.user).exclude(pk=self.pk).update(is_default=False)
    def __str__(self): return f'{self.user} @ {self.centre}'

class SequenceCounter(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.PositiveBigIntegerField(default=0)

class AuditEvent(TimeStampedModel):
    """Enterprise audit trail. Timestamps are stored in UTC (USE_TZ); UI shows Africa/Lagos."""
    CATEGORIES = [
        ('AUTH', 'Authentication'),
        ('ACCESS', 'Access control'),
        ('CLINICAL', 'Clinical'),
        ('BILLING', 'Billing / finance'),
        ('ADMIN', 'Administration'),
        ('INTEGRATION', 'Integrations'),
        ('SYSTEM', 'System / HTTP'),
        ('OTHER', 'Other'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_events')
    action = models.CharField(max_length=80, db_index=True)
    category = models.CharField(max_length=20, choices=CATEGORIES, default='OTHER', db_index=True)
    object_type = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=100, blank=True, db_index=True)
    path = models.CharField(max_length=500, blank=True)
    method = models.CharField(max_length=10, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=255, blank=True)
    location_hint = models.CharField(
        max_length=120, blank=True,
        help_text='Coarse network location from trusted proxy headers (country/region). Not GPS.',
    )
    centre = models.ForeignKey(Centre, null=True, blank=True, on_delete=models.SET_NULL)
    department = models.CharField(max_length=120, blank=True, db_index=True)
    patient_ref = models.CharField(max_length=80, blank=True, db_index=True, help_text='UHID or patient identifier')
    visit_ref = models.CharField(max_length=80, blank=True, db_index=True)
    order_ref = models.CharField(max_length=80, blank=True, db_index=True)
    result_ref = models.CharField(max_length=80, blank=True, db_index=True)
    previous_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    approval_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='audit_events_approved',
    )
    approval_note = models.TextField(blank=True)
    evidence_ref = models.CharField(max_length=255, blank=True, help_text='Reference to supporting evidence (doc id, file path, ticket).')
    evidence_note = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ['-created_at']
        permissions = [('view_sensitive_audit_details', 'Can view sensitive audit details')]
        indexes = [
            models.Index(fields=['-created_at', 'action'], name='core_audite_created_1b2e8a_idx'),
            models.Index(fields=['actor', '-created_at'], name='core_audite_actor_i_7c4d2f_idx'),
            models.Index(fields=['centre', '-created_at'], name='core_audite_centre__9a1c4e_idx'),
        ]
    def __str__(self): return f'{self.created_at} {self.action} {self.object_type} {self.object_id}'


class ResultLedger(TimeStampedModel):
    """One row per result action. Repeat dispatches each get their own row."""

    SERVICES = [('RADIOLOGY', 'Radiology'), ('LABORATORY', 'Laboratory')]
    KINDS = [
        ('DISPATCH', 'Dispatch'),
        ('MODIFY', 'Return for modification'),
        ('APPROVE', 'Approve'),
    ]
    service = models.CharField(max_length=20, choices=SERVICES, db_index=True)
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    visit = models.ForeignKey('patients.Visit', on_delete=models.PROTECT, related_name='result_ledger')
    object_type = models.CharField(max_length=40)
    object_id = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=200, blank=True)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='result_ledger_entries',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service', 'kind', '-created_at'], name='core_result_svc_kind_idx'),
        ]

    def __str__(self):
        return f'{self.kind} {self.service} {self.object_id}'
