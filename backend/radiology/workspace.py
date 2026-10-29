"""Radiology screen actions. They use the imaging API, including its centre checks."""
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from billing.visibility import imaging_payment_complete, settled
from core.access import RADIOLOGIST_ROLES, can_access_platform_controls
from core.result_control import user_can_approve_radiology, user_can_open_dispatch, user_can_open_modify
from core.models import UserCentreAccess
from core.paging import paginate
from core.staff_api import call_action, call_create, call_update, describe
from patients.models import Visit
from radiology.api import ImagingOrderViewSet, RadiologyExamViewSet, RadiologyReportViewSet
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport, RadiologyReportTemplate
from radiology.templates_svc import clone_template_version, publish_template, resolve_report_templates


def _finish(status, body, success):
    if 200 <= status < 300:
        return True, success
    return False, describe(body)


def _text(post, key):
    return (post.get(key) or '').strip()


def handle_radiology_post(request):
    action = request.POST.get('action')
    user = request.user
    post = request.POST
    if action == 'create':
        return False, 'Imaging studies are added at registration. They appear here after the paypoint completes payment.'
    gated = (
        'assign', 'assign_radiologist', 'authorize_substitute', 'start', 'end', 'upload_pacs', 'accept', 'draft',
        'update_draft', 'finalize', 'release', 'override', 'addendum', 'reconcile_pacs',
    )
    if action in gated and not imaging_payment_complete(
        order_id=post.get('order') or None, exam_id=post.get('exam') or None, report_id=post.get('report') or None,
    ):
        return False, 'This study stays off the radiology worklist until the paypoint completes payment.'
    if action == 'assign':
        return _finish(*call_action(ImagingOrderViewSet, 'assign', user, post.get('order'), {
            'radiographer': post.get('radiographer'),
        }), 'Radiographer assigned.')
    if action == 'assign_radiologist':
        return _finish(*call_action(ImagingOrderViewSet, 'assign_radiologist', user, post.get('order'), {
            'radiologist': post.get('radiologist'),
        }), 'Radiologist assigned for reporting.')
    if action == 'authorize_substitute':
        return _finish(*call_action(ImagingOrderViewSet, 'authorize_substitute', user, post.get('order'), {
            'substitute': post.get('substitute'), 'reason': post.get('reason', ''),
        }), 'Reporting substitute authorised.')
    if action == 'start':
        return _finish(*call_action(RadiologyExamViewSet, 'start', user, post.get('exam'), {}), 'Exam started. Recorded in examination history.')
    if action == 'end':
        if not post.get('radiologist'):
            return False, 'Select the radiologist who will report this study.'
        names = post.getlist('item_name')
        qtys = post.getlist('quantity')
        supplies = [
            {'name': name, 'quantity': qty}
            for name, qty in zip(names, qtys)
        ]
        return _finish(*call_action(RadiologyExamViewSet, 'end', user, post.get('exam'), {
            'acquisition_notes': post.get('acquisition_notes', ''),
            'supplies': supplies,
            'radiologist': post.get('radiologist'),
        }), 'Exam ended. Duration and items were recorded, and the study was assigned to a radiologist.')
    if action == 'upload_pacs':
        return _finish(*call_action(RadiologyExamViewSet, 'upload_pacs', user, post.get('exam'), {}), 'Study uploaded to the PACS server. It is now on the radiologist worklist.')
    if action == 'accept':
        return _finish(*call_action(RadiologyExamViewSet, 'accept', user, post.get('exam'), {}), 'Study accepted for reporting.')
    if action == 'reconcile_pacs':
        return _finish(*call_action(RadiologyExamViewSet, 'reconcile_pacs', user, post.get('exam'), {}), 'PACS reconcile requested.')
    if action in ('draft', 'update_draft'):
        payload = {
            'indication': post.get('indication', ''),
            'findings': post.get('findings', ''),
        }
        for name in ('technique', 'impression', 'recommendations'):
            if name in post:
                payload[name] = post.get(name, '')
        if post.get('template'):
            payload['template'] = post.get('template')
        if action == 'update_draft':
            return _finish(
                *call_update(RadiologyReportViewSet, user, post.get('report'), payload),
                'Report draft saved.',
            )
        payload['exam'] = post.get('exam')
        return _finish(*call_create(RadiologyReportViewSet, user, payload), 'Report draft saved.')
    if action == 'finalize':
        return _finish(*call_action(RadiologyReportViewSet, 'finalize', user, post.get('report'), {}), 'Report signed.')
    if action == 'approve':
        return _finish(*call_action(RadiologyReportViewSet, 'approve', user, post.get('report'), {}), 'Result approved. It can now be dispatched.')
    if action == 'release':
        return _finish(*call_action(RadiologyReportViewSet, 'release', user, post.get('report'), {}), 'Result dispatched.')
    if action == 'override':
        return _finish(*call_action(RadiologyReportViewSet, 'override', user, post.get('report'), {
            'reason': post.get('reason', ''),
            'indication': post.get('indication', ''),
            'technique': post.get('technique', ''),
            'findings': post.get('findings', ''),
            'impression': post.get('impression', ''),
            'recommendations': post.get('recommendations', ''),
        }), 'Formal amendment recorded with audit trail.')
    if action == 'addendum':
        return _finish(*call_action(RadiologyReportViewSet, 'addendum', user, post.get('report'), {
            'reason': post.get('reason', ''),
            'addendum_text': post.get('addendum_text', ''),
        }), 'Formal addendum recorded with audit trail.')
    return False, 'Choose a radiology action.'


def build_radiology_context(request):
    from django.utils import timezone
    from radiology.desk import (
        SUPPLY_PRESETS, accept_window, exam_duration_label, exam_minutes, is_radiographer, is_reporting_radiologist,
        performing_radiographer_id, radiographer_active_exam, release_expired_assignments, user_may_reassign,
    )

    user = request.user
    release_expired_assignments()
    centre_ids = list(UserCentreAccess.objects.filter(
        user=user, centre__is_active=True, can_view=True,
    ).values_list('centre_id', flat=True))
    orders = settled(
        ImagingOrder.objects.select_related(
            'visit__patient', 'visit__centre', 'modality', 'procedure', 'stat', 'exam',
            'exam__assigned_radiographer', 'exam__assigned_radiologist', 'exam__report',
        ).prefetch_related('exam__activities', 'exam__supplies', 'visit__documents'),
        'imaging_order_id',
    ).order_by('-created_at')
    if not user.is_superuser:
        orders = orders.filter(visit__centre_id__in=centre_ids)
    radiographer = is_radiographer(user)
    radiologist = (is_reporting_radiologist(user) or user_can_approve_radiology(user)) and not radiographer
    if user.is_superuser:
        radiologist = request.GET.get('desk') == 'report'
        radiographer = not radiologist
    returned_to_tech = Q(
        exam__returned_at__isnull=False,
        exam__assigned_radiologist__isnull=True,
        exam__accepted_at__isnull=True,
    )
    if radiologist:
        orders = orders.filter(
            exam__pacs_uploaded_at__isnull=False, exam__status='ACQUIRED',
        ).exclude(returned_to_tech)
        # A radiologist only sees studies assigned to them (or where they are the substitute).
        # Superusers on the reporting desk keep the full centre overview.
        if not user.is_superuser:
            orders = orders.filter(
                Q(exam__assigned_radiologist=user) | Q(exam__reporting_substitute=user),
            )
    scope = (request.GET.get('scope') or 'active').lower()
    if scope not in ('active', 'history'):
        scope = 'active'
    if radiographer and not user.is_superuser and not radiologist:
        mine = (
            Q(exam__started_by=user)
            | Q(exam__assigned_radiographer=user)
            | Q(exam__ended_by=user)
            | Q(exam__pacs_uploaded_by=user)
        )
        if scope == 'history':
            # Full history of exams this radiographer worked — including after PACS / dispatch.
            orders = orders.filter(mine).exclude(status='CANCELLED')
        else:
            returned_mine = returned_to_tech & (
                Q(exam__started_by=user) | Q(exam__assigned_radiographer=user) | Q(exam__ended_by=user)
            )
            orders = orders.filter(
                Q(exam__pacs_uploaded_at__isnull=True) | returned_mine,
            ).exclude(status__in=['COMPLETED', 'CANCELLED'])
            scope = 'active'
    elif not radiographer or radiologist:
        scope = 'active'
    modality = (request.GET.get('modality') or 'ALL').upper()
    counts = {'ALL': orders.count()}
    type_labels = dict(ImagingModality.MODALITY_TYPES)
    type_order = {code: index for index, (code, _label) in enumerate(ImagingModality.MODALITY_TYPES)}
    worklist_types = {
        code for code in orders.values_list('modality__modality_type', flat=True).distinct() if code
    }
    catalogue_qs = ImagingModality.objects.filter(is_active=True)
    if not user.is_superuser:
        catalogue_qs = catalogue_qs.filter(centre_id__in=centre_ids)
    available_types = worklist_types | set(catalogue_qs.values_list('modality_type', flat=True).distinct())
    if modality != 'ALL':
        available_types.add(modality)
    for code in available_types:
        counts[code] = orders.filter(modality__modality_type=code).count()
    modality_pills = sorted(
        [
            {'code': code, 'label': type_labels.get(code, code), 'count': counts.get(code, 0)}
            for code in available_types
        ],
        key=lambda row: (type_order.get(row['code'], 99), row['label'].lower()),
    )
    if modality != 'ALL' and modality in counts:
        orders = orders.filter(modality__modality_type=modality)
    when = (request.GET.get('when') or 'all').lower()
    if when == 'today':
        today = timezone.localdate()
        orders = orders.filter(created_at__date=today)
    from django.utils.dateparse import parse_date
    date_from = parse_date(request.GET.get('date_from') or '')
    date_to = parse_date(request.GET.get('date_to') or '')
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
    visit_query = (request.GET.get('visit') or request.GET.get('patient_bill_id') or '').strip()
    if visit_query:
        orders = orders.filter(visit__visit_id__icontains=visit_query)
    exam_stats = {
        'PENDING': orders.filter(
            Q(exam__report__isnull=True) | Q(exam__report__status='DRAFT'),
        ).count(),
        'REPORTED': orders.filter(exam__report__status='FINAL').count(),
        'APPROVED': orders.filter(exam__report__status='APPROVED').count(),
        'DISPATCHED': orders.filter(exam__report__status__in=['RELEASED', 'AMENDED']).count(),
    }
    status_filter = (request.GET.get('status') or '').upper()
    if status_filter == 'PENDING':
        orders = orders.filter(Q(exam__report__isnull=True) | Q(exam__report__status='DRAFT'))
    elif status_filter == 'REPORTED':
        orders = orders.filter(exam__report__status='FINAL')
    elif status_filter == 'APPROVED':
        orders = orders.filter(exam__report__status='APPROVED')
    elif status_filter == 'DISPATCHED':
        orders = orders.filter(exam__report__status__in=['RELEASED', 'AMENDED'])
    else:
        status_filter = ''
    if getattr(request, '_radiology_export', False):
        return {
            'rows': [_exam_export_row(order) for order in orders[:5000]],
            'stats': exam_stats,
        }
    staff = get_user_model().objects.filter(
        is_active=True, centre_access__centre_id__in=centre_ids, centre_access__can_edit=True,
    ).distinct().order_by('username')
    if user.is_superuser:
        staff = get_user_model().objects.filter(is_active=True).order_by('username')
    radiographers = staff.filter(groups__name='Radiographer').distinct()
    radiologists = staff.filter(groups__name='Radiologist').distinct()
    window = accept_window()
    order_page = paginate(request, orders, 'radpage')
    now = timezone.now()
    for order in order_page.object_list:
        order.available_templates = resolve_report_templates(order.modality.modality_type, user)
        try:
            exam = order.exam
        except ObjectDoesNotExist:
            exam = None
        if exam is not None:
            exam.recent_activities = list(exam.activities.select_related('performed_by')[:8])
            exam.duration_minutes = exam_minutes(exam, now)
            exam.duration_label = exam_duration_label(exam, now)
            exam.supply_rows = list(exam.supplies.all())
            exam.accept_remaining = None
            exam.awaiting_reassignment = bool(
                exam.returned_at and not exam.assigned_radiologist_id and not exam.accepted_at
            )
            exam.viewer_can_reassign = exam.awaiting_reassignment and user_may_reassign(user, exam)
            exam.performed_by_id = performing_radiographer_id(exam)
            if exam.radiologist_assigned_at and not exam.accepted_at and not exam.returned_at:
                elapsed = int((now - exam.radiologist_assigned_at).total_seconds() // 60)
                exam.accept_remaining = max(0, window.accept_minutes - elapsed)
        documents = list(order.visit.documents.all())
        order.request_form = next((row for row in documents if row.document_type == 'REGISTRATION'), None)
        if order.request_form is None:
            order.request_form = next((row for row in documents if row.document_type == 'REFERRAL'), None)
        order.work_status = exam_bucket(order)
    selected = None
    study_id = request.GET.get('study')
    if study_id:
        selected = next((row for row in order_page.object_list if str(row.pk) == study_id), None)
    if selected is None and order_page.object_list:
        selected = order_page.object_list[0]
    reports = settled(
        RadiologyReport.objects.select_related(
            'exam__order__visit__patient', 'exam__order__visit__centre',
            'exam__order__modality', 'author', 'template',
        ),
        'imaging_order_id', 'exam__order_id',
    ).order_by('-updated_at')
    if not user.is_superuser:
        reports = reports.filter(exam__order__visit__centre_id__in=centre_ids)
    if radiologist:
        reports = reports.filter(exam__pacs_uploaded_at__isnull=False)
        if not user.is_superuser:
            reports = reports.filter(
                Q(exam__assigned_radiologist=user) | Q(exam__reporting_substitute=user),
            )
    report_page = paginate(request, reports, 'repage')
    from billing.eligibility import check_visit_release
    for report in report_page.object_list:
        if report.status == 'FINAL':
            report.release_blockers = check_visit_release(report.exam.order.visit, check_workflow=False)
        else:
            report.release_blockers = []
    if selected is not None:
        try:
            selected_report = selected.exam.report
        except ObjectDoesNotExist:
            selected_report = None
        if selected_report is not None and selected_report.status == 'FINAL':
            selected_report.release_blockers = check_visit_release(selected.exam.order.visit, check_workflow=False)
    return {
        'orders': order_page,
        'reports': report_page,
        'selected': selected,
        'radiographers': radiographers,
        'radiologists': radiologists,
        'modality_filter': modality,
        'modality_counts': counts,
        'modality_pills': modality_pills,
        'when': when,
        'scope': scope,
        'today_label': timezone.localtime().strftime('%A, %d %B %Y').upper(),
        'supply_presets': SUPPLY_PRESETS,
        'accept_minutes': window.accept_minutes,
        'is_radiologist': radiologist,
        'is_radiographer': radiographer or (user.is_superuser and not radiologist),
        'show_radiographer_history': radiographer and not radiologist and not user.is_superuser,
        'active_exam': None if radiologist else radiographer_active_exam(user),
        'report_templates': _template_catalog(selected),
        'report_body': _report_body(selected),
        'show_end_form': request.GET.get('end') == '1',
        'desk': 'report' if radiologist else 'acquire',
        'show_performance': can_access_platform_controls(user),
        'exam_stats': exam_stats,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'visit_query': visit_query,
        'status_filter': status_filter,
        'list_query': _list_query(
            modality, when, 'report' if radiologist else 'acquire', date_from, date_to, visit_query, status_filter,
            scope=scope if (radiographer and not radiologist) else '',
        ),
        'awaiting_approval': _awaiting_approval(user, centre_ids),
        'can_approve_result': user_can_approve_radiology(user),
        'can_dispatch': user_can_open_dispatch(user),
        'can_modify_result': user_can_open_modify(user),
    }


def _awaiting_approval(user, centre_ids):
    if not user_can_approve_radiology(user):
        return []
    rows = RadiologyReport.objects.select_related(
        'exam__order__visit', 'exam__order__modality', 'exam__order__procedure', 'author',
    ).filter(status='FINAL').order_by('-finalized_at')
    if not user.is_superuser:
        rows = rows.filter(exam__order__visit__centre_id__in=centre_ids)
    return list(rows[:50])


def exam_bucket(order):
    try:
        report = order.exam.report
    except ObjectDoesNotExist:
        report = None
    if report is None or report.status == 'DRAFT':
        return 'PENDING'
    if report.status == 'FINAL':
        return 'REPORTED'
    if report.status == 'APPROVED':
        return 'APPROVED'
    if report.status in ('RELEASED', 'AMENDED'):
        return 'DISPATCHED'
    return 'PENDING'


def _exam_export_row(order):
    from django.utils import timezone
    procedure = order.procedure.name if order.procedure_id else order.modality.name
    return [
        order.visit.visit_id,
        order.modality.get_modality_type_display(),
        procedure,
        exam_bucket(order),
        timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'),
    ]


def _list_query(modality, when, desk, date_from, date_to, visit_query, status_filter, scope=''):
    from urllib.parse import urlencode
    values = {
        'modality': modality,
        'when': when,
        'desk': desk,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'visit': visit_query,
        'status': status_filter,
        'scope': scope if scope and scope != 'active' else '',
    }
    return urlencode({key: value for key, value in values.items() if value})


def _template_catalog(order):
    if order is None:
        return []
    rows = []
    for template in getattr(order, 'available_templates', []) or []:
        rows.append({
            'id': template.pk,
            'name': template.name,
            'body_html': template.body_html or '',
            'indication': template.indication_boilerplate or '',
            'technique': template.technique_boilerplate or '',
            'findings': template.findings_boilerplate or '',
            'impression': template.impression_boilerplate or '',
            'recommendations': template.recommendations_boilerplate or '',
        })
    return rows


def _report_body(order):
    if order is None:
        return ''
    try:
        return order.exam.report.findings or ''
    except ObjectDoesNotExist:
        return ''


# --- Personal / platform template screens ---------------------------------

def build_radiology_templates_context(request, *, platform=False):
    query = _text(request.GET, 'q')
    if platform:
        rows = RadiologyReportTemplate.objects.filter(scope='PLATFORM', owner__isnull=True)
    else:
        rows = RadiologyReportTemplate.objects.filter(scope='PERSONAL', owner=request.user)
    rows = rows.order_by('modality_type', 'name', '-version')
    if query:
        from django.db.models import Q
        rows = rows.filter(
            Q(name__icontains=query)
            | Q(modality_type__icontains=query)
            | Q(body_part__icontains=query)
            | Q(variant_name__icontains=query)
        )
    platform_published = RadiologyReportTemplate.objects.filter(
        scope='PLATFORM', status='PUBLISHED', is_active=True, owner__isnull=True,
    ).order_by('modality_type', 'name')
    return {
        'directory': paginate(request, rows, 'page'),
        'query': query,
        'modality_types': ImagingModality.MODALITY_TYPES,
        'visibility_choices': RadiologyReportTemplate.VISIBILITY,
        'platform_mode': platform,
        'platform_published': platform_published,
        'page_sizes': (25, 50, 100),
        'sensitive': platform,
    }


def build_radiology_template_builder_context(request, pk, *, platform=False):
    import json
    template = get_object_or_404(RadiologyReportTemplate, pk=pk)
    if platform:
        if template.scope != 'PLATFORM' or template.owner_id:
            raise Http404('Not a platform template.')
    else:
        if template.owner_id != request.user.pk and not request.user.is_superuser:
            raise Http404('You can only edit your own personal templates.')
    # Platform published layouts stay immutable; personal/custom templates can be updated.
    can_edit_meta = template.status == 'DRAFT' or (
        template.scope == 'PERSONAL' and template.status != 'RETIRED'
    )
    if platform and template.status == 'PUBLISHED':
        can_edit_meta = False
    can_edit_body = template.status != 'RETIRED'
    return {
        'template': template,
        'body_html_json': json.dumps(template.body_html or ''),
        'extra_fields_json': json.dumps(template.extra_fields or [], indent=2),
        'modality_types': ImagingModality.MODALITY_TYPES,
        'visibility_choices': RadiologyReportTemplate.VISIBILITY,
        'platform_mode': platform,
        'can_edit_meta': can_edit_meta,
        'can_edit_body': can_edit_body,
        'can_save': can_edit_body,
        'builder_subtitle': f'{template.get_modality_type_display()} · {template.scope.lower()} · v{template.version}',
        'sensitive': platform,
    }


def _default_body_html(modality_label, body_part=''):
    part = body_part or '—'
    return (
        f'<p><strong>EXAM:</strong> {modality_label} — {part}</p>'
        '<p><strong>CLINICAL INFORMATION:</strong> Clinical history not provided.</p>'
        '<p><strong>TECHNIQUE:</strong> </p>'
        '<p><strong>FINDINGS:</strong> </p>'
        '<p><strong>IMPRESSION:</strong> </p>'
        '<p><strong>COMPARISON:</strong> No prior studies available for comparison.</p>'
    )


def handle_radiology_templates_post(request, *, platform=False):
    action = request.POST.get('action')
    if action == 'create':
        name = _text(request.POST, 'name')
        modality_type = _text(request.POST, 'modality_type').upper()
        body_part = _text(request.POST, 'body_part')
        visibility = _text(request.POST, 'visibility').upper() or 'PUBLIC'
        if visibility not in dict(RadiologyReportTemplate.VISIBILITY):
            visibility = 'PUBLIC'
        if not name or modality_type not in dict(ImagingModality.MODALITY_TYPES):
            messages.error(request, 'Name and modality type are required.')
            return redirect('platform-radiology-templates' if platform else 'radiology-templates')
        if not body_part:
            messages.error(request, 'Body part is required.')
            return redirect('platform-radiology-templates' if platform else 'radiology-templates')
        modality_label = dict(ImagingModality.MODALITY_TYPES).get(modality_type, modality_type)
        try:
            row = RadiologyReportTemplate.objects.create(
                modality_type=modality_type,
                name=name,
                variant_name=_text(request.POST, 'variant_name'),
                body_part=body_part,
                visibility=visibility,
                version=1,
                status='DRAFT',
                scope='PLATFORM' if platform else 'PERSONAL',
                owner=None if platform else request.user,
                body_html=_default_body_html(modality_label, body_part),
                is_active=True,
            )
        except IntegrityError:
            messages.error(request, 'That template name/version already exists.')
            return redirect('platform-radiology-templates' if platform else 'radiology-templates')
        messages.success(request, f'Template {name} v1 created.')
        return redirect(
            'platform-radiology-template-builder' if platform else 'radiology-template-builder',
            pk=row.pk,
        )
    if action == 'clone_personal':
        # Radiologist clones a published platform template into a personal variant.
        src = get_object_or_404(
            RadiologyReportTemplate, pk=request.POST.get('template'),
            scope='PLATFORM', status='PUBLISHED', owner__isnull=True,
        )
        clone = clone_template_version(src, user=request.user, personal=True, name=_text(request.POST, 'name') or src.name)
        messages.success(request, f'Personal variant {clone.name} v{clone.version} created.')
        return redirect('radiology-template-builder', pk=clone.pk)
    return redirect('platform-radiology-templates' if platform else 'radiology-templates')


def _apply_template_form(template, post):
    name = _text(post, 'name') or template.name
    modality_type = _text(post, 'modality_type').upper() or template.modality_type
    if modality_type not in dict(ImagingModality.MODALITY_TYPES):
        modality_type = template.modality_type
    body_part = _text(post, 'body_part')
    if not body_part:
        raise ValidationError('Body part is required.')
    visibility = _text(post, 'visibility').upper() or template.visibility
    if visibility not in dict(RadiologyReportTemplate.VISIBILITY):
        visibility = template.visibility
    template.name = name
    template.variant_name = _text(post, 'variant_name')
    template.modality_type = modality_type
    template.body_part = body_part
    template.visibility = visibility
    if post.get('body_html') is not None:
        template.body_html = post.get('body_html') or ''
    template.is_active = post.get('is_active') == 'on'
    return template


def handle_radiology_template_builder_post(request, pk, *, platform=False):
    template = get_object_or_404(RadiologyReportTemplate, pk=pk)
    if platform and (template.scope != 'PLATFORM' or template.owner_id):
        messages.error(request, 'Not a platform template.')
        return redirect('platform-radiology-templates')
    if not platform and template.owner_id != request.user.pk and not request.user.is_superuser:
        messages.error(request, 'You can only edit your own personal templates.')
        return redirect('radiology-templates')
    action = request.POST.get('action') or request.POST.get('action_btn') or 'update'
    dest = 'platform-radiology-template-builder' if platform else 'radiology-template-builder'
    can_edit = template.status == 'DRAFT' or (template.scope == 'PERSONAL' and template.status != 'RETIRED')
    if platform and template.status == 'PUBLISHED':
        can_edit = False

    if action == 'update':
        if not can_edit:
            messages.error(request, 'Published platform templates are immutable. Save as a new variant instead.')
            return redirect(dest, pk=template.pk)
        try:
            _apply_template_form(template, request.POST)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))
            return redirect(dest, pk=template.pk)
        template.save()
        messages.success(request, 'Template updated.')
        return redirect(dest, pk=template.pk)

    if action == 'save_variant':
        if template.status == 'RETIRED':
            messages.error(request, 'Retired templates cannot spawn variants.')
            return redirect(dest, pk=template.pk)
        body_part = _text(request.POST, 'body_part') or template.body_part
        if not body_part:
            messages.error(request, 'Body part is required.')
            return redirect(dest, pk=template.pk)
        modality_type = _text(request.POST, 'modality_type').upper() or template.modality_type
        if modality_type not in dict(ImagingModality.MODALITY_TYPES):
            modality_type = template.modality_type
        visibility = _text(request.POST, 'visibility').upper() or template.visibility
        if visibility not in dict(RadiologyReportTemplate.VISIBILITY):
            visibility = template.visibility
        clone = clone_template_version(
            template,
            user=request.user,
            personal=not platform or template.scope == 'PERSONAL',
            name=_text(request.POST, 'name') or template.name,
        )
        clone.variant_name = _text(request.POST, 'variant_name') or template.variant_name
        clone.body_part = body_part
        clone.visibility = visibility
        clone.modality_type = modality_type
        clone.body_html = request.POST.get('body_html') if request.POST.get('body_html') is not None else template.body_html
        clone.is_active = request.POST.get('is_active') == 'on' if can_edit else True
        clone.save()
        messages.success(request, f'Saved as new variant template v{clone.version}.')
        return redirect(
            'radiology-template-builder' if clone.scope == 'PERSONAL' else dest,
            pk=clone.pk,
        )

    if action == 'publish':
        if template.status != 'DRAFT':
            messages.error(request, 'Only drafts can be published.')
            return redirect(dest, pk=template.pk)
        try:
            _apply_template_form(template, request.POST)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))
            return redirect(dest, pk=template.pk)
        template.is_active = True
        template.save()
        publish_template(template, request.user)
        messages.success(request, f'{template.name} v{template.version} published.')
        return redirect(dest, pk=template.pk)

    if action == 'retire':
        template.status = 'RETIRED'
        template.is_active = False
        template.save(update_fields=['status', 'is_active', 'updated_at'])
        messages.success(request, 'Template retired.')
        return redirect(dest, pk=template.pk)

    if action in ('clone', 'save'):
        clone = clone_template_version(template, user=request.user, personal=not platform)
        messages.success(request, f'Created draft v{clone.version}.')
        return redirect(dest if (platform and clone.scope == 'PLATFORM') else 'radiology-template-builder', pk=clone.pk)
    return redirect(dest, pk=template.pk)
