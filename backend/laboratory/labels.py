"""Printable sample barcode labels (Code128)."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from core.access import require_workspace
from laboratory.api import record_barcode_print, scoped_samples
from laboratory.models import Sample


def _svg_for(sample_id: str) -> bytes:
    import barcode
    from barcode.writer import SVGWriter

    return barcode.get('code128', sample_id, writer=SVGWriter()).render({
        'write_text': True,
        'module_height': 14,
        'module_width': 0.38,
        'font_size': 11,
        'text_distance': 3,
        'quiet_zone': 2.5,
    })


@login_required
@xframe_options_sameorigin
def sample_label(request, pk):
    require_workspace(request.user, 'phlebotomy', 'laboratory')
    sample = get_object_or_404(
        scoped_samples(request.user).select_related(
            'centre', 'order__test', 'order__visit__patient', 'collected_by',
        ).prefetch_related('barcode_prints__printed_by'),
        pk=pk,
    )
    barcode_svg = ''
    barcode_error = ''
    try:
        barcode_svg = _svg_for(sample.sample_id).decode('utf-8')
    except ImportError:
        barcode_error = 'Install python-barcode to render labels.'
    except Exception as exc:  # noqa: BLE001 — surface render failures on the label page
        barcode_error = str(exc)

    auto_print = request.GET.get('print') == '1'
    # Auto-print after collect records immediately; manual prints use afterprint JS → API.
    if auto_print and barcode_svg:
        record_barcode_print(sample, request, source='AUTO_PRINT')

    print_history = list(sample.barcode_prints.select_related('printed_by').order_by('-printed_at')[:15])
    return render(request, 'laboratory/sample_label.html', {
        'sample': sample,
        'barcode_svg': barcode_svg,
        'barcode_error': barcode_error,
        'auto_print': auto_print,
        'print_history': print_history,
        'record_print_url': f'/api/samples/{sample.pk}/record-print/',
    })


@login_required
def sample_barcode_svg(request, pk):
    require_workspace(request.user, 'phlebotomy', 'laboratory')
    sample = get_object_or_404(scoped_samples(request.user), pk=pk)
    try:
        svg = _svg_for(sample.sample_id)
    except ImportError:
        return HttpResponse('python-barcode is not installed.', status=503, content_type='text/plain')
    return HttpResponse(svg, content_type='image/svg+xml')
