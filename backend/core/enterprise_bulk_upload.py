"""Enterprise bulk upload workspace: templates, validate, and save catalogue data."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.access import is_platform_admin
from core.audit import record_audit
from core.bulk_upload import DATASETS, get_dataset
from core.bulk_upload.io import build_template_csv, build_template_xlsx, read_uploaded_table


SESSION_KEY = 'enterprise_bulk_upload'


def _admin_only(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not is_platform_admin(request.user):
        raise PermissionDenied('Only platform administrators can bulk-upload catalogue data.')


def _dataset_cards():
    return [
        {
            'key': ds.key,
            'label': ds.label,
            'description': ds.description,
            'headers': ds.headers,
            'required': ds.required_headers,
        }
        for ds in DATASETS.values()
    ]


@login_required
@require_http_methods(['GET', 'POST'])
def bulk_upload_hub(request):
    _admin_only(request)

    dataset_key = (request.GET.get('dataset') or request.POST.get('dataset') or 'lab_tests').strip()
    if dataset_key not in DATASETS:
        dataset_key = 'lab_tests'
    dataset = get_dataset(dataset_key)
    report = request.session.get(SESSION_KEY)

    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'download_template':
                fmt = (request.POST.get('format') or 'xlsx').lower()
                if fmt == 'csv':
                    payload = build_template_csv(dataset.headers, dataset.sample_rows)
                    response = HttpResponse(payload, content_type='text/csv; charset=utf-8')
                    response['Content-Disposition'] = f'attachment; filename="hfdn-{dataset.key}-template.csv"'
                    return response
                payload = build_template_xlsx(dataset.headers, dataset.sample_rows, sheet_title=dataset.label)
                response = HttpResponse(
                    payload,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                response['Content-Disposition'] = f'attachment; filename="hfdn-{dataset.key}-template.xlsx"'
                return response

            if action == 'validate':
                uploaded = request.FILES.get('upload_file')
                if not uploaded:
                    raise ValidationError('Choose a CSV or Excel file to analyse.')
                headers, rows = read_uploaded_table(uploaded)
                validation = dataset.validate_rows(headers, rows, actor=request.user)
                request.session[SESSION_KEY] = validation.to_session()
                request.session.modified = True
                if not validation.headers_ok:
                    messages.error(
                        request,
                        'Missing required header(s): '
                        + ', '.join(validation.missing_headers)
                        + '. Download the template and keep the header row unchanged.',
                    )
                elif validation.ready_count:
                    messages.success(
                        request,
                        f'Analysed {len(validation.outcomes)} row(s): '
                        f'{validation.ready_count} ready, {validation.duplicate_count} duplicate, '
                        f'{validation.error_count} error.',
                    )
                else:
                    messages.warning(request, 'No rows are ready to save. Fix duplicates/errors and re-validate.')
                return redirect(f'/enterprise/bulk-upload/?dataset={dataset_key}')

            if action == 'save':
                stored = request.session.get(SESSION_KEY) or {}
                if stored.get('dataset_key') != dataset_key:
                    raise ValidationError('Validate a file for this dataset before saving.')
                payloads = stored.get('payloads') or []
                if not payloads:
                    raise ValidationError('There are no validated rows ready to save.')
                result = dataset.apply_rows(payloads, actor=request.user)
                record_audit(
                    request=request, actor=request.user, action='BULK_UPLOAD_APPLIED',
                    object_type='BulkUpload', object_id=dataset_key,
                    detail={'dataset': dataset_key, 'result': result, 'rows': len(payloads)},
                )
                request.session.pop(SESSION_KEY, None)
                messages.success(
                    request,
                    f'Saved {dataset.label}: '
                    + ', '.join(f'{k}={v}' for k, v in result.items()),
                )
                return redirect(f'/enterprise/bulk-upload/?dataset={dataset_key}')

            if action == 'clear':
                request.session.pop(SESSION_KEY, None)
                messages.info(request, 'Cleared the validation preview.')
                return redirect(f'/enterprise/bulk-upload/?dataset={dataset_key}')

            raise ValidationError('Choose a bulk upload action.')
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect(f'/enterprise/bulk-upload/?dataset={dataset_key}')
        except Exception as exc:
            messages.error(request, str(exc))
            return redirect(f'/enterprise/bulk-upload/?dataset={dataset_key}')

    preview = None
    if report and report.get('dataset_key') == dataset_key:
        preview = report

    return render(request, 'enterprise/bulk_upload.html', {
        'datasets': _dataset_cards(),
        'dataset': dataset,
        'dataset_key': dataset_key,
        'preview': preview,
    })
