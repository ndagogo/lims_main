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


class PortalRequest(TimeStampedModel):
    """Patient support or result-correction request. Does not change the clinical record."""
    KINDS = [('SUPPORT', 'Support'), ('CORRECTION', 'Correction')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='portal_requests')
    visit = models.ForeignKey('patients.Visit', null=True, blank=True, on_delete=models.SET_NULL, related_name='portal_requests')
    kind = models.CharField(max_length=12, choices=KINDS)
    message = models.TextField()
    status = models.CharField(max_length=12, default='OPEN')

    class Meta:
        ordering = ['-created_at']


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
