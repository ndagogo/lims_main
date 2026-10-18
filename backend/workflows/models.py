"""Versioned laboratory workflow definitions bound to orders at creation time."""
import uuid

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class WorkflowDefinition(TimeStampedModel):
    """Immutable-once-published workflow version. In-progress orders keep their bound version."""

    STATUS = [
        ('DRAFT', 'Draft'),
        ('PUBLISHED', 'Published'),
        ('RETIRED', 'Retired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS, default='DRAFT', db_index=True)
    is_active = models.BooleanField(
        default=False,
        help_text='Exactly one published version per code should be active for new orders.',
    )
    description = models.TextField(blank=True)
    expected_tat_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Overall expected turnaround from first step to release.',
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='workflows_published',
    )
    notes = models.TextField(blank=True, help_text='Approval notes from laboratory leadership.')

    class Meta:
        unique_together = [('code', 'version')]
        ordering = ['code', '-version']

    def __str__(self):
        return f'{self.name} v{self.version} ({self.status})'


class WorkflowAssignment(TimeStampedModel):
    """Map a published workflow code to a test and/or service group (most specific wins)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_code = models.CharField(max_length=40, db_index=True)
    test = models.ForeignKey(
        'laboratory.Test', null=True, blank=True, on_delete=models.CASCADE,
        related_name='workflow_assignments',
    )
    service_group = models.ForeignKey(
        'laboratory.ServiceGroup', null=True, blank=True, on_delete=models.CASCADE,
        related_name='workflow_assignments',
    )
    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text='Lower number wins when multiple assignments match.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['priority', 'workflow_code']

    def __str__(self):
        target = self.test.code if self.test_id else (self.service_group.name if self.service_group_id else '*')
        return f'{self.workflow_code} → {target}'


class WorkflowStep(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(WorkflowDefinition, on_delete=models.CASCADE, related_name='steps')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0)
    sample_status = models.CharField(max_length=40, blank=True)
    result_status = models.CharField(max_length=40, blank=True)
    required = models.BooleanField(
        default=True,
        help_text='Mandatory preceding step for later transitions when on the path.',
    )
    department = models.CharField(max_length=80, blank=True, default='')
    allowed_roles = models.JSONField(
        default=list, blank=True,
        help_text='Django group names allowed to perform transitions leaving this step. Empty = any lab role.',
    )
    required_fields = models.JSONField(
        default=list, blank=True,
        help_text='Logical field keys that must be present on the transition payload.',
    )
    required_documents = models.JSONField(
        default=list, blank=True,
        help_text='Visit document types required before leaving this step, e.g. REGISTRATION.',
    )
    expected_tat_minutes = models.PositiveIntegerField(null=True, blank=True)
    escalation_after_minutes = models.PositiveIntegerField(null=True, blank=True)
    escalation_roles = models.JSONField(default=list, blank=True)
    instructions = models.TextField(blank=True)
    skip_allowed = models.BooleanField(
        default=False,
        help_text='When true, a configured skip transition may bypass this step.',
    )

    class Meta:
        unique_together = [('workflow', 'code')]
        ordering = ['sort_order', 'code']

    def __str__(self):
        return f'{self.workflow.code}:{self.code}'


class WorkflowTransition(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(WorkflowDefinition, on_delete=models.CASCADE, related_name='transitions')
    from_step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name='outgoing')
    to_step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name='incoming')
    action = models.CharField(max_length=40)
    label = models.CharField(max_length=120, blank=True)
    condition = models.JSONField(
        default=dict, blank=True,
        help_text='Optional gates, e.g. {"require_paid": true} or {"require_document": "REGISTRATION"}.',
    )
    require_fields_complete = models.BooleanField(default=True)

    class Meta:
        unique_together = [('workflow', 'from_step', 'to_step', 'action')]
        ordering = ['from_step__sort_order', 'action']

    def __str__(self):
        return f'{self.from_step.code} -{self.action}-> {self.to_step.code}'


class OrderWorkflowInstance(TimeStampedModel):
    """Snapshot binding: lab order stays on the workflow version active at bind time."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lab_order = models.OneToOneField(
        'laboratory.LabOrder', on_delete=models.CASCADE, related_name='workflow_instance',
    )
    workflow = models.ForeignKey(WorkflowDefinition, on_delete=models.PROTECT, related_name='instances')
    current_step = models.ForeignKey(WorkflowStep, on_delete=models.PROTECT, related_name='current_instances')
    completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    bound_version = models.PositiveIntegerField(default=1)
    bound_code = models.CharField(max_length=40, blank=True, default='')

    def __str__(self):
        return f'{self.lab_order_id} @ {self.current_step.code}'


class OrderWorkflowEvent(TimeStampedModel):
    """Immutable history of enforced transitions for an order."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(OrderWorkflowInstance, on_delete=models.CASCADE, related_name='events')
    from_step = models.ForeignKey(
        WorkflowStep, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    to_step = models.ForeignKey(WorkflowStep, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    note = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['created_at']
