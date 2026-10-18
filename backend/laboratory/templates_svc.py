"""Result template publish/clone and authentication configuration snapshots."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from laboratory.models import ResultTemplate, ResultTemplateField
from laboratory.services import matching_range


def _dec(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, 'f')
    return value


def publish_template(template: ResultTemplate, user=None) -> ResultTemplate:
    if template.status == 'PUBLISHED':
        return template
    # Only one published layout per test+name; older releases stay frozen on authenticated results.
    ResultTemplate.objects.filter(
        test=template.test, name=template.name, status='PUBLISHED',
    ).exclude(pk=template.pk).update(status='RETIRED', is_active=False)
    template.status = 'PUBLISHED'
    template.is_active = True
    template.published_at = timezone.now()
    template.published_by = user if getattr(user, 'is_authenticated', False) else None
    template.save(update_fields=['status', 'is_active', 'published_at', 'published_by', 'updated_at'])
    return template


def resolve_entry_template(test, preferred=None):
    """Prefer an explicit template, else the test default when published/active."""
    if preferred is not None:
        return preferred
    template = getattr(test, 'default_template', None)
    if template and template.is_active and template.status in ('PUBLISHED', 'DRAFT'):
        return template
    return (
        ResultTemplate.objects.filter(test=test, status='PUBLISHED', is_active=True)
        .order_by('-version')
        .first()
    )


def entry_fields_for_sample(sample, template=None):
    """Structured entry fields with server-matched reference intervals (never hardcoded)."""
    from laboratory.services import matching_range, range_display

    test = sample.order.test
    patient = sample.order.visit.patient
    template = resolve_entry_template(test, template)
    fields = []
    for parameter, field in template_parameters(template, test):
        matched = matching_range(parameter, patient)
        required = field.is_required if field and field.is_required is not None else parameter.is_required
        if parameter.result_type in ('SECTION', 'CALCULATED'):
            required = False
        fields.append({
            'parameter': parameter,
            'field': field,
            'result_type': parameter.result_type,
            'unit': parameter.unit,
            'choices': list(parameter.choices or []),
            'decimal_places': parameter.decimal_places,
            'calculation': parameter.calculation,
            'section_code': parameter.section_code,
            'section_label': field.section_label if field else '',
            'is_required': required,
            'show_reference_range': field.show_reference_range if field else True,
            'include_in_report': field.include_in_report if field else True,
            'matched_range': matched,
            'matched_range_version': matched.version if matched else None,
            'reference_display': range_display(matched, parameter.reference_range),
            'read_only': parameter.result_type in ('SECTION', 'CALCULATED'),
            'widget': _widget_for(parameter.result_type),
        })
    return template, fields


def _widget_for(result_type: str) -> str:
    return {
        'NUMBER': 'number',
        'TEXT': 'textarea',
        'CHOICE': 'select',
        'MULTI_CHOICE': 'multiselect',
        'BOOLEAN': 'select',
        'QUALITATIVE': 'select',
        'CALCULATED': 'calculated',
        'TABLE': 'number',
        'COMMENT': 'textarea',
        'SECTION': 'section',
    }.get(result_type, 'text')


@transaction.atomic
def clone_template_version(template: ResultTemplate, *, user=None, name=None) -> ResultTemplate:
    """Create the next draft version, copying ordered fields."""
    latest = (
        ResultTemplate.objects.filter(test=template.test, name=template.name)
        .order_by('-version')
        .first()
    )
    next_version = (latest.version if latest else template.version) + 1
    clone = ResultTemplate.objects.create(
        test=template.test,
        name=name or template.name,
        version=next_version,
        status='DRAFT',
        instructions=template.instructions,
        report_header=template.report_header,
        report_footer=template.report_footer,
        is_active=True,
        cloned_from=template,
    )
    for field in template.fields.select_related('parameter').order_by('display_order'):
        ResultTemplateField.objects.create(
            template=clone,
            parameter=field.parameter,
            display_order=field.display_order,
            is_required=field.is_required,
            show_reference_range=field.show_reference_range,
            include_in_report=field.include_in_report,
            section_label=field.section_label,
        )
    return clone


def template_parameters(template: ResultTemplate | None, test):
    """Ordered parameters for entry: template fields if present, else all test parameters."""
    if template is not None:
        fields = list(template.fields.select_related('parameter').order_by('display_order', 'parameter__name'))
        if fields:
            return [(f.parameter, f) for f in fields]
    return [(p, None) for p in test.parameters.all()]


def build_configuration_snapshot(result) -> dict:
    """Freeze template + parameter + reference-range versions used at authentication."""
    sample = result.sample
    test = sample.order.test
    patient = sample.order.visit.patient
    template = result.template
    if template is None and test.default_template_id:
        template = test.default_template

    parameters = []
    for parameter, field in template_parameters(template, test):
        ranges = []
        for row in parameter.reference_ranges.filter(is_active=True).order_by('version', 'sex', 'age_min_years'):
            ranges.append({
                'id': row.pk,
                'version': row.version,
                'population_label': row.population_label,
                'sex': row.sex,
                'age_min_years': _dec(row.age_min_years),
                'age_max_years': _dec(row.age_max_years),
                'low': _dec(row.low),
                'high': _dec(row.high),
                'critical_low': _dec(row.critical_low),
                'critical_high': _dec(row.critical_high),
                'display_text': row.display_text,
            })
        matched = matching_range(parameter, patient)
        value_row = result.values.filter(parameter=parameter).first()
        parameters.append({
            'parameter_id': parameter.pk,
            'code': parameter.code,
            'name': parameter.name,
            'result_type': parameter.result_type,
            'unit': parameter.unit,
            'decimal_places': parameter.decimal_places,
            'choices': list(parameter.choices or []),
            'calculation': parameter.calculation,
            'section_code': parameter.section_code,
            'legacy_reference_range': parameter.reference_range,
            'is_required': field.is_required if field and field.is_required is not None else parameter.is_required,
            'show_reference_range': field.show_reference_range if field else True,
            'include_in_report': field.include_in_report if field else True,
            'section_label': field.section_label if field else '',
            'reference_ranges': ranges,
            'matched_range_id': matched.pk if matched else None,
            'matched_range_version': matched.version if matched else None,
            'matched_display': (
                value_row.reference_display if value_row and value_row.reference_display
                else (matched.display_text if matched else parameter.reference_range)
            ),
            'value': value_row.value if value_row else '',
            'flag': value_row.flag if value_row else '',
            'comment': value_row.comment if value_row else '',
        })

    return {
        'frozen_at': timezone.now().isoformat(),
        'test': {
            'id': test.pk,
            'code': test.code,
            'name': test.name,
            'department': test.department,
            'service_group': test.service_group.name if test.service_group_id else '',
            'specimen': test.specimen,
            'tat_target_minutes': test.tat_target_minutes,
            'workflow_code': test.default_workflow_code,
        },
        'template': {
            'id': template.pk if template else None,
            'name': template.name if template else '',
            'version': template.version if template else None,
            'status': template.status if template else '',
            'instructions': template.instructions if template else '',
            'report_header': template.report_header if template else '',
            'report_footer': template.report_footer if template else '',
        } if template else None,
        'patient': {
            'sex': getattr(patient, 'sex', ''),
            'date_of_birth': patient.date_of_birth.isoformat() if getattr(patient, 'date_of_birth', None) else None,
        },
        'parameters': parameters,
        'narrative': result.narrative or '',
    }


def freeze_result_configuration(result, *, signer=None) -> dict:
    """Persist snapshot + template_version on the result (call inside authenticate)."""
    from core.report_format import apply_format_and_signature

    snapshot = build_configuration_snapshot(result)
    template = result.template
    if template is None and result.sample.order.test.default_template_id:
        template = result.sample.order.test.default_template
        result.template = template
    result.template_version = template.version if template else None
    snapshot = apply_format_and_signature(
        snapshot, centre=result.sample.centre, signer=signer, kind='lab',
    )
    result.configuration_snapshot = snapshot
    result.save(update_fields=['template', 'template_version', 'configuration_snapshot', 'updated_at'])
    return snapshot
