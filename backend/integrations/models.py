import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel, Centre
from laboratory.models import Test, TestParameter, Sample

class Analyzer(TimeStampedModel):
    PROTOCOLS = [('HL7','HL7 v2'),('ASTM','ASTM'),('TCP','Vendor TCP'),('SERIAL','Serial/COM'),('FILE','File drop'),('MANUAL','Manual import')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='analyzers')
    name = models.CharField(max_length=120)
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    protocol = models.CharField(max_length=10, choices=PROTOCOLS)
    host = models.CharField(max_length=255, blank=True, help_text='Use private LAN address only; do not expose analyzer ports publicly.')
    port = models.PositiveIntegerField(null=True, blank=True)
    serial_port = models.CharField(max_length=80, blank=True)
    instrument_identifier = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True, help_text='Non-secret adapter settings; never store credentials here.')
    last_seen_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ['centre__code','name']; constraints = [models.UniqueConstraint(fields=['centre','name'], name='uniq_analyzer_centre_name')]
    def __str__(self): return f'{self.centre.code} / {self.name}'

class AnalyzerTestMapping(TimeStampedModel):
    analyzer = models.ForeignKey(Analyzer, on_delete=models.CASCADE, related_name='mappings')
    instrument_test_code = models.CharField(max_length=100)
    test = models.ForeignKey(Test, on_delete=models.PROTECT)
    parameter = models.ForeignKey(TestParameter, null=True, blank=True, on_delete=models.PROTECT)
    unit_override = models.CharField(max_length=40, blank=True)
    enabled = models.BooleanField(default=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['analyzer','instrument_test_code'], name='uniq_analyzer_test_code')]
    def clean(self):
        if self.parameter_id and self.test_id and self.parameter.test_id != self.test_id:
            raise ValidationError({'parameter': 'Parameter must belong to the mapped test.'})
    def __str__(self): return f'{self.analyzer.name}: {self.instrument_test_code}'

class AnalyzerMessage(TimeStampedModel):
    STATUS = [('RECEIVED','Received'),('PARSED','Parsed'),('MAPPED','Mapped'),('PENDING_REVIEW','Pending review'),('ACCEPTED','Accepted'),('REJECTED','Rejected'),('ERROR','Error')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analyzer = models.ForeignKey(Analyzer, on_delete=models.PROTECT, related_name='messages')
    sample = models.ForeignKey(Sample, null=True, blank=True, on_delete=models.PROTECT, related_name='analyzer_messages')
    external_message_id = models.CharField(max_length=150, blank=True, db_index=True)
    raw_payload = models.TextField(help_text='Restricted-access instrument payload; apply retention controls.')
    parsed_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='RECEIVED', db_index=True)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    gateway = models.ForeignKey('GatewayRegistration', null=True, blank=True, on_delete=models.SET_NULL, related_name='messages')
    class Meta: ordering = ['-received_at']
    def __str__(self): return f'{self.analyzer.name} / {self.status} / {self.received_at:%Y-%m-%d %H:%M}'


class GatewayRegistration(TimeStampedModel):
    """Registered local analyser gateway instance (API key auth on central ingest)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name='gateways')
    name = models.CharField(max_length=120)
    api_key = models.CharField(max_length=64, unique=True)
    hostname = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    device_profiles = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['centre__code', 'name']

    def __str__(self):
        return f'{self.centre.code} / {self.name}'
