import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

class User(AbstractUser):
    """Platform user. Django Groups/Permissions provide role-based access."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=180, blank=True)
    must_change_password = models.BooleanField(default=False)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_password_change = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

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
        permissions = [('manage_centre_users', 'Can manage centre user access')]
    def __str__(self): return f'{self.code} — {self.name}'

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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_events')
    action = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=100, blank=True)
    path = models.CharField(max_length=500, blank=True)
    method = models.CharField(max_length=10, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=255, blank=True)
    centre = models.ForeignKey(Centre, null=True, blank=True, on_delete=models.SET_NULL)
    detail = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ['-created_at']
        permissions = [('view_sensitive_audit_details', 'Can view sensitive audit details')]
    def __str__(self): return f'{self.created_at} {self.action} {self.object_type} {self.object_id}'
