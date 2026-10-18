"""Staff screens for management dashboard and report catalogue."""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import is_accountant, is_platform_admin, require_workspace

from .catalogue import CATALOGUE_REPORTS, build_catalogue_report
from .dashboard import build_management_dashboard
from .detail_reports import MANAGEMENT_SECTIONS, SECTION_KEYS, build_management_detail
from .exports import log_report_export, management_summary_rows, table_export, tables_export


def _can_view_management(user):
    return bool(
        user and user.is_authenticated and (
            user.is_superuser or is_accountant(user) or is_platform_admin(user)
            or user.groups.filter(name__in=('Centre Administrator', 'Auditor')).exists()
        )
    )


def _guard(request):
    if _can_view_management(request.user):
        return None
    try:
        require_workspace(request.user, 'accounts')
    except Exception:
        messages.error(request, 'Management reporting requires accountant or platform privileges.')
        return redirect('dashboard')
    return None


@login_required
def management_dashboard(request):
    blocked = _guard(request)
    if blocked:
        return blocked
    period = request.GET.get('period') or 'monthly'
    centre_id = request.GET.get('centre') or None
    start = request.GET.get('start')
    end = request.GET.get('end')
    context = build_management_dashboard(
        request.user, period=period, centre_id=centre_id, start=start, end=end,
    )
    selected = [key for key in request.GET.getlist('section') if key in SECTION_KEYS]
    context['catalogue_reports'] = CATALOGUE_REPORTS
    context['management_sections'] = MANAGEMENT_SECTIONS
    context['selected_sections'] = selected
    context['detail_reports'] = []
    if selected:
        detail = build_management_detail(
            request.user, selected, period=period, centre_id=centre_id, start=start, end=end,
        )
        context['detail_reports'] = detail['reports']
    return render(request, 'reporting/management_dashboard.html', context)


@login_required
def report_catalogue(request):
    blocked = _guard(request)
    if blocked:
        return blocked
    report_key = request.GET.get('report') or 'revenue_centre'
    context = build_catalogue_report(
        request.user,
        report_key,
        period=request.GET.get('period') or 'monthly',
        centre_id=request.GET.get('centre') or None,
        start=request.GET.get('start'),
        end=request.GET.get('end'),
    )
    return render(request, 'reporting/catalogue.html', context)


@login_required
def report_export(request):
    blocked = _guard(request)
    if blocked:
        return blocked
    fmt = (request.GET.get('format') or 'csv').lower()
    kind = (request.GET.get('kind') or 'catalogue').strip()
    period = request.GET.get('period') or 'monthly'
    centre_id = request.GET.get('centre') or None
    start = request.GET.get('start')
    end = request.GET.get('end')
    centre_label = ''

    if kind == 'management' and request.GET.get('detail') == '1':
        selected = [key for key in request.GET.getlist('section') if key in SECTION_KEYS]
        if not selected:
            messages.error(request, 'Select at least one section to generate a detailed report.')
            params = {'period': period, 'start': start or '', 'end': end or '', 'centre': centre_id or ''}
            return redirect(f"{reverse('management-dashboard')}?{urlencode(params)}")
        detail = build_management_detail(
            request.user, selected, period=period, centre_id=centre_id, start=start, end=end,
        )
        title = 'Management detailed report'
        report_type = 'management_' + '_'.join(detail['keys'])
        include_ids = False
        period_label = detail['period_label']
        if detail.get('selected_centre'):
            centre_label = detail['selected_centre'].code
        stamp = detail['start_date'].replace('-', '')
        slug = detail['keys'][0] if len(detail['keys']) == 1 else 'detail'
        basename = f'hfdn-management-{slug}-{detail["period"]}-{stamp}'
        meta = [f'{period_label} · {detail["start_date"]} to {detail["end_date"]}']
        row_count = sum(len(report['rows']) for report in detail['reports'])
        log_report_export(
            request,
            report_type=report_type,
            fmt=fmt,
            period_label=period_label,
            centre=centre_label,
            row_count=row_count,
            include_patient_ids=include_ids,
        )
        return tables_export(
            title,
            detail['reports'],
            fmt=fmt,
            basename=basename,
            meta_lines=meta,
        )
    if kind == 'management':
        dash = build_management_dashboard(
            request.user, period=period, centre_id=centre_id, start=start, end=end,
        )
        headers, rows = management_summary_rows(dash)
        title = 'Management dashboard summary'
        report_type = 'management_summary'
        include_ids = False
        period_label = dash['period_label']
        if dash.get('selected_centre'):
            centre_label = dash['selected_centre'].code
        stamp = dash['start_date'].replace('-', '')
        basename = f'hfdn-management-{dash["period"]}-{stamp}'
        meta = [f'{period_label} · {dash["start_date"]} to {dash["end_date"]}']
    else:
        report_key = request.GET.get('report') or 'revenue_centre'
        report = build_catalogue_report(
            request.user, report_key, period=period, centre_id=centre_id, start=start, end=end,
        )
        headers, rows = report['headers'], report['rows']
        title = report['report_title']
        report_type = report['report_key']
        include_ids = report['include_patient_ids']
        period_label = report['period_label']
        if report.get('selected_centre'):
            centre_label = report['selected_centre'].code
        stamp = report['start_date'].replace('-', '')
        basename = f'hfdn-{report_type}-{report["period"]}-{stamp}'
        meta = [f'{period_label} · {report["start_date"]} to {report["end_date"]}']

    log_report_export(
        request,
        report_type=report_type,
        fmt=fmt,
        period_label=period_label,
        centre=centre_label,
        row_count=len(rows),
        include_patient_ids=include_ids,
    )
    return table_export(title, headers, rows, fmt=fmt, basename=basename, meta_lines=meta)
