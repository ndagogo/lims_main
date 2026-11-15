import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


def _token():
    return secrets.token_urlsafe(32)


class PortalAccessToken(TimeStampedModel):
    """Short-lived portal session token (PDF §11 — no broad phone-only access)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portal = models.CharField(max_length=20, choices=[('PATIENT','Patient'),('PHYSICIAN','Physician'),('CORPORATE','Corporate')])
    token = models.CharField(max_length=64, unique=True, default=_token)
    patient = models.ForeignKey('patients.Patient', null=True, blank=True, on_delete=models.CASCADE, related_name='portal_tokens')
    doctor = models.ForeignKey('referrals.ReferralDoctor', null=True, blank=True, on_delete=models.CASCADE, related_name='portal_tokens')
    organisation = models.ForeignKey('patients.OrganisationClient', null=True, blank=True, on_delete=models.CASCADE, related_name='portal_tokens')
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    @classmethod
    def issue(cls, portal, *, hours=2, **kwargs):
        return cls.objects.create(
            portal=portal,
            expires_at=timezone.now() + timedelta(hours=hours),
            **kwargs,
        )

    def is_valid(self):
        return self.is_active and self.expires_at > timezone.now()


class SupportRequestType(TimeStampedModel):
    """Request types the centre defines. Each type can route to a support owner."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=240, blank=True)
    centre = models.ForeignKey(
        'core.Centre', null=True, blank=True, on_delete=models.CASCADE, related_name='support_request_types',
        help_text='Leave empty to offer this type at every centre.',
    )
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='support_types_owned',
        help_text='New requests of this type are routed to this person.',
    )
    response_hours = models.PositiveIntegerField(default=24)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='support_types_created',
    )

    class Meta:
        ordering = ['sort_order', 'name']
        permissions = [
            ('patient_support', 'Can handle patient portal support requests'),
            ('manage_support_types', 'Can create patient support request types'),
        ]

    def __str__(self):
        return self.name


class PortalRequest(TimeStampedModel):
    """Patient support request. Does not change the clinical record."""
    STATUSES = [
        ('OPEN', 'Open'),
        ('ASSIGNED', 'Assigned'),
        ('IN_PROGRESS', 'In progress'),
        ('WAITING', 'Waiting on patient'),
        ('RESOLVED', 'Resolved'),
        ('CLOSED', 'Closed'),
    ]
    OPEN_STATUSES = ('OPEN', 'ASSIGNED', 'IN_PROGRESS', 'WAITING')
    PRIORITIES = [('NORMAL', 'Normal'), ('URGENT', 'Urgent')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True, null=True, blank=True)
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='portal_requests')
    visit = models.ForeignKey('patients.Visit', null=True, blank=True, on_delete=models.SET_NULL, related_name='portal_requests')
    centre = models.ForeignKey('core.Centre', null=True, blank=True, on_delete=models.PROTECT, related_name='support_requests')
    request_type = models.ForeignKey(
        SupportRequestType, null=True, blank=True, on_delete=models.PROTECT, related_name='requests',
    )
    kind = models.CharField(max_length=40, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUSES, default='OPEN', db_index=True)
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='NORMAL')
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='support_requests',
    )
    due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.reference or str(self.pk)

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class SupportEvent(TimeStampedModel):
    """Timeline row for one support request."""
    KINDS = [
        ('OPENED', 'Opened'),
        ('ASSIGNED', 'Assigned'),
        ('STATUS', 'Status change'),
        ('NOTE', 'Note'),
        ('REPLY', 'Reply to patient'),
        ('PATIENT_REPLY', 'Patient reply'),
        ('RESOLVED', 'Resolved'),
        ('CLOSED', 'Closed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PortalRequest, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20, choices=KINDS)
    note = models.TextField(blank=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    visible_to_patient = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']


class PhysicianInvite(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey('referrals.ReferralDoctor', on_delete=models.CASCADE, related_name='invites')
    invite_token = models.CharField(max_length=64, unique=True, default=_token)
    email = models.EmailField()
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    @classmethod
    def create_for(cls, doctor, email, user, *, days=7):
        return cls.objects.create(
            doctor=doctor, email=email, invited_by=user,
            expires_at=timezone.now() + timedelta(days=days),
        )
