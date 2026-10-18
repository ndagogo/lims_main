"""Native platform-control screens — configuration without Django admin."""
import secrets
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date

from billing.models import BillAmendment, Refund
from communications.messaging import ALLOWED_TEMPLATE_VARIABLES, validate_template_text
from communications.models import NotificationOutbox, NotificationTemplate
from core.access import can_access_platform_controls
from core.models import AuditEvent, Centre
from core.paging import PAGE_SIZES, paginate
from core.platform_controls import platform_control_sections
from integrations.models import Analyzer, AnalyzerMessage, AnalyzerTestMapping, GatewayRegistration
from inventory.models import ReagentSku
from django.contrib.auth.models import Group

from laboratory.models import (
    QcMaterial, QcRun, ResultTemplate, ResultTemplateField, SampleCodeConfig, ServiceGroup,
    Test, TestParameter, TatPauseReason, TatRule,
)
from laboratory.templates_svc import clone_template_version, publish_template
from portals.models import PhysicianInvite
from radiology.models import PacsFailure
from referrals.models import ReferralDoctor
from workflows.models import WorkflowAssignment, WorkflowDefinition, WorkflowStep, WorkflowTransition
from workflows.services import clone_workflow_version, preview_workflow, publish_workflow


def require_platform_controls(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not can_access_platform_controls(request.user):
        raise PermissionDenied('Platform controls are restricted to authorised platform administrators.')


def _audit(request, action, obj, detail=None, centre=None, **kwargs):
    from core.audit import record_audit
    record_audit(
        request=request,
        actor=request.user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        centre=centre,
        detail=detail or {},
        previous_values=kwargs.get('previous_values'),
        new_values=kwargs.get('new_values'),
        reason=kwargs.get('reason', ''),
    )


def _text(post, name, default=''):
    return (post.get(name) or default).strip()


def _checked(post, name):
    return post.get(name) in ('on', 'true', '1', 'yes')


def _int(post, name, default=0):
    raw = (post.get(name) or str(default)).strip()
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f'{name.replace("_", " ").title()} must be a whole number.') from exc


def build_hub_context(request):
    return {
        'sections': platform_control_sections(),
        'sensitive': True,
    }


# --- Audit -----------------------------------------------------------------

def build_audit_context(request):
    from core.audit import (
        audit_dashboard_stats, centres_for_filter, filter_audit_events, format_lagos, format_utc,
    )
    from core.models import AuditEvent

    rows = filter_audit_events(request.GET)
    stats = audit_dashboard_stats(rows)
    selected = None
    detail_id = _text(request.GET, 'event')
    if detail_id:
        selected = rows.filter(pk=detail_id).first() or AuditEvent.objects.select_related(
            'actor', 'centre', 'approval_by',
        ).filter(pk=detail_id).first()
    page = paginate(request, rows, 'page')
    for row in page.object_list:
        row.when_lagos = format_lagos(row.created_at)
        row.when_utc = format_utc(row.created_at)
    if selected:
        selected.when_lagos = format_lagos(selected.created_at)
        selected.when_utc = format_utc(selected.created_at)
    return {
        'directory': page,
        'query': _text(request.GET, 'q'),
        'filters': {
            'username': _text(request.GET, 'username'),
            'action': _text(request.GET, 'action'),
            'object_type': _text(request.GET, 'object_type'),
            'category': _text(request.GET, 'category'),
            'centre': _text(request.GET, 'centre'),
            'department': _text(request.GET, 'department'),
            'patient': _text(request.GET, 'patient'),
            'visit': _text(request.GET, 'visit'),
            'order': _text(request.GET, 'order'),
            'result': _text(request.GET, 'result'),
            'ip': _text(request.GET, 'ip'),
            'date_from': _text(request.GET, 'date_from'),
            'date_to': _text(request.GET, 'date_to'),
            'exclude_http': request.GET.get('exclude_http', '1'),
            'significant': request.GET.get('significant', ''),
        },
        'categories': AuditEvent.CATEGORIES,
        'centres': centres_for_filter(),
        'stats': stats,
        'selected': selected,
        'page_sizes': PAGE_SIZES,
        'export_query': request.GET.urlencode(),
    }


def export_audit_report(request):
    """Download filtered audit trail as csv, xlsx, or pdf."""
    from core.audit import export_audit_csv, export_audit_excel, export_audit_pdf, filter_audit_events

    fmt = (_text(request.GET, 'format') or 'csv').lower()
    rows = filter_audit_events(request.GET)
    if fmt in ('xlsx', 'excel'):
        return export_audit_excel(rows)
    if fmt == 'pdf':
        return export_audit_pdf(rows)
    return export_audit_csv(rows)


# --- Workflows -------------------------------------------------------------

WORKFLOW_ROLE_CHOICES = [
    'Front Desk', 'Cashier', 'Accounts', 'Phlebotomist', 'Laboratory Scientist',
    'Lab Manager', 'Radiographer', 'Radiologist', 'Platform Administrator', 'Centre Administrator',
]
WORKFLOW_FIELD_CHOICES = [
    'collector', 'narrative_or_values', 'observation', 'accession', 'clinical_history',
    'batch_id', 'instrument_id', 'gross_description',
]
WORKFLOW_DOC_CHOICES = ['REGISTRATION', 'REFERRAL', 'OTHER']
WORKFLOW_DEPARTMENTS = [
    'Registration', 'Accounts', 'Phlebotomy', 'Pathology', 'Microbiology',
    'Cytology', 'Histology', 'Haematology', 'Chemistry', 'Radiology',
]


def _wf_url(pk=None, *, preview=False, q=''):
    base = '/platform/workflows/'
    params = []
    if pk:
        params.append(f'workflow={pk}')
    if preview:
        params.append('preview=1')
    if q:
        params.append(f'q={q}')
    return base + (('?' + '&'.join(params)) if params else '')


def _optional_int(post, name):
    raw = _text(post, name)
    if not raw:
        return None
    return _int(post, name)


def _csv_list(post, name):
    raw = _text(post, name)
    if not raw:
        # Multi-select posts as getlist
        values = [v.strip() for v in post.getlist(name) if v and v.strip()]
        return values
    return [part.strip() for part in raw.replace(';', ',').split(',') if part.strip()]


def _multi_list(post, name):
    return [v.strip() for v in post.getlist(name) if v and v.strip()]


def _require_draft(workflow):
    if workflow.status != 'DRAFT':
        raise ValidationError(
            'Published or retired workflows are locked. Create a new version to edit steps and transitions.'
        )


def _step_payload(post):
    return {
        'name': _text(post, 'step_name') or _text(post, 'name'),
        'sample_status': _text(post, 'sample_status'),
        'result_status': _text(post, 'result_status'),
        'department': _text(post, 'department'),
        'allowed_roles': _multi_list(post, 'allowed_roles'),
        'required_fields': _multi_list(post, 'required_fields') or _csv_list(post, 'required_fields_csv'),
        'required_documents': _multi_list(post, 'required_documents'),
        'expected_tat_minutes': _optional_int(post, 'expected_tat_minutes'),
        'escalation_after_minutes': _optional_int(post, 'escalation_after_minutes'),
        'escalation_roles': _multi_list(post, 'escalation_roles'),
        'instructions': _text(post, 'instructions'),
        'required': _checked(post, 'required') if post.get('required_submitted') else True,
        'skip_allowed': _checked(post, 'skip_allowed'),
    }


def _transition_condition(post):
    condition = {}
    if _checked(post, 'require_paid'):
        condition['require_paid'] = True
    doc = _text(post, 'require_document')
    if doc:
        condition['require_document'] = doc
    extra = _text(post, 'condition_json')
    if extra:
        import json
        try:
            parsed = json.loads(extra)
            if isinstance(parsed, dict):
                condition.update(parsed)
        except json.JSONDecodeError as exc:
            raise ValidationError('Condition JSON is invalid.') from exc
    return condition


STEP_CANVAS_LABELS = {
    'ORDERED': 'Registration',
    'PAYMENT_CLEARED': 'Payment',
    'COLLECTED': 'Collection',
    'BATCHED': 'Batching',
    'RECEIVED': 'Dept receiving',
    'PROCESSING': 'Sample processing',
    'RESULT_ENTRY': 'Result entry',
    'REVIEW': 'First review',
    'UNDER_REVIEW': 'Under review',
    'AUTHENTICATED': 'Authenticate',
    'RELEASED': 'Release',
    'CULTURE': 'Culture',
    'INCUBATION': 'Incubation',
    'CYTO_PENDING': 'Cyto pending',
    'CYTO_PROCESSING': 'Cyto processing',
    'ACCESSIONING': 'Accessioning',
    'GROSSING': 'Grossing',
    'EMBEDDING': 'Embedding',
    'MICROSCOPY': 'Microscopy',
}
# code, canvas label, sample status written when the chip is dropped on a draft
STEP_CATALOGUE = [
    ('ORDERED', 'Registration', 'ORDERED'),
    ('PAYMENT_CLEARED', 'Payment', ''),
    ('COLLECTED', 'Collection', 'COLLECTED'),
    ('BATCHED', 'Batching', 'BATCHED'),
    ('RECEIVED', 'Dept receiving', 'RECEIVED'),
    ('PROCESSING', 'Sample processing', 'PROCESSING'),
    ('RESULT_ENTRY', 'Result entry', 'RESULT_ENTERED'),
    ('REVIEW', 'First review', 'UNDER_REVIEW'),
    ('UNDER_REVIEW', 'Under review', 'UNDER_REVIEW'),
    ('AUTHENTICATED', 'Authenticate', 'AUTHENTICATED'),
    ('RELEASED', 'Release', 'RELEASED'),
    ('CULTURE', 'Culture', 'PROCESSING'),
    ('INCUBATION', 'Incubation', 'PROCESSING'),
    ('CYTO_PENDING', 'Cyto pending', 'PROCESSING'),
    ('CYTO_PROCESSING', 'Cyto processing', 'PROCESSING'),
    ('ACCESSIONING', 'Accessioning', 'RECEIVED'),
    ('GROSSING', 'Grossing', 'PROCESSING'),
    ('EMBEDDING', 'Embedding', 'PROCESSING'),
    ('MICROSCOPY', 'Microscopy', 'PROCESSING'),
]
WORKFLOW_ACTION_CHOICES = [
    'collect', 'batch', 'receive', 'start', 'enter_result', 'submit',
    'authenticate', 'release', 'confirm_payment',
]
LANE_LABELS = {
    'NORMAL': 'Normal',
    'MICROBIOLOGY': 'Micro',
    'CYTOLOGY': 'Cyto',
    'HISTOLOGY': 'Histo',
    'RADIOLOGY': 'Radiology',
}


class PaletteStep:
    def __init__(self, code, canvas_label, sample_status=''):
        self.code = code
        self.canvas_label = canvas_label
        self.sample_status = sample_status


def _label_steps(workflow):
    for step in workflow.steps.all():
        step.canvas_label = STEP_CANVAS_LABELS.get(step.code, step.name)


def _step_palette(canvas_workflows):
    """Catalogue steps always appear, including Batching when no live workflow has it."""
    by_code = {
        code: PaletteStep(code, label, status)
        for code, label, status in STEP_CATALOGUE
    }
    for workflow in canvas_workflows:
        for step in workflow.steps.all():
            if step.code in by_code:
                continue
            by_code[step.code] = PaletteStep(
                step.code,
                STEP_CANVAS_LABELS.get(step.code, step.name),
                step.sample_status or '',
            )
    ordered = [by_code[code] for code, _, _ in STEP_CATALOGUE]
    ordered.extend(step for code, step in by_code.items() if code not in {c for c, _, _ in STEP_CATALOGUE})
    return ordered


def _canvas_workflows():
    chosen = []
    codes = WorkflowDefinition.objects.order_by('code').values_list('code', flat=True).distinct()
    for code in codes:
        row = (
            WorkflowDefinition.objects.filter(code=code, is_active=True).order_by('-version').first()
            or WorkflowDefinition.objects.filter(code=code).order_by('-version').first()
        )
        if row:
            chosen.append(row.pk)
    return list(
        WorkflowDefinition.objects.filter(pk__in=chosen).prefetch_related('steps').order_by('name')
    )


def build_workflows_context(request):
    query = _text(request.GET, 'q')
    rows = WorkflowDefinition.objects.prefetch_related('steps').order_by('code', '-version')
    if query:
        rows = rows.filter(Q(code__icontains=query) | Q(name__icontains=query))
    canvas_workflows = _canvas_workflows()
    for workflow in canvas_workflows:
        workflow.lane_label = LANE_LABELS.get(workflow.code, (workflow.name or workflow.code).split()[0])
        _label_steps(workflow)
    lane = (_text(request.GET, 'lane') or 'ALL').upper()
    known_codes = {row.code for row in canvas_workflows}
    if lane not in known_codes and lane not in ('ALL', 'NEW'):
        lane = 'ALL'
    visible_canvas = canvas_workflows if lane in ('ALL', 'NEW') else [row for row in canvas_workflows if row.code == lane]
    palette = _step_palette(canvas_workflows)
    selected = None
    preview = None
    assignments = []
    selected_step = None
    wid = request.GET.get('workflow')
    if wid:
        selected = WorkflowDefinition.objects.prefetch_related(
            'steps', 'transitions__from_step', 'transitions__to_step',
        ).filter(pk=wid).first()
        if selected:
            selected.lane_label = LANE_LABELS.get(selected.code, (selected.name or selected.code).split()[0])
            _label_steps(selected)
            preview = preview_workflow(selected)
            assignments = list(
                WorkflowAssignment.objects.filter(workflow_code=selected.code)
                .select_related('test', 'service_group')
                .order_by('priority', 'workflow_code')
            )
            sid = request.GET.get('step')
            if sid:
                selected_step = selected.steps.filter(pk=sid).first()
            visible_canvas = [
                selected if row.code == selected.code else row for row in visible_canvas
            ]
            if selected.code not in {row.code for row in visible_canvas}:
                visible_canvas = [selected, *visible_canvas]
    role_names = list(Group.objects.order_by('name').values_list('name', flat=True))
    if not role_names:
        role_names = WORKFLOW_ROLE_CHOICES
    for name in WORKFLOW_ROLE_CHOICES:
        if name not in role_names:
            role_names.append(name)
    return {
        'directory': paginate(request, rows, 'page'),
        'selected': selected,
        'selected_step': selected_step,
        'preview': preview,
        'show_preview': request.GET.get('preview') == '1' or bool(selected),
        'assignments': assignments,
        'query': query,
        'page_sizes': PAGE_SIZES,
        'role_choices': role_names,
        'field_choices': WORKFLOW_FIELD_CHOICES,
        'doc_choices': WORKFLOW_DOC_CHOICES,
        'department_choices': WORKFLOW_DEPARTMENTS,
        'tests': Test.objects.filter(is_active=True).order_by('code'),
        'service_groups': ServiceGroup.objects.filter(is_active=True).order_by('name'),
        'workflow_codes': list(
            WorkflowDefinition.objects.order_by('code').values_list('code', flat=True).distinct()
        ),
        'editable': bool(selected and selected.status == 'DRAFT'),
        'canvas_workflows': canvas_workflows,
        'visible_canvas': visible_canvas,
        'step_palette': palette,
        'action_choices': WORKFLOW_ACTION_CHOICES,
        'lane': lane,
        'step_labels': STEP_CANVAS_LABELS,
        'lane_labels': LANE_LABELS,
    }


def handle_workflows_post(request):
    action = request.POST.get('action')
    q = _text(request.POST, 'q') or _text(request.GET, 'q')

    try:
        if action == 'create':
            code = _text(request.POST, 'code').upper().replace(' ', '_')
            name = _text(request.POST, 'name')
            if not code or not name:
                messages.error(request, 'Code and name are required.')
                return redirect('platform-workflows')
            latest = WorkflowDefinition.objects.filter(code=code).order_by('-version').first()
            version = (latest.version + 1) if latest else 1
            tat = _optional_int(request.POST, 'expected_tat_minutes')
            row = WorkflowDefinition.objects.create(
                code=code, name=name, version=version, status='DRAFT', is_active=False,
                description=_text(request.POST, 'description'),
                expected_tat_minutes=tat,
            )
            _audit(request, 'WORKFLOW_CREATED', row, {'code': row.code, 'version': row.version})
            messages.success(request, f'Draft workflow {row.code} v{row.version} created.')
            return redirect(_wf_url(row.pk, q=q))

        if action == 'update':
            row = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            _require_draft(row)
            row.name = _text(request.POST, 'name') or row.name
            row.description = _text(request.POST, 'description')
            row.expected_tat_minutes = _optional_int(request.POST, 'expected_tat_minutes')
            row.notes = _text(request.POST, 'notes')
            row.save()
            _audit(request, 'WORKFLOW_UPDATED', row, {'code': row.code})
            messages.success(request, f'Workflow {row.code} v{row.version} saved.')
            return redirect(_wf_url(row.pk, q=q))

        if action == 'add_step':
            workflow = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            _require_draft(workflow)
            code = _text(request.POST, 'step_code').upper().replace(' ', '_')
            payload = _step_payload(request.POST)
            if not code or not payload['name']:
                messages.error(request, 'Step code and name are required.')
                return redirect(_wf_url(workflow.pk, q=q))
            max_order = workflow.steps.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0
            sort_order = _int(request.POST, 'sort_order', max_order + 10)
            try:
                step = WorkflowStep.objects.create(workflow=workflow, code=code, sort_order=sort_order, **payload)
            except IntegrityError:
                messages.error(request, 'That step code already exists on this workflow.')
                return redirect(_wf_url(workflow.pk, q=q))
            _audit(request, 'WORKFLOW_STEP_ADDED', step, {'workflow': workflow.code, 'step': code})
            messages.success(request, f'Step {code} added.')
            return redirect(_wf_url(workflow.pk, q=q) + f'&step={step.pk}')

        if action == 'update_step':
            step = get_object_or_404(WorkflowStep.objects.select_related('workflow'), pk=request.POST.get('step'))
            _require_draft(step.workflow)
            payload = _step_payload(request.POST)
            if not payload['name']:
                messages.error(request, 'Step name is required.')
                return redirect(_wf_url(step.workflow_id, q=q) + f'&step={step.pk}')
            for key, value in payload.items():
                setattr(step, key, value)
            step.save()
            _audit(request, 'WORKFLOW_STEP_UPDATED', step, {'step': step.code})
            messages.success(request, f'Step {step.code} updated.')
            return redirect(_wf_url(step.workflow_id, q=q) + f'&step={step.pk}')

        if action == 'delete_step':
            step = get_object_or_404(WorkflowStep.objects.select_related('workflow'), pk=request.POST.get('step'))
            _require_draft(step.workflow)
            workflow_id = step.workflow_id
            label = step.code
            step.delete()
            messages.success(request, f'Step {label} removed.')
            return redirect(_wf_url(workflow_id, q=q))

        if action == 'reorder_steps':
            workflow = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            _require_draft(workflow)
            order = request.POST.getlist('step_order')
            if not order:
                messages.error(request, 'No step order received.')
                return redirect(_wf_url(workflow.pk, q=q))
            steps = {str(s.pk): s for s in workflow.steps.all()}
            for index, step_id in enumerate(order):
                step = steps.get(str(step_id))
                if step:
                    step.sort_order = (index + 1) * 10
                    step.save(update_fields=['sort_order', 'updated_at'])
            _audit(request, 'WORKFLOW_STEPS_REORDERED', workflow, {'count': len(order)})
            messages.success(request, 'Step order saved.')
            return redirect(_wf_url(workflow.pk, q=q))

        if action == 'add_transition':
            workflow = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            _require_draft(workflow)
            from_step = get_object_or_404(WorkflowStep, pk=request.POST.get('from_step'), workflow=workflow)
            to_step = get_object_or_404(WorkflowStep, pk=request.POST.get('to_step'), workflow=workflow)
            action_code = _text(request.POST, 'transition_action').lower().replace(' ', '_')
            if not action_code:
                messages.error(request, 'Transition action is required.')
                return redirect(_wf_url(workflow.pk, q=q))
            condition = _transition_condition(request.POST)
            try:
                tr = WorkflowTransition.objects.create(
                    workflow=workflow, from_step=from_step, to_step=to_step,
                    action=action_code, label=_text(request.POST, 'label') or action_code,
                    condition=condition,
                    require_fields_complete=_checked(request.POST, 'require_fields_complete')
                    if request.POST.get('require_fields_submitted') else True,
                )
            except IntegrityError:
                messages.error(request, 'That transition already exists.')
                return redirect(_wf_url(workflow.pk, q=q))
            _audit(request, 'WORKFLOW_TRANSITION_ADDED', tr, {'action': action_code})
            messages.success(request, f'Transition {from_step.code} → {to_step.code} added.')
            return redirect(_wf_url(workflow.pk, preview=True, q=q))

        if action == 'delete_transition':
            tr = get_object_or_404(
                WorkflowTransition.objects.select_related('workflow'), pk=request.POST.get('transition'),
            )
            _require_draft(tr.workflow)
            workflow_id = tr.workflow_id
            tr.delete()
            messages.success(request, 'Transition removed.')
            return redirect(_wf_url(workflow_id, preview=True, q=q))

        if action == 'publish':
            workflow = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            notes = _text(request.POST, 'notes')
            publish_workflow(workflow, user=request.user, notes=notes)
            _audit(request, 'WORKFLOW_PUBLISHED', workflow, {'code': workflow.code, 'version': workflow.version})
            messages.success(
                request,
                f'Published {workflow.code} v{workflow.version}. In-progress orders stay on their bound version.',
            )
            return redirect(_wf_url(workflow.pk, preview=True, q=q))

        if action == 'clone_version':
            source = get_object_or_404(WorkflowDefinition, pk=request.POST.get('workflow'))
            clone = clone_workflow_version(source, user=request.user)
            _audit(request, 'WORKFLOW_CLONED', clone, {'from': source.version, 'to': clone.version})
            messages.success(request, f'Created draft {clone.code} v{clone.version} for editing.')
            return redirect(_wf_url(clone.pk, q=q))

        if action == 'add_assignment':
            code = _text(request.POST, 'workflow_code')
            if not code:
                messages.error(request, 'Workflow code is required for assignment.')
                return redirect('platform-workflows')
            test_id = _text(request.POST, 'test') or None
            group_id = _text(request.POST, 'service_group') or None
            if not test_id and not group_id:
                messages.error(request, 'Assign to a test or a service group.')
                return redirect(_wf_url(request.POST.get('workflow'), q=q) if request.POST.get('workflow') else 'platform-workflows')
            row = WorkflowAssignment.objects.create(
                workflow_code=code,
                test_id=test_id,
                service_group_id=group_id if not test_id else None,
                priority=_int(request.POST, 'priority', 100),
                is_active=True,
            )
            _audit(request, 'WORKFLOW_ASSIGNMENT_CREATED', row, {'code': code})
            messages.success(request, f'Assigned {code} to {"test" if test_id else "service group"}.')
            return redirect(_wf_url(request.POST.get('workflow'), q=q) if request.POST.get('workflow') else 'platform-workflows')

        if action == 'delete_assignment':
            row = get_object_or_404(WorkflowAssignment, pk=request.POST.get('assignment'))
            code = row.workflow_code
            row.delete()
            messages.success(request, 'Assignment removed.')
            wid = request.POST.get('workflow')
            return redirect(_wf_url(wid, q=q) if wid else 'platform-workflows')

        if action == 'toggle_assignment':
            row = get_object_or_404(WorkflowAssignment, pk=request.POST.get('assignment'))
            row.is_active = not row.is_active
            row.save(update_fields=['is_active', 'updated_at'])
            messages.success(request, f'Assignment {"activated" if row.is_active else "deactivated"}.')
            return redirect(_wf_url(request.POST.get('workflow'), q=q) if request.POST.get('workflow') else 'platform-workflows')

    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        wid = request.POST.get('workflow')
        return redirect(_wf_url(wid, q=q) if wid else 'platform-workflows')

    messages.error(request, 'Unknown workflow action.')
    return redirect('platform-workflows')


# --- QC materials / runs ---------------------------------------------------

def build_qc_materials_context(request):
    query = _text(request.GET, 'q')
    rows = QcMaterial.objects.select_related('centre', 'test').order_by('-created_at')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(lot_number__icontains=query) | Q(test__code__icontains=query))
    return {
        'directory': paginate(request, rows, 'page'),
        'query': query,
        'centres': Centre.objects.filter(is_active=True).order_by('code'),
        'tests': Test.objects.filter(is_active=True).order_by('code'),
        'page_sizes': PAGE_SIZES,
    }


def handle_qc_materials_post(request):
    action = request.POST.get('action')
    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        test = get_object_or_404(Test, pk=request.POST.get('test'))
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Material name is required.')
            return redirect('platform-qc-materials')
        expires = parse_date(_text(request.POST, 'expires_on')) if _text(request.POST, 'expires_on') else None
        row = QcMaterial.objects.create(
            centre=centre, test=test, name=name, lot_number=_text(request.POST, 'lot_number'),
            expires_on=expires, is_active=True,
        )
        _audit(request, 'QC_MATERIAL_CREATED', row, {'name': name}, centre=centre)
        messages.success(request, f'QC material {name} created.')
        return redirect('platform-qc-materials')
    if action == 'toggle':
        row = get_object_or_404(QcMaterial, pk=request.POST.get('material'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{row.name} marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-qc-materials')
    return redirect('platform-qc-materials')


def build_qc_runs_context(request):
    rows = QcRun.objects.select_related('material__centre', 'material__test', 'parameter', 'entered_by').order_by('-run_at')
    return {
        'directory': paginate(request, rows, 'page'),
        'materials': QcMaterial.objects.filter(is_active=True).select_related('test', 'centre').order_by('name'),
        'parameters': TestParameter.objects.select_related('test').order_by('test__code', 'display_order'),
        'page_sizes': PAGE_SIZES,
    }


def handle_qc_runs_post(request):
    if request.POST.get('action') != 'create':
        return redirect('platform-qc-runs')
    material = get_object_or_404(QcMaterial, pk=request.POST.get('material'))
    parameter = get_object_or_404(TestParameter, pk=request.POST.get('parameter'))
    value = _text(request.POST, 'value')
    if not value:
        messages.error(request, 'QC value is required.')
        return redirect('platform-qc-runs')
    row = QcRun.objects.create(
        material=material, parameter=parameter, value=value,
        acceptable=_checked(request.POST, 'acceptable') if request.POST.get('acceptable_submitted') else True,
        entered_by=request.user, run_at=timezone.now(),
    )
    _audit(request, 'QC_RUN_CREATED', row, {'value': value}, centre=material.centre)
    messages.success(request, 'QC run recorded.')
    return redirect('platform-qc-runs')


# --- Result templates ------------------------------------------------------

def build_result_templates_context(request):
    query = _text(request.GET, 'q')
    rows = ResultTemplate.objects.select_related('test', 'published_by').prefetch_related('fields').order_by('test__code', 'name', '-version')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(test__code__icontains=query) | Q(test__name__icontains=query))
    return {
        'directory': paginate(request, rows, 'page'),
        'query': query,
        'tests': Test.objects.filter(is_active=True).order_by('code'),
        'page_sizes': PAGE_SIZES,
    }


def build_result_template_builder_context(request, pk):
    template = get_object_or_404(
        ResultTemplate.objects.select_related('test', 'published_by', 'cloned_from')
        .prefetch_related('fields__parameter'),
        pk=pk,
    )
    used_ids = set(template.fields.values_list('parameter_id', flat=True))
    available = template.test.parameters.exclude(pk__in=used_ids).order_by('display_order', 'name')
    fields = template.fields.select_related('parameter').order_by('display_order', 'parameter__name')
    next_order = (fields.order_by('-display_order').values_list('display_order', flat=True).first() or 0) + 10
    return {
        'template': template,
        'fields': fields,
        'available_parameters': available,
        'next_order': next_order,
        'builder_subtitle': f'{template.test.code} — {template.test.name} · versioned clinical report layout',
        'sensitive': True,
    }


def handle_result_templates_post(request):
    action = request.POST.get('action')
    if action == 'create':
        test = get_object_or_404(Test, pk=request.POST.get('test'))
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Template name is required.')
            return redirect('platform-result-templates')
        try:
            version = _int(request.POST, 'version', 1)
            row = ResultTemplate.objects.create(
                test=test, name=name, version=version, status='DRAFT',
                instructions=_text(request.POST, 'instructions'),
                report_header=_text(request.POST, 'report_header'),
                report_footer=_text(request.POST, 'report_footer'),
                is_active=True,
            )
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc) if not isinstance(exc, IntegrityError) else 'That template name/version already exists for this test.')
            return redirect('platform-result-templates')
        # Seed fields from all current test parameters (CBC-style starter).
        for idx, param in enumerate(test.parameters.order_by('display_order', 'name')):
            ResultTemplateField.objects.create(template=row, parameter=param, display_order=idx * 10)
        _audit(request, 'RESULT_TEMPLATE_CREATED', row, {'test': test.code, 'name': name})
        messages.success(request, f'Result template {name} v{row.version} created. Configure fields next.')
        return redirect('platform-result-template-builder', pk=row.pk)
    if action == 'toggle':
        row = get_object_or_404(ResultTemplate, pk=request.POST.get('template'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{row.name} marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-result-templates')
    return redirect('platform-result-templates')


def handle_result_template_builder_post(request, pk):
    template = get_object_or_404(ResultTemplate, pk=pk)
    action = request.POST.get('action')
    if template.status == 'PUBLISHED' and action in ('save_meta', 'add_field', 'remove_field', 'reorder_field', 'import_all'):
        messages.error(request, 'Published templates are immutable. Clone a new version to edit.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'save_meta':
        template.name = _text(request.POST, 'name') or template.name
        template.instructions = _text(request.POST, 'instructions')
        template.report_header = _text(request.POST, 'report_header')
        template.report_footer = _text(request.POST, 'report_footer')
        template.save(update_fields=['name', 'instructions', 'report_header', 'report_footer', 'updated_at'])
        messages.success(request, 'Template details saved.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'add_field':
        param = get_object_or_404(TestParameter, pk=request.POST.get('parameter'), test=template.test)
        try:
            order = _int(request.POST, 'display_order', (template.fields.count() + 1) * 10)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect('platform-result-template-builder', pk=template.pk)
        required = None
        if request.POST.get('is_required_submitted'):
            required = _checked(request.POST, 'is_required')
        try:
            ResultTemplateField.objects.create(
                template=template, parameter=param, display_order=order,
                is_required=required,
                show_reference_range=_checked(request.POST, 'show_reference_range') if request.POST.get('show_ref_submitted') else True,
                include_in_report=_checked(request.POST, 'include_in_report') if request.POST.get('include_submitted') else True,
                section_label=_text(request.POST, 'section_label'),
            )
        except IntegrityError:
            messages.error(request, 'That parameter is already on this template.')
            return redirect('platform-result-template-builder', pk=template.pk)
        messages.success(request, f'Added {param.code}.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'remove_field':
        field = get_object_or_404(ResultTemplateField, pk=request.POST.get('field'), template=template)
        field.delete()
        messages.success(request, 'Field removed.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'reorder_field':
        field = get_object_or_404(ResultTemplateField, pk=request.POST.get('field'), template=template)
        try:
            field.display_order = _int(request.POST, 'display_order', field.display_order)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect('platform-result-template-builder', pk=template.pk)
        field.section_label = _text(request.POST, 'section_label')
        if request.POST.get('is_required_submitted'):
            field.is_required = _checked(request.POST, 'is_required')
        field.show_reference_range = _checked(request.POST, 'show_reference_range') if request.POST.get('show_ref_submitted') else field.show_reference_range
        field.include_in_report = _checked(request.POST, 'include_in_report') if request.POST.get('include_submitted') else field.include_in_report
        field.save()
        messages.success(request, 'Field updated.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'import_all':
        existing = set(template.fields.values_list('parameter_id', flat=True))
        order = template.fields.count() * 10
        added = 0
        for param in template.test.parameters.order_by('display_order', 'name'):
            if param.pk in existing:
                continue
            order += 10
            ResultTemplateField.objects.create(template=template, parameter=param, display_order=order)
            added += 1
        messages.success(request, f'Imported {added} parameter(s).')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'publish':
        if not template.fields.exists():
            messages.error(request, 'Add at least one field before publishing.')
            return redirect('platform-result-template-builder', pk=template.pk)
        publish_template(template, request.user)
        _audit(request, 'RESULT_TEMPLATE_PUBLISHED', template, {'version': template.version})
        messages.success(request, f'{template.name} v{template.version} published. Released reports will freeze this version at authentication.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'retire':
        template.status = 'RETIRED'
        template.is_active = False
        template.save(update_fields=['status', 'is_active', 'updated_at'])
        messages.success(request, 'Template retired.')
        return redirect('platform-result-template-builder', pk=template.pk)
    if action == 'clone':
        clone = clone_template_version(template, user=request.user)
        _audit(request, 'RESULT_TEMPLATE_CLONED', clone, {'from': template.version, 'to': clone.version})
        messages.success(request, f'Created draft v{clone.version}.')
        return redirect('platform-result-template-builder', pk=clone.pk)
    return redirect('platform-result-template-builder', pk=template.pk)


# --- Analyzers / mappings / messages / gateways ----------------------------

def build_analyzers_context(request):
    query = _text(request.GET, 'q')
    rows = Analyzer.objects.select_related('centre').order_by('centre__code', 'name')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(manufacturer__icontains=query) | Q(model__icontains=query))
    selected = Analyzer.objects.filter(pk=request.GET.get('analyzer')).first() if request.GET.get('analyzer') else None
    return {
        'directory': paginate(request, rows, 'page'),
        'selected': selected,
        'query': query,
        'centres': Centre.objects.filter(is_active=True).order_by('code'),
        'protocols': Analyzer.PROTOCOLS,
        'page_sizes': PAGE_SIZES,
    }


def handle_analyzers_post(request):
    action = request.POST.get('action')
    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        name = _text(request.POST, 'name')
        protocol = _text(request.POST, 'protocol')
        if not name or protocol not in dict(Analyzer.PROTOCOLS):
            messages.error(request, 'Name and a valid protocol are required.')
            return redirect('platform-analyzers')
        port_raw = _text(request.POST, 'port')
        try:
            row = Analyzer.objects.create(
                centre=centre, name=name, protocol=protocol,
                manufacturer=_text(request.POST, 'manufacturer'),
                model=_text(request.POST, 'model'),
                host=_text(request.POST, 'host'),
                port=int(port_raw) if port_raw else None,
                serial_port=_text(request.POST, 'serial_port'),
                instrument_identifier=_text(request.POST, 'instrument_identifier'),
                is_active=True,
            )
        except IntegrityError:
            messages.error(request, 'An analyzer with that name already exists at this centre.')
            return redirect('platform-analyzers')
        _audit(request, 'ANALYZER_CREATED', row, {'name': name}, centre=centre)
        messages.success(request, f'Analyzer {name} created.')
        return redirect(f'/platform/analyzers/?analyzer={row.pk}')
    if action == 'update':
        row = get_object_or_404(Analyzer, pk=request.POST.get('analyzer'))
        row.name = _text(request.POST, 'name') or row.name
        protocol = _text(request.POST, 'protocol')
        if protocol in dict(Analyzer.PROTOCOLS):
            row.protocol = protocol
        row.manufacturer = _text(request.POST, 'manufacturer')
        row.model = _text(request.POST, 'model')
        row.host = _text(request.POST, 'host')
        port_raw = _text(request.POST, 'port')
        row.port = int(port_raw) if port_raw else None
        row.serial_port = _text(request.POST, 'serial_port')
        row.instrument_identifier = _text(request.POST, 'instrument_identifier')
        row.is_active = _checked(request.POST, 'is_active')
        row.save()
        _audit(request, 'ANALYZER_UPDATED', row, {'name': row.name}, centre=row.centre)
        messages.success(request, f'Analyzer {row.name} saved.')
        return redirect(f'/platform/analyzers/?analyzer={row.pk}')
    return redirect('platform-analyzers')


def build_analyzer_mappings_context(request):
    rows = AnalyzerTestMapping.objects.select_related('analyzer__centre', 'test', 'parameter').order_by('analyzer__name', 'instrument_test_code')
    return {
        'directory': paginate(request, rows, 'page'),
        'analyzers': Analyzer.objects.filter(is_active=True).select_related('centre').order_by('name'),
        'tests': Test.objects.filter(is_active=True).order_by('code'),
        'parameters': TestParameter.objects.select_related('test').order_by('test__code', 'code'),
        'page_sizes': PAGE_SIZES,
    }


def handle_analyzer_mappings_post(request):
    action = request.POST.get('action')
    if action == 'create':
        analyzer = get_object_or_404(Analyzer, pk=request.POST.get('analyzer'))
        test = get_object_or_404(Test, pk=request.POST.get('test'))
        code = _text(request.POST, 'instrument_test_code')
        if not code:
            messages.error(request, 'Instrument test code is required.')
            return redirect('platform-analyzer-mappings')
        parameter = None
        if request.POST.get('parameter'):
            parameter = get_object_or_404(TestParameter, pk=request.POST.get('parameter'), test=test)
        try:
            row = AnalyzerTestMapping.objects.create(
                analyzer=analyzer, instrument_test_code=code, test=test,
                parameter=parameter, unit_override=_text(request.POST, 'unit_override'), enabled=True,
            )
        except IntegrityError:
            messages.error(request, 'That instrument code is already mapped on this analyzer.')
            return redirect('platform-analyzer-mappings')
        _audit(request, 'ANALYZER_MAPPING_CREATED', row, {'code': code}, centre=analyzer.centre)
        messages.success(request, 'Mapping created.')
        return redirect('platform-analyzer-mappings')
    if action == 'toggle':
        row = get_object_or_404(AnalyzerTestMapping, pk=request.POST.get('mapping'))
        row.enabled = not row.enabled
        row.save(update_fields=['enabled', 'updated_at'])
        messages.success(request, f'Mapping {"enabled" if row.enabled else "disabled"}.')
        return redirect('platform-analyzer-mappings')
    return redirect('platform-analyzer-mappings')


def build_analyzer_messages_context(request):
    status = _text(request.GET, 'status')
    rows = AnalyzerMessage.objects.select_related('analyzer__centre', 'sample', 'reviewed_by').order_by('-received_at')
    if status:
        rows = rows.filter(status=status)
    return {
        'directory': paginate(request, rows, 'page'),
        'status': status,
        'statuses': AnalyzerMessage.STATUS,
        'page_sizes': PAGE_SIZES,
    }


def build_gateways_context(request):
    rows = GatewayRegistration.objects.select_related('centre').order_by('centre__code', 'name')
    return {
        'directory': paginate(request, rows, 'page'),
        'centres': Centre.objects.filter(is_active=True).order_by('code'),
        'page_sizes': PAGE_SIZES,
        'new_api_key': request.session.pop('gateway_api_key', None),
    }


def handle_gateways_post(request):
    action = request.POST.get('action')
    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Gateway name is required.')
            return redirect('platform-gateways')
        api_key = secrets.token_urlsafe(32)
        row = GatewayRegistration.objects.create(
            centre=centre, name=name, api_key=api_key,
            hostname=_text(request.POST, 'hostname'), is_active=True,
        )
        request.session['gateway_api_key'] = api_key
        _audit(request, 'GATEWAY_CREATED', row, {'name': name}, centre=centre)
        messages.success(request, f'Gateway {name} registered. Copy the API key now — it is shown once.')
        return redirect('platform-gateways')
    if action == 'toggle':
        row = get_object_or_404(GatewayRegistration, pk=request.POST.get('gateway'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'Gateway marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-gateways')
    if action == 'rotate_key':
        row = get_object_or_404(GatewayRegistration, pk=request.POST.get('gateway'))
        api_key = secrets.token_urlsafe(32)
        row.api_key = api_key
        row.save(update_fields=['api_key', 'updated_at'])
        request.session['gateway_api_key'] = api_key
        _audit(request, 'GATEWAY_KEY_ROTATED', row, {'name': row.name}, centre=row.centre)
        messages.success(request, 'API key rotated. Copy the new key now.')
        return redirect('platform-gateways')
    return redirect('platform-gateways')


def build_pacs_failures_context(request):
    show = _text(request.GET, 'show') or 'open'
    rows = PacsFailure.objects.select_related('exam__order__visit__centre', 'exam__order__procedure').order_by('-created_at')
    if show == 'open':
        rows = rows.filter(resolved_at__isnull=True)
    elif show == 'resolved':
        rows = rows.filter(resolved_at__isnull=False)
    return {'directory': paginate(request, rows, 'page'), 'show': show, 'page_sizes': PAGE_SIZES}


def handle_pacs_failures_post(request):
    if request.POST.get('action') == 'resolve':
        row = get_object_or_404(PacsFailure, pk=request.POST.get('failure'))
        row.resolved_at = timezone.now()
        row.save(update_fields=['resolved_at', 'updated_at'])
        _audit(request, 'PACS_FAILURE_RESOLVED', row, {'reason': row.reason})
        messages.success(request, 'PACS failure marked resolved.')
    return redirect('platform-pacs-failures')


def build_modalities_context(request):
    from radiology.models import ImagingModality
    from radiology.worklist import listener_status, scheduled_exams

    query = _text(request.GET, 'q')
    rows = ImagingModality.objects.select_related('centre').order_by('centre__code', 'name')
    if query:
        rows = rows.filter(
            Q(name__icontains=query) | Q(code__icontains=query) | Q(ae_title__icontains=query) | Q(host__icontains=query)
        )
    selected = None
    worklist = []
    chosen = request.GET.get('modality')
    if chosen:
        selected = ImagingModality.objects.select_related('centre').filter(pk=chosen).first()
        if selected and selected.ae_title:
            worklist = scheduled_exams(selected.ae_title)[:30]
    return {
        'directory': paginate(request, rows, 'page'),
        'selected': selected,
        'worklist': worklist,
        'query': query,
        'centres': Centre.objects.filter(is_active=True).order_by('code'),
        'modality_types': ImagingModality.MODALITY_TYPES,
        'listener': listener_status(),
        'page_sizes': PAGE_SIZES,
    }


def _modality_connection(post):
    from radiology.worklist import clean_ae

    port_raw = _text(post, 'port')
    port = None
    if port_raw:
        try:
            port = int(port_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Port must be a whole number.') from exc
        if port < 1 or port > 65535:
            raise ValidationError('Port must be between 1 and 65535.')
    host = _text(post, 'host') or None
    return {
        'name': _text(post, 'name'),
        'ae_title': clean_ae(post.get('ae_title')),
        'host': host,
        'port': port,
        'worklist_enabled': _checked(post, 'worklist_enabled'),
    }


def handle_modalities_post(request):
    from django.conf import settings
    from radiology.dicom import echo_modality
    from radiology.models import ImagingModality
    from radiology.worklist import republish_modality, start_mwl_server

    action = request.POST.get('action')
    if action == 'start_listener':
        ok, message = start_mwl_server()
        (messages.success if ok else messages.error)(request, message)
        _audit(request, 'MWL_LISTENER_START', request.user, {'ok': ok, 'message': message})
        return redirect('platform-modalities')

    if action == 'test_connection':
        modality = get_object_or_404(ImagingModality, pk=request.POST.get('modality'))
        try:
            ok, message = echo_modality(modality, calling_ae=getattr(settings, 'RIS_CALLING_AE', 'HFDN_SCU'))
        except ValueError as exc:
            ok, message = False, str(exc)
        except Exception as exc:
            ok, message = False, str(exc)
        modality.last_echo_ok = ok
        modality.last_echo_at = timezone.now()
        modality.last_echo_message = message[:300]
        modality.save(update_fields=['last_echo_ok', 'last_echo_at', 'last_echo_message', 'updated_at'])
        _audit(request, 'MODALITY_C_ECHO', modality, {'ok': ok, 'message': message}, centre=modality.centre)
        (messages.success if ok else messages.error)(request, message)
        return redirect(f'/platform/modalities/?modality={modality.pk}')

    if action not in ('create', 'update'):
        messages.error(request, 'Unknown modality action.')
        return redirect('platform-modalities')

    try:
        fields = _modality_connection(request.POST)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('platform-modalities')
    if not fields['name']:
        messages.error(request, 'Name is required.')
        return redirect('platform-modalities')

    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        code = _text(request.POST, 'code').upper()
        modality_type = _text(request.POST, 'modality_type')
        if not code or modality_type not in dict(ImagingModality.MODALITY_TYPES):
            messages.error(request, 'Code and modality type are required.')
            return redirect('platform-modalities')
        if ImagingModality.objects.filter(centre=centre, code__iexact=code).exists():
            messages.error(request, 'That modality code already exists at this centre.')
            return redirect('platform-modalities')
        modality = ImagingModality.objects.create(
            centre=centre, code=code, modality_type=modality_type, is_active=True, **fields,
        )
        _audit(request, 'IMAGING_MODALITY_CREATED', modality, {'code': modality.code}, centre=centre)
        messages.success(request, f'{modality.name} was saved.')
    else:
        modality = get_object_or_404(ImagingModality, pk=request.POST.get('modality'))
        for key, value in fields.items():
            setattr(modality, key, value)
        modality.is_active = _checked(request.POST, 'is_active')
        try:
            modality.save()
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect(f'/platform/modalities/?modality={modality.pk}')
        _audit(request, 'IMAGING_MODALITY_UPDATED', modality, {'code': modality.code, 'ae_title': modality.ae_title}, centre=modality.centre)
        messages.success(request, f'{modality.name} connection was saved.')
    sent = republish_modality(modality)
    if sent:
        messages.success(request, f'{sent} paid study(s) are on the {modality.name} worklist.')
    return redirect(f'/platform/modalities/?modality={modality.pk}')


def build_sample_codes_context(request):
    rows = SampleCodeConfig.objects.select_related('centre').order_by('centre__code')
    used = set(SampleCodeConfig.objects.values_list('numeric_code', flat=True))
    centres = Centre.objects.filter(is_active=True, sample_code_config__isnull=True).order_by('code')
    return {
        'directory': paginate(request, rows, 'page'),
        'centres': centres,
        'used_digits': sorted(used),
        'page_sizes': PAGE_SIZES,
    }


def handle_sample_codes_post(request):
    action = request.POST.get('action')
    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        digit = _text(request.POST, 'numeric_code')
        if not digit.isdigit() or len(digit) != 1:
            messages.error(request, 'Sample digit must be a single digit 0–9.')
            return redirect('platform-sample-codes')
        try:
            row = SampleCodeConfig.objects.create(centre=centre, numeric_code=digit, is_active=True)
        except IntegrityError:
            messages.error(request, 'That centre already has a digit, or the digit is already assigned.')
            return redirect('platform-sample-codes')
        _audit(request, 'SAMPLE_CODE_CREATED', row, {'digit': digit}, centre=centre)
        messages.success(request, f'Assigned digit {digit} to {centre.code}.')
        return redirect('platform-sample-codes')
    if action == 'update':
        row = get_object_or_404(SampleCodeConfig, pk=request.POST.get('config'))
        digit = _text(request.POST, 'numeric_code')
        if not digit.isdigit() or len(digit) != 1:
            messages.error(request, 'Sample digit must be a single digit 0–9.')
            return redirect('platform-sample-codes')
        row.numeric_code = digit
        row.is_active = _checked(request.POST, 'is_active')
        try:
            row.save()
        except IntegrityError:
            messages.error(request, 'That digit is already assigned to another centre.')
            return redirect('platform-sample-codes')
        _audit(request, 'SAMPLE_CODE_UPDATED', row, {'digit': digit}, centre=row.centre)
        messages.success(request, 'Sample code config saved.')
        return redirect('platform-sample-codes')
    return redirect('platform-sample-codes')


# --- Finance history -------------------------------------------------------

def build_bill_amendments_context(request):
    rows = BillAmendment.objects.select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'authorised_by',
    ).order_by('-created_at')
    return {'directory': paginate(request, rows, 'page'), 'page_sizes': PAGE_SIZES}


def build_refunds_context(request):
    rows = Refund.objects.select_related(
        'invoice__visit__patient', 'invoice__visit__centre', 'payment', 'authorised_by',
    ).order_by('-created_at')
    return {'directory': paginate(request, rows, 'page'), 'page_sizes': PAGE_SIZES}


# --- Notifications ---------------------------------------------------------

def build_notification_templates_context(request):
    rows = NotificationTemplate.objects.order_by('key')
    return {
        'directory': paginate(request, rows, 'page'),
        'channels': NotificationOutbox.CHANNELS,
        'allowed_variables': sorted(ALLOWED_TEMPLATE_VARIABLES),
        'page_sizes': PAGE_SIZES,
    }


def handle_notification_templates_post(request):
    action = request.POST.get('action')
    if action == 'create':
        key = _text(request.POST, 'key')
        name = _text(request.POST, 'name')
        channel = _text(request.POST, 'channel')
        body = _text(request.POST, 'body_template')
        subject = _text(request.POST, 'subject_template')
        if not key or not name or channel not in dict(NotificationOutbox.CHANNELS) or not body:
            messages.error(request, 'Key, name, channel, and body are required.')
            return redirect('platform-notification-templates')
        try:
            validate_template_text(subject)
            validate_template_text(body)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('platform-notification-templates')
        try:
            row = NotificationTemplate.objects.create(
                key=key, name=name, channel=channel,
                subject_template=subject,
                body_template=body, is_active=True,
            )
        except IntegrityError:
            messages.error(request, 'That template key already exists.')
            return redirect('platform-notification-templates')
        _audit(request, 'NOTIFICATION_TEMPLATE_CREATED', row, {'key': key})
        messages.success(request, f'Template {name} created.')
        return redirect('platform-notification-templates')
    if action == 'update':
        row = get_object_or_404(NotificationTemplate, pk=request.POST.get('template'))
        row.name = _text(request.POST, 'name') or row.name
        channel = _text(request.POST, 'channel')
        if channel in dict(NotificationOutbox.CHANNELS):
            row.channel = channel
        row.subject_template = _text(request.POST, 'subject_template')
        row.body_template = _text(request.POST, 'body_template') or row.body_template
        row.is_active = _checked(request.POST, 'is_active')
        if row.is_active:
            try:
                validate_template_text(row.subject_template)
                validate_template_text(row.body_template)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
                return redirect('platform-notification-templates')
        row.save()
        _audit(request, 'NOTIFICATION_TEMPLATE_UPDATED', row, {'key': row.key})
        messages.success(request, f'Template {row.name} saved.')
        return redirect('platform-notification-templates')
    return redirect('platform-notification-templates')


def build_notification_outbox_context(request):
    status = _text(request.GET, 'status')
    rows = NotificationOutbox.objects.select_related('centre', 'visit', 'created_by').order_by('-created_at')
    if status:
        rows = rows.filter(status=status)
    return {
        'directory': paginate(request, rows, 'page'),
        'status': status,
        'statuses': NotificationOutbox.STATUSES,
        'page_sizes': PAGE_SIZES,
    }


def handle_notification_outbox_post(request):
    if request.POST.get('action') == 'cancel':
        row = get_object_or_404(NotificationOutbox, pk=request.POST.get('message'))
        if row.status in ('QUEUED', 'SENDING', 'FAILED'):
            row.status = 'CANCELLED'
            row.save(update_fields=['status', 'updated_at'])
            _audit(request, 'NOTIFICATION_CANCELLED', row, {'recipient': row.recipient}, centre=row.centre)
            messages.success(request, 'Message cancelled.')
        else:
            messages.error(request, 'Only queued, sending, or failed messages can be cancelled.')
    return redirect('platform-notification-outbox')


# --- Portals / inventory ---------------------------------------------------

def build_physician_invites_context(request):
    rows = PhysicianInvite.objects.select_related('doctor', 'invited_by').order_by('-created_at')
    return {
        'directory': paginate(request, rows, 'page'),
        'doctors': ReferralDoctor.objects.filter(is_active=True).select_related('centre').order_by('last_name'),
        'page_sizes': PAGE_SIZES,
    }


def handle_physician_invites_post(request):
    if request.POST.get('action') != 'create':
        return redirect('platform-physician-invites')
    doctor = get_object_or_404(ReferralDoctor, pk=request.POST.get('doctor'))
    email = _text(request.POST, 'email')
    if not email:
        messages.error(request, 'Invite email is required.')
        return redirect('platform-physician-invites')
    days = _int(request.POST, 'days', 7)
    row = PhysicianInvite.create_for(doctor, email, request.user, days=days)
    _audit(request, 'PHYSICIAN_INVITE_CREATED', row, {'email': email, 'doctor': doctor.doctor_code})
    messages.success(request, f'Invite created for {email}. Token: {row.invite_token}')
    return redirect('platform-physician-invites')


def build_inventory_skus_context(request):
    query = _text(request.GET, 'q')
    rows = ReagentSku.objects.select_related('centre').order_by('centre__code', 'sku_code')
    if query:
        rows = rows.filter(Q(sku_code__icontains=query) | Q(name__icontains=query))
    return {
        'directory': paginate(request, rows, 'page'),
        'query': query,
        'centres': Centre.objects.filter(is_active=True).order_by('code'),
        'page_sizes': PAGE_SIZES,
    }


def handle_inventory_skus_post(request):
    action = request.POST.get('action')
    if action == 'create':
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        code = _text(request.POST, 'sku_code')
        name = _text(request.POST, 'name')
        if not code or not name:
            messages.error(request, 'SKU code and name are required.')
            return redirect('platform-inventory-skus')
        try:
            reorder = Decimal(_text(request.POST, 'reorder_level') or '0')
        except InvalidOperation:
            messages.error(request, 'Reorder level must be a number.')
            return redirect('platform-inventory-skus')
        try:
            row = ReagentSku.objects.create(
                centre=centre, sku_code=code, name=name,
                unit=_text(request.POST, 'unit') or 'unit',
                reorder_level=reorder, is_active=True,
            )
        except IntegrityError:
            messages.error(request, 'That SKU already exists at this centre.')
            return redirect('platform-inventory-skus')
        _audit(request, 'REAGENT_SKU_CREATED', row, {'sku': code}, centre=centre)
        messages.success(request, f'SKU {code} created.')
        return redirect('platform-inventory-skus')
    if action == 'toggle':
        row = get_object_or_404(ReagentSku, pk=request.POST.get('sku'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{row.sku_code} marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-inventory-skus')
    return redirect('platform-inventory-skus')


# --- TAT rules & pause reasons ----------------------------------------------

def build_tat_rules_context(request):
    query = _text(request.GET, 'q')
    rules = TatRule.objects.select_related('test', 'service_group').order_by('name')
    if query:
        rules = rules.filter(
            Q(name__icontains=query) | Q(test__code__icontains=query) | Q(test__name__icontains=query)
            | Q(service_group__name__icontains=query) | Q(department__icontains=query)
        )
    return {
        'directory': paginate(request, rules, 'page'),
        'reasons': TatPauseReason.objects.order_by('name'),
        'query': query,
        'tests': Test.objects.filter(is_active=True).order_by('code'),
        'service_groups': ServiceGroup.objects.filter(is_active=True).order_by('name'),
        'clock_starts': TatRule.CLOCK_START,
        'clock_ends': TatRule.CLOCK_END,
        'page_sizes': PAGE_SIZES,
    }


def handle_tat_rules_post(request):
    action = request.POST.get('action')
    if action == 'create_rule':
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Rule name is required.')
            return redirect('platform-tat-rules')
        try:
            expected = _int(request.POST, 'expected_minutes', 1440)
            warning = _int(request.POST, 'warning_minutes', 0) or None
            escalation = _int(request.POST, 'escalation_minutes', 0) or None
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect('platform-tat-rules')
        test = Test.objects.filter(pk=request.POST.get('test')).first() if request.POST.get('test') else None
        group = ServiceGroup.objects.filter(pk=request.POST.get('service_group')).first() if request.POST.get('service_group') else None
        if not test and not group:
            messages.error(request, 'Select a test or a service group.')
            return redirect('platform-tat-rules')
        roles = [r.strip() for r in _text(request.POST, 'escalation_roles').split(',') if r.strip()]
        row = TatRule.objects.create(
            name=name, test=test, service_group=group,
            department=_text(request.POST, 'department'),
            clock_start=_text(request.POST, 'clock_start') or 'PAYMENT',
            clock_end=_text(request.POST, 'clock_end') or 'RELEASE',
            expected_minutes=expected,
            warning_minutes=warning,
            escalation_minutes=escalation,
            escalation_roles=roles or ['Lab Manager'],
            allow_pause=_checked(request.POST, 'allow_pause') if request.POST.get('allow_pause_submitted') else True,
            notes=_text(request.POST, 'notes'),
            is_active=True,
        )
        _audit(request, 'TAT_RULE_CREATED', row, {'name': name, 'expected': expected})
        messages.success(request, f'TAT rule “{name}” created.')
        return redirect('platform-tat-rules')
    if action == 'toggle_rule':
        row = get_object_or_404(TatRule, pk=request.POST.get('rule'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{row.name} marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-tat-rules')
    if action == 'create_reason':
        code = _text(request.POST, 'code').upper().replace(' ', '_')
        name = _text(request.POST, 'reason_name')
        if not code or not name:
            messages.error(request, 'Pause reason code and name are required.')
            return redirect('platform-tat-rules')
        try:
            row = TatPauseReason.objects.create(
                code=code, name=name,
                pauses_clock=_checked(request.POST, 'pauses_clock') if request.POST.get('pauses_clock_submitted') else True,
                is_active=True,
            )
        except IntegrityError:
            messages.error(request, 'That pause reason code already exists.')
            return redirect('platform-tat-rules')
        _audit(request, 'TAT_PAUSE_REASON_CREATED', row, {'code': code})
        messages.success(request, f'Pause reason {code} created.')
        return redirect('platform-tat-rules')
    if action == 'toggle_reason':
        row = get_object_or_404(TatPauseReason, pk=request.POST.get('reason'))
        row.is_active = not row.is_active
        row.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{row.name} marked {"active" if row.is_active else "inactive"}.')
        return redirect('platform-tat-rules')
    return redirect('platform-tat-rules')
