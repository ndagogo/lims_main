"""Background generation of large management exports."""
from pathlib import Path

from django.conf import settings


def _write_export_file(export_type, user_id, params):
    from django.contrib.auth import get_user_model

    from core.audit import record_audit
    from reporting.catalogue import build_catalogue_report
    from reporting.dashboard import build_management_dashboard
    from reporting.exports import management_summary_rows, table_export

    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return {'ok': False, 'error': 'user_not_found'}

    params = params or {}
    period = params.get('period') or 'monthly'
    centre_id = params.get('centre') or None
    start = params.get('start')
    end = params.get('end')
    fmt = (params.get('format') or 'xlsx').lower()

    if export_type == 'management_summary':
        dash = build_management_dashboard(user, period=period, centre_id=centre_id, start=start, end=end)
        headers, rows = management_summary_rows(dash)
        title = 'Management dashboard summary'
        stamp = dash['start_date'].replace('-', '')
        basename = f'hfdn-management-{dash["period"]}-{stamp}-u{user_id}'
        period_label = dash['period_label']
    else:
        report = build_catalogue_report(
            user, export_type, period=period, centre_id=centre_id, start=start, end=end,
        )
        headers, rows = report['headers'], report['rows']
        title = report['report_title']
        stamp = report['start_date'].replace('-', '')
        basename = f'hfdn-{export_type}-{report["period"]}-{stamp}-u{user_id}'
        period_label = report['period_label']

    response = table_export(title, headers, rows, fmt=fmt, basename=basename, meta_lines=[period_label])
    export_dir = Path(getattr(settings, 'MEDIA_ROOT', Path(settings.BASE_DIR) / 'media')) / 'report-exports'
    export_dir.mkdir(parents=True, exist_ok=True)
    ext = 'pdf' if fmt == 'pdf' else ('xlsx' if fmt in ('xlsx', 'excel') else 'csv')
    path = export_dir / f'{basename}.{ext}'
    path.write_bytes(response.content)

    record_audit(
        actor=user,
        action='REPORT_EXPORT_COMPLETED',
        object_type='ReportExport',
        object_id=export_type[:100],
        category='BILLING',
        detail={
            'export_type': export_type,
            'format': fmt,
            'path': str(path),
            'row_count': len(rows),
            'params': params,
        },
        reason=f'Background export completed: {export_type}',
    )
    return {'ok': True, 'path': str(path), 'rows': len(rows), 'format': fmt}


try:
    from celery import shared_task

    @shared_task(name='reporting.run_large_export')
    def run_large_export(export_type, user_id, params=None):
        from core.audit import record_audit
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id).first()
        record_audit(
            actor=user,
            action='EXPORT_QUEUED',
            object_type='Export',
            object_id=str(export_type)[:100],
            detail={'export_type': export_type, 'params': params or {}},
        )
        return _write_export_file(export_type, user_id, params)

except ImportError:
    def run_large_export(export_type, user_id, params=None):
        return _write_export_file(export_type, user_id, params)


# Keep communications.tasks.run_large_export working by providing the same contract.
def queue_large_export(export_type, user_id, params=None):
    try:
        return run_large_export.delay(export_type, user_id, params)
    except Exception:
        return run_large_export(export_type, user_id, params)
