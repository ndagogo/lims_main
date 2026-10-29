"""Views for the sensitive Platform Controls area."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from core.platform_admin import (
    build_analyzer_mappings_context, build_analyzer_messages_context, build_analyzers_context,
    build_audit_context, build_bill_amendments_context, build_gateways_context, build_hub_context,
    build_inventory_skus_context, build_modalities_context, build_notification_outbox_context, build_notification_templates_context,
    build_pacs_failures_context, build_physician_invites_context, build_qc_materials_context,
    build_qc_runs_context, build_refunds_context, build_result_templates_context,
    build_result_template_builder_context, build_sample_codes_context, build_tat_rules_context,
    build_workflows_context, export_audit_report, handle_analyzer_mappings_post, handle_analyzers_post,
    handle_gateways_post, handle_inventory_skus_post, handle_modalities_post, handle_notification_outbox_post,
    handle_notification_templates_post, handle_pacs_failures_post, handle_physician_invites_post,
    handle_qc_materials_post, handle_qc_runs_post, handle_result_template_builder_post,
    handle_result_templates_post, handle_sample_codes_post, handle_tat_rules_post,
    handle_workflows_post, require_platform_controls,
)


def _screen(request, *, template, builder, handler=None):
    require_platform_controls(request)
    if request.method == 'POST' and handler:
        return handler(request)
    return render(request, template, builder(request))


@login_required
def platform_hub(request):
    require_platform_controls(request)
    return render(request, 'platform/hub.html', build_hub_context(request))


@login_required
def platform_audit(request):
    require_platform_controls(request)
    if request.GET.get('export'):
        return export_audit_report(request)
    return render(request, 'platform/audit.html', build_audit_context(request))


@login_required
def platform_workflows(request):
    return _screen(request, template='platform/workflows.html', builder=build_workflows_context, handler=handle_workflows_post)


@login_required
def platform_qc_materials(request):
    return _screen(request, template='platform/qc_materials.html', builder=build_qc_materials_context, handler=handle_qc_materials_post)


@login_required
def platform_qc_runs(request):
    return _screen(request, template='platform/qc_runs.html', builder=build_qc_runs_context, handler=handle_qc_runs_post)


@login_required
def platform_result_templates(request):
    return _screen(request, template='platform/result_templates.html', builder=build_result_templates_context, handler=handle_result_templates_post)


@login_required
def platform_result_template_builder(request, pk):
    require_platform_controls(request)
    if request.method == 'POST':
        return handle_result_template_builder_post(request, pk)
    return render(request, 'platform/result_template_builder.html', build_result_template_builder_context(request, pk))


@login_required
def platform_radiology_accept(request):
    from django.contrib import messages
    from django.shortcuts import redirect
    from radiology.desk import accept_window
    require_platform_controls(request)
    row = accept_window()
    if request.method == 'POST':
        raw = (request.POST.get('accept_minutes') or '').strip()
        try:
            minutes = int(raw)
        except ValueError:
            minutes = 0
        if minutes < 1 or minutes > 1440:
            messages.error(request, 'Enter a whole number of minutes from 1 to 1440.')
        else:
            row.accept_minutes = minutes
            row.save(update_fields=['accept_minutes', 'updated_at'])
            messages.success(request, f'Unaccepted studies return to the performing radiographer after {minutes} minutes.')
        return redirect('platform-radiology-accept')
    return render(request, 'platform/radiology_accept.html', {'setting': row})


@login_required
def platform_radiology_templates(request):
    from radiology.workspace import build_radiology_templates_context, handle_radiology_templates_post
    require_platform_controls(request)
    if request.method == 'POST':
        return handle_radiology_templates_post(request, platform=True)
    return render(request, 'platform/radiology_templates.html', build_radiology_templates_context(request, platform=True))


@login_required
def platform_radiology_template_builder(request, pk):
    from radiology.workspace import (
        build_radiology_template_builder_context, handle_radiology_template_builder_post,
    )
    require_platform_controls(request)
    if request.method == 'POST':
        return handle_radiology_template_builder_post(request, pk, platform=True)
    return render(
        request, 'platform/radiology_template_builder.html',
        build_radiology_template_builder_context(request, pk, platform=True),
    )


@login_required
def platform_analyzers(request):
    return _screen(request, template='platform/analyzers.html', builder=build_analyzers_context, handler=handle_analyzers_post)


@login_required
def platform_analyzer_mappings(request):
    return _screen(request, template='platform/analyzer_mappings.html', builder=build_analyzer_mappings_context, handler=handle_analyzer_mappings_post)


@login_required
def platform_analyzer_messages(request):
    return _screen(request, template='platform/analyzer_messages.html', builder=build_analyzer_messages_context)


@login_required
def platform_gateways(request):
    return _screen(request, template='platform/gateways.html', builder=build_gateways_context, handler=handle_gateways_post)


@login_required
def platform_pacs_failures(request):
    return _screen(request, template='platform/pacs_failures.html', builder=build_pacs_failures_context, handler=handle_pacs_failures_post)


@login_required
def platform_modalities(request):
    return _screen(request, template='platform/modalities.html', builder=build_modalities_context, handler=handle_modalities_post)


@login_required
def platform_sample_codes(request):
    return _screen(request, template='platform/sample_codes.html', builder=build_sample_codes_context, handler=handle_sample_codes_post)


@login_required
def platform_bill_amendments(request):
    return _screen(request, template='platform/bill_amendments.html', builder=build_bill_amendments_context)


@login_required
def platform_refunds(request):
    return _screen(request, template='platform/refunds.html', builder=build_refunds_context)


@login_required
def platform_notification_templates(request):
    return _screen(request, template='platform/notification_templates.html', builder=build_notification_templates_context, handler=handle_notification_templates_post)


@login_required
def platform_notification_outbox(request):
    return _screen(request, template='platform/notification_outbox.html', builder=build_notification_outbox_context, handler=handle_notification_outbox_post)


@login_required
def platform_physician_invites(request):
    return _screen(request, template='platform/physician_invites.html', builder=build_physician_invites_context, handler=handle_physician_invites_post)


@login_required
def platform_inventory_skus(request):
    return _screen(request, template='platform/inventory_skus.html', builder=build_inventory_skus_context, handler=handle_inventory_skus_post)


@login_required
def platform_tat_rules(request):
    return _screen(request, template='platform/tat_rules.html', builder=build_tat_rules_context, handler=handle_tat_rules_post)
