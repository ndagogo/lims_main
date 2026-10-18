"""Bind and enforce versioned workflow templates on lab orders."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from workflows.models import (
    OrderWorkflowEvent, OrderWorkflowInstance, WorkflowAssignment, WorkflowDefinition,
    WorkflowStep, WorkflowTransition,
)


TEMPLATE_CODES = {
    'NORMAL': 'Normal laboratory',
    'MICROBIOLOGY': 'Microbiology',
    'CYTOLOGY': 'Cytology',
    'HISTOLOGY': 'Histology',
}

RELEASE_READY_STEPS = {'AUTHENTICATED', 'READY_TO_RELEASE', 'RELEASED', 'COMPLETED'}


def pick_template_code(test):
    """Fallback heuristic when no WorkflowAssignment matches."""
    assignment = resolve_assignment(test)
    if assignment:
        return assignment.workflow_code
    preferred = (getattr(test, 'default_workflow_code', '') or '').strip().upper()
    if preferred:
        return preferred
    dept = (getattr(test, 'department', '') or '').upper()
    name = (getattr(test, 'name', '') or '').upper()
    group = (getattr(getattr(test, 'service_group', None), 'name', '') or '').upper()
    blob = f'{dept} {name} {group}'
    if 'MICRO' in blob or 'CULTURE' in blob:
        return 'MICROBIOLOGY'
    if 'CYTO' in blob:
        return 'CYTOLOGY'
    if 'HISTO' in blob or 'BIOPSY' in blob:
        return 'HISTOLOGY'
    return 'NORMAL'


def resolve_assignment(test):
    qs = WorkflowAssignment.objects.filter(is_active=True).filter(
        Q(test_id=test.pk) | Q(test__isnull=True, service_group_id=getattr(test, 'service_group_id', None))
    ).order_by('priority', 'workflow_code')
    # Prefer exact test match over service-group match.
    exact = qs.filter(test_id=test.pk).first()
    if exact:
        return exact
    return qs.filter(test__isnull=True).first()


def active_workflow(code):
    return (
        WorkflowDefinition.objects.filter(code=code, status='PUBLISHED', is_active=True)
        .order_by('-version')
        .first()
    )


def bind_workflow(lab_order):
    """Attach the active published workflow version. Never rewrite an existing binding."""
    existing = OrderWorkflowInstance.objects.filter(lab_order=lab_order).select_related(
        'current_step', 'workflow',
    ).first()
    if existing:
        return existing
    code = pick_template_code(lab_order.test)
    workflow = active_workflow(code) or active_workflow('NORMAL')
    if not workflow:
        return None
    first = workflow.steps.order_by('sort_order').first()
    if not first:
        return None
    return OrderWorkflowInstance.objects.create(
        lab_order=lab_order,
        workflow=workflow,
        current_step=first,
        bound_version=workflow.version,
        bound_code=workflow.code,
    )


def _user_role_names(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    if user.is_superuser:
        return {'*'}
    return set(user.groups.values_list('name', flat=True))


def _invoice_paid(visit):
    invoices = list(visit.invoices.exclude(status='CANCELLED'))
    if not invoices:
        return False
    return all(inv.status == 'PAID' for inv in invoices)


def _has_document(visit, doc_type):
    return visit.documents.filter(document_type=doc_type).exists()


def _evaluate_condition(condition, lab_order, payload):
    if not condition:
        return True
    visit = lab_order.visit
    if condition.get('require_paid') and not _invoice_paid(visit):
        raise ValidationError('Payment confirmation or approved credit is required before this step.')
    doc = condition.get('require_document')
    if doc and not _has_document(visit, doc):
        raise ValidationError(f'Document type {doc} is required before this transition.')
    # Payload-supplied flags for optional skips configured by leadership.
    for key, expected in condition.items():
        if key.startswith('require_') and key not in ('require_paid', 'require_document'):
            field = key[len('require_'):]
            if not payload.get(field):
                raise ValidationError(f'Field "{field}" is required for this transition.')
        if key.startswith('flag_') and payload.get(key) != expected:
            raise ValidationError(f'Transition condition {key} was not satisfied.')
    return True


def assert_transition(lab_order, action, *, user=None, payload=None, note='', enforce=True):
    """
    Server-side state machine. Raises ValidationError / PermissionDenied when blocked.
    When enforce=False and no instance exists, no-op (legacy orders without binding).
    """
    payload = payload or {}
    instance = getattr(lab_order, 'workflow_instance', None)
    if instance is None:
        instance = OrderWorkflowInstance.objects.filter(lab_order=lab_order).select_related(
            'current_step', 'workflow',
        ).first()
    if instance is None:
        if enforce:
            # Bind lazily so older orders still enter the engine once templates exist.
            instance = bind_workflow(lab_order)
        if instance is None:
            return None
    if instance.completed:
        raise ValidationError('This order workflow is already completed.')

    transition = WorkflowTransition.objects.filter(
        workflow=instance.workflow,
        from_step=instance.current_step,
        action=action,
    ).select_related('to_step', 'from_step').first()
    if not transition:
        raise ValidationError(
            f'Workflow {instance.workflow.code} v{instance.workflow.version} does not allow '
            f'"{action}" from step "{instance.current_step.name}" ({instance.current_step.code}).'
        )

    step = instance.current_step
    roles = set(step.allowed_roles or [])
    if roles and user is not None:
        names = _user_role_names(user)
        if '*' not in names and not names.intersection(roles):
            raise PermissionDenied(
                f'Step "{step.name}" requires one of these roles: {", ".join(sorted(roles))}.'
            )

    if transition.require_fields_complete:
        missing = [f for f in (step.required_fields or []) if not payload.get(f)]
        if missing:
            raise ValidationError(f'Required fields missing for this step: {", ".join(missing)}.')

    for doc in (step.required_documents or []):
        if not _has_document(lab_order.visit, doc):
            raise ValidationError(f'Document "{doc}" is required before leaving step {step.name}.')

    _evaluate_condition(transition.condition or {}, lab_order, payload)

    # Mandatory preceding steps: cannot jump to authenticate/release without completing required path.
    # Enforced by only allowing configured transitions — no free jumps.

    with transaction.atomic():
        from_step = instance.current_step
        instance.current_step = transition.to_step
        if transition.to_step.code in RELEASE_READY_STEPS | {'RELEASED'}:
            if transition.to_step.code in ('RELEASED', 'COMPLETED'):
                instance.completed = True
                instance.completed_at = timezone.now()
        instance.save(update_fields=['current_step', 'completed', 'completed_at', 'updated_at'])
        OrderWorkflowEvent.objects.create(
            instance=instance,
            from_step=from_step,
            to_step=transition.to_step,
            action=action,
            actor=user if getattr(user, 'is_authenticated', False) else None,
            note=note or '',
            detail={'payload_keys': sorted(payload.keys()), 'workflow_version': instance.bound_version},
        )
    return instance


def workflow_blocks_release(visit):
    open_instances = OrderWorkflowInstance.objects.filter(
        lab_order__visit=visit, completed=False,
    ).select_related('current_step', 'lab_order')
    for row in open_instances:
        if row.current_step.code not in RELEASE_READY_STEPS:
            return (
                f'Order {row.lab_order.test.code if row.lab_order_id else row.lab_order_id} '
                f'is still at workflow step {row.current_step.name}.'
            )
    return None


def publish_workflow(workflow, user=None, notes=''):
    """Publish a draft version and retire other active versions of the same code."""
    if workflow.status == 'PUBLISHED' and workflow.is_active:
        return workflow
    if not workflow.steps.exists():
        raise ValidationError('Add at least one step before publishing.')
    if not workflow.transitions.exists():
        raise ValidationError('Add at least one transition before publishing.')
    with transaction.atomic():
        WorkflowDefinition.objects.filter(code=workflow.code, is_active=True).exclude(pk=workflow.pk).update(
            is_active=False, status='RETIRED',
        )
        workflow.status = 'PUBLISHED'
        workflow.is_active = True
        workflow.published_at = timezone.now()
        workflow.published_by = user if getattr(user, 'is_authenticated', False) else None
        if notes:
            workflow.notes = notes
        workflow.save()
    return workflow


def clone_workflow_version(source, user=None):
    """Create a new DRAFT version copied from a published/retired definition (for safe edits)."""
    next_version = (
        WorkflowDefinition.objects.filter(code=source.code).order_by('-version').first().version + 1
    )
    with transaction.atomic():
        clone = WorkflowDefinition.objects.create(
            code=source.code,
            name=source.name,
            version=next_version,
            status='DRAFT',
            is_active=False,
            description=source.description,
            expected_tat_minutes=source.expected_tat_minutes,
            notes=f'Cloned from v{source.version} for editing.',
        )
        step_map = {}
        for step in source.steps.order_by('sort_order'):
            step_map[step.pk] = WorkflowStep.objects.create(
                workflow=clone,
                code=step.code,
                name=step.name,
                sort_order=step.sort_order,
                sample_status=step.sample_status,
                result_status=step.result_status,
                required=step.required,
                department=step.department,
                allowed_roles=list(step.allowed_roles or []),
                required_fields=list(step.required_fields or []),
                required_documents=list(step.required_documents or []),
                expected_tat_minutes=step.expected_tat_minutes,
                escalation_after_minutes=step.escalation_after_minutes,
                escalation_roles=list(step.escalation_roles or []),
                instructions=step.instructions,
                skip_allowed=step.skip_allowed,
            )
        for tr in source.transitions.select_related('from_step', 'to_step'):
            WorkflowTransition.objects.create(
                workflow=clone,
                from_step=step_map[tr.from_step_id],
                to_step=step_map[tr.to_step_id],
                action=tr.action,
                label=tr.label,
                condition=dict(tr.condition or {}),
                require_fields_complete=tr.require_fields_complete,
            )
    return clone


def preview_workflow(workflow):
    """Ordered steps + transition graph for the builder preview panel."""
    steps = list(workflow.steps.order_by('sort_order'))
    transitions = [
        {
            'from': tr.from_step.code,
            'to': tr.to_step.code,
            'action': tr.action,
            'label': tr.label or tr.action,
            'condition': tr.condition or {},
        }
        for tr in workflow.transitions.select_related('from_step', 'to_step')
    ]
    return {
        'code': workflow.code,
        'name': workflow.name,
        'version': workflow.version,
        'status': workflow.status,
        'expected_tat_minutes': workflow.expected_tat_minutes,
        'steps': [
            {
                'code': s.code,
                'name': s.name,
                'sort_order': s.sort_order,
                'department': s.department,
                'allowed_roles': s.allowed_roles,
                'required': s.required,
                'required_fields': s.required_fields,
                'required_documents': s.required_documents,
                'expected_tat_minutes': s.expected_tat_minutes,
                'escalation_after_minutes': s.escalation_after_minutes,
                'skip_allowed': s.skip_allowed,
            }
            for s in steps
        ],
        'transitions': transitions,
    }
