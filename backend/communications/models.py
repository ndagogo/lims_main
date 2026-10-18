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

class NotificationTemplate(TimeStampedModel):
    key=models.SlugField(max_length=80,unique=True)
    name=models.CharField(max_length=120)
    channel=models.CharField(max_length=10,choices=NotificationOutbox.CHANNELS)
    subject_template=models.CharField(max_length=200,blank=True)
    body_template=models.TextField()
    is_active=models.BooleanField(default=True)
    def __str__(self): return self.name
