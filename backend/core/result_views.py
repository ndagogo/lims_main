"""Modified approval, result dispatch, radiology preview, and worklist statistics."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from core.dispatch_desk import (
    build_dispatch_context, compose_dispatch, dispatch_history_export_rows, retry_delivery,
)
from core.modify_desk import build_modify_context, modify_history_export_rows
from core.result_control import (
    return_laboratory_for_modification,
    return_radiology_for_modification,
    user_can_approve_radiology,
    user_can_open_dispatch,
    user_can_open_modify,
    visit_results,
)
from radiology.models import RadiologyReport


def _deny_message(exc):
    detail = getattr(exc, 'detail', None)
    if detail is not None:
        return str(detail)
    if getattr(exc, 'messages', None):
        return '; '.join(str(item) for item in exc.messages)
    return str(exc)


def _guard(request, allowed):
    if not allowed(request.user):
        raise PermissionDenied('You do not have the right to open this page.')


@login_required
def result_modify(request):
    _guard(request, user_can_open_modify)
    visit_id = (request.POST.get('visit_id') or request.GET.get('visit_id') or '').strip()
    if request.method == 'POST' and request.POST.get('action') in ('modify_radiology', 'modify_laboratory'):
        reason = request.POST.get('reason', '')
        try:
            if request.POST.get('action') == 'modify_radiology':
                report = get_object_or_404(RadiologyReport, pk=request.POST.get('report'))
                return_radiology_for_modification(request.user, report, reason, request=request)
            else:
                from laboratory.models import LabResult
                result = get_object_or_404(LabResult, pk=request.POST.get('result'))
                return_laboratory_for_modification(request.user, result, reason, request=request)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _deny_message(exc))
        else:
            messages.success(request, 'The result is back with the reporter. They can report it again.')
        return redirect(f"{request.path}?visit_id={visit_id}")
    visit = None
    reports = []
    results = []
    missing = False
    if visit_id:
        visit, reports, results = visit_results(request.user, visit_id)
        missing = visit is None
    return render(
        request,
        'results/modify.html',
        build_modify_context(
            request,
            visit_id=visit_id,
            visit=visit,
            reports=reports,
            results=results,
            missing=missing,
        ),
    )


@login_required
def modify_history_export(request):
    _guard(request, user_can_open_modify)
    from reporting.exports import tables_export
    payload = modify_history_export_rows(request)
    filters = payload['filters']
    meta = []
    if filters['q']:
        meta.append(f"Search: {filters['q']}")
    if filters['service']:
        meta.append(f"Service: {filters['service']}")
    if filters['from'] or filters['to']:
        meta.append(f"Period: {filters['from'] or '…'} to {filters['to'] or '…'}")
    fmt = (request.GET.get('fmt') or 'csv').lower()
    if fmt == 'excel':
        fmt = 'xlsx'
    return tables_export(
        'Modified approvals history',
        [{
            'title': 'Modified approvals',
            'headers': ['When', 'Who', 'Visit ID', 'Patient', 'Service', 'Summary', 'Reason'],
            'rows': payload['rows'],
        }],
        fmt=fmt,
        basename='modified-approvals-history',
        meta_lines=meta,
    )


@login_required
def result_dispatch(request):
    _guard(request, user_can_open_dispatch)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'compose_dispatch':
                outcome = compose_dispatch(request.user, request.POST, request=request)
                count = outcome['recipient_count']
                queued = len(outcome['queued'])
                messages.success(
                    request,
                    f"Result dispatched to {count} recipient{'s' if count != 1 else ''}"
                    f"{f' · {queued} message(s) queued' if queued else ''}.",
                )
                return redirect(f"{reverse('result-dispatch')}?item={outcome['item_key']}&tab=ready")
            if action == 'retry_delivery':
                note = retry_delivery(request.user, request.POST.get('outbox'), request=request)
                if note and note.status == 'SENT':
                    messages.success(request, 'Delivery retried successfully.')
                elif note and note.status == 'FAILED':
                    messages.error(request, note.last_error or 'Retry failed.')
                else:
                    messages.success(request, 'Delivery queued for retry.')
                item = (request.POST.get('item') or '').strip()
                return redirect(f"{reverse('result-dispatch')}?item={item}&tab=failed" if item else reverse('result-dispatch') + '?tab=failed')
            # Legacy single-click dispatch (kept for older bookmarks / tests)
            if action == 'dispatch_radiology':
                from core.result_control import dispatch_radiology_report
                report = get_object_or_404(
                    RadiologyReport.objects.select_related('exam__order__visit'),
                    pk=request.POST.get('report'),
                )
                before = report.status
                dispatch_radiology_report(request.user, report, request=request)
                if before in ('RELEASED', 'AMENDED'):
                    messages.success(request, 'Dispatch recorded again for this result.')
                else:
                    messages.success(request, 'Result dispatched.')
            elif action == 'dispatch_laboratory':
                from laboratory.models import LabResult
                from core.result_control import dispatch_laboratory_result
                result = get_object_or_404(
                    LabResult.objects.select_related('sample__order__visit'),
                    pk=request.POST.get('result'),
                )
                before = result.status
                dispatch_laboratory_result(request.user, result, request=request)
                if before in ('RELEASED', 'AMENDED'):
                    messages.success(request, 'Dispatch recorded again for this result.')
                else:
                    messages.success(request, 'Result dispatched.')
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _deny_message(exc))
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as APIValidation
            if isinstance(exc, APIValidation):
                messages.error(request, _deny_message(exc))
            else:
                raise
        return redirect('result-dispatch')
    return render(request, 'results/dispatch.html', build_dispatch_context(request))


@login_required
def dispatch_history_export(request):
    _guard(request, user_can_open_dispatch)
    from reporting.exports import tables_export
    payload = dispatch_history_export_rows(request)
    filters = payload['filters']
    meta = []
    if filters['q']:
        meta.append(f"Search: {filters['q']}")
    if filters['service']:
        meta.append(f"Service: {filters['service']}")
    if filters['from'] or filters['to']:
        meta.append(f"Period: {filters['from'] or '…'} to {filters['to'] or '…'}")
    fmt = (request.GET.get('fmt') or 'csv').lower()
    if fmt == 'excel':
        fmt = 'xlsx'
    return tables_export(
        'Dispatch history',
        [{
            'title': 'Dispatch history',
            'headers': ['When', 'Who', 'Visit ID', 'Patient', 'Service', 'Summary'],
            'rows': payload['rows'],
        }],
        fmt=fmt,
        basename='dispatch-history',
        meta_lines=meta,
    )


@login_required
@xframe_options_sameorigin
def radiology_report_preview(request, pk):
    """Serve the finished radiology PDF for preview (UNVERIFIED watermark until dispatch)."""
    from core.access import require_workspace
    from django.http import HttpResponse
    require_workspace(request.user, 'radiology')
    report = get_object_or_404(
        RadiologyReport.objects.select_related(
            'exam__order__visit__patient', 'exam__order__visit__centre', 'exam__order__modality',
            'exam__order__procedure', 'author',
        ),
        pk=pk,
    )
    from core.models import UserCentreAccess
    allowed = request.user.is_superuser or UserCentreAccess.objects.filter(
        user=request.user, centre=report.exam.order.visit.centre, centre__is_active=True, can_view=True,
    ).exists()
    if not allowed or report.status == 'DRAFT':
        raise Http404()
    if report.status not in ('FINAL', 'APPROVED', 'RELEASED', 'AMENDED'):
        raise Http404()
    from radiology.reports import build_radiology_report_pdf
    payload = build_radiology_report_pdf(report)
    visit_id = report.exam.order.visit.visit_id
    label = {
        'FINAL': 'unverified',
        'APPROVED': 'approved',
    }.get(report.status, 'result')
    response = HttpResponse(payload, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="radiology-{label}-{visit_id}-v{report.version}.pdf"'
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex'
    return response


@login_required
def radiology_stats_export(request):
    from core.access import require_workspace
    from reporting.exports import tables_export
    require_workspace(request.user, 'radiology')
    request._radiology_export = True
    from radiology.workspace import build_radiology_context
    payload = build_radiology_context(request)
    stats = payload['stats']
    fmt = (request.GET.get('fmt') or 'csv').lower()
    if fmt == 'excel':
        fmt = 'xlsx'
    return tables_export(
        'Radiology examinations',
        [
            {
                'title': 'Counts',
                'headers': ['Status', 'Examinations'],
                'rows': [
                    ['PENDING', stats['PENDING']],
                    ['Reported', stats['REPORTED']],
                    ['Approved', stats['APPROVED']],
                    ['Dispatched', stats['DISPATCHED']],
                ],
            },
            {
                'title': 'Examinations',
                'headers': ['Visit ID', 'Modality', 'Procedure', 'Status', 'Ordered'],
                'rows': payload['rows'],
            },
        ],
        fmt=fmt,
        basename='radiology-examinations',
    )
