import uuid
from django.conf import settings
from django.db import models
from core.models import TimeStampedModel, Centre
from patients.models import Visit

class NotificationOutbox(TimeStampedModel):
    CHANNELS=[('SMS','SMS'),('EMAIL','Email'),('WHATSAPP','WhatsApp')]
    STATUSES=[('QUEUED','Queued'),('SENDING','Sending'),('SENT','Sent'),('FAILED','Failed'),('CANCELLED','Cancelled')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    centre=models.ForeignKey(Centre,on_delete=models.PROTECT,related_name='notifications')
    visit=models.ForeignKey(Visit,null=True,blank=True,on_delete=models.PROTECT,related_name='notifications')
    channel=models.CharField(max_length=10,choices=CHANNELS)
    recipient=models.CharField(max_length=255)
    subject=models.CharField(max_length=200,blank=True)
    body=models.TextField()
    status=models.CharField(max_length=12,choices=STATUSES,default='QUEUED',db_index=True)
    provider_message_id=models.CharField(max_length=200,blank=True)
    attempts=models.PositiveSmallIntegerField(default=0)
    last_error=models.TextField(blank=True)
    scheduled_for=models.DateTimeField(null=True,blank=True)
    sent_at=models.DateTimeField(null=True,blank=True)
    idempotency_key=models.CharField(max_length=120, blank=True, default='', db_index=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    class Meta: ordering=['-created_at']
    def __str__(self): return f'{self.channel} to {self.recipient} ({self.status})'

class AppNotification(TimeStampedModel):
    """A notice shown inside the app. One row belongs to one person."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='in_app_notices',
    )
    patient = models.ForeignKey(
        'patients.Patient', null=True, blank=True, on_delete=models.CASCADE, related_name='in_app_notices',
    )
    doctor = models.ForeignKey(
        'referrals.ReferralDoctor', null=True, blank=True, on_delete=models.CASCADE, related_name='in_app_notices',
    )
    organisation = models.ForeignKey(
        'patients.OrganisationClient', null=True, blank=True, on_delete=models.CASCADE, related_name='in_app_notices',
    )
    centre = models.ForeignKey(Centre, null=True, blank=True, on_delete=models.SET_NULL, related_name='in_app_notices')
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=400, blank=True)
    url = models.CharField(max_length=300, blank=True)
    event_key = models.CharField(max_length=80, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=160, null=True, blank=True, unique=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['patient', 'created_at']),
            models.Index(fields=['doctor', 'created_at']),
            models.Index(fields=['organisation', 'created_at']),
        ]

    def __str__(self):
        return self.title


class NotificationTemplate(TimeStampedModel):
    key=models.SlugField(max_length=80,unique=True)
    name=models.CharField(max_length=120)
    channel=models.CharField(max_length=10,choices=NotificationOutbox.CHANNELS)
    subject_template=models.CharField(max_length=200,blank=True)
    body_template=models.TextField()
    is_active=models.BooleanField(default=True)
    def __str__(self): return self.name
