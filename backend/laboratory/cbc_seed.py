"""Seed a laboratory-approved CBC catalogue + versioned result template (demo/config)."""
from decimal import Decimal

from laboratory.models import ReferenceRange, ResultTemplate, ResultTemplateField, Test, TestParameter
from laboratory.templates_svc import publish_template


def ensure_cbc_catalogue(*, publish=True):
    """Create CBC parameters, age/sex ranges, and a published Standard template if missing."""
    test = Test.objects.filter(code='CBC').first()
    if test is None:
        return None

    test.specimen = test.specimen or 'EDTA whole blood'
    test.collection_requirements = test.collection_requirements or 'Venous draw; mix gently.'
    test.sample_volume = test.sample_volume or '3 mL'
    test.container = test.container or 'EDTA (lavender)'
    test.handling_instructions = test.handling_instructions or 'Do not freeze. Analyse within 6 hours.'
    test.tat_target_minutes = test.tat_target_minutes or 120
    test.comments_required = True
    test.save()

    specs = [
        ('SECTION_CBC', 'Complete blood count', 'SECTION', '', 0, '', '', True),
        ('HGB', 'Haemoglobin', 'NUMBER', 'g/dL', 10, '', '', True),
        ('WBC', 'White blood cell count', 'NUMBER', '10^9/L', 20, '', '', True),
        ('PLT', 'Platelet count', 'NUMBER', '10^9/L', 30, '', '', True),
        ('SECTION_DIFF', 'Differential count', 'SECTION', '', 40, '', 'DIFF', False),
        ('NEUT_PCT', 'Neutrophils', 'TABLE', '%', 50, '', 'DIFF', True),
        ('LYMPH_PCT', 'Lymphocytes', 'TABLE', '%', 60, '', 'DIFF', True),
        ('MONO_PCT', 'Monocytes', 'TABLE', '%', 70, '', 'DIFF', False),
        ('NEUT_ABS', 'Absolute neutrophils', 'CALCULATED', '10^9/L', 80, 'WBC * NEUT_PCT / 100', 'DIFF', False),
        ('MORPH', 'Film morphology', 'QUALITATIVE', '', 90, '', '', False),
        ('FLAGS', 'Analyzer flags', 'MULTI_CHOICE', '', 100, '', '', False),
        ('INTERP', 'Interpretation', 'TEXT', '', 110, '', '', False),
        ('SPEC_NOTE', 'Specimen notes', 'COMMENT', '', 120, '', '', False),
    ]
    params = {}
    for code, name, result_type, unit, order, calc, section, required in specs:
        param, created = TestParameter.objects.get_or_create(
            test=test, code=code,
            defaults={
                'name': name, 'result_type': result_type, 'unit': unit,
                'display_order': order, 'calculation': calc, 'section_code': section,
                'is_required': required, 'decimal_places': 1 if result_type in ('NUMBER', 'TABLE', 'CALCULATED') else None,
            },
        )
        if not created:
            param.name = name
            param.result_type = result_type
            param.unit = unit
            param.display_order = order
            param.calculation = calc
            param.section_code = section
            param.is_required = required
            if param.decimal_places is None and result_type in ('NUMBER', 'TABLE', 'CALCULATED'):
                param.decimal_places = 1
            param.save()
        if code == 'MORPH' and not param.choices:
            param.choices = ['Normal', 'Positive', 'Negative', 'Equivocal']
            param.save(update_fields=['choices', 'updated_at'])
        if code == 'FLAGS' and not param.choices:
            param.choices = ['Blasts?', 'Left shift', 'Clumped platelets', 'Lipaemia']
            param.result_type = 'MULTI_CHOICE'
            param.save(update_fields=['choices', 'result_type', 'updated_at'])
        params[code] = param

    # Age/sex laboratory-approved ranges (not frontend-hardcoded).
    _range(params['HGB'], population='Adult male', sex='M', age_min=18, low='13.0', high='17.5', version=1)
    _range(params['HGB'], population='Adult female', sex='F', age_min=18, low='12.0', high='15.5', version=1)
    _range(params['HGB'], population='Paediatric', sex='', age_min=1, age_max=17, low='11.0', high='14.5', version=1)
    _range(params['WBC'], population='Adult', sex='', age_min=18, low='4.0', high='11.0', version=1, critical_low='2.0', critical_high='30.0')
    _range(params['PLT'], population='Adult', sex='', age_min=18, low='150', high='400', version=1, critical_low='20', critical_high='1000')
    _range(params['NEUT_PCT'], population='Adult', sex='', age_min=18, low='40', high='70', version=1)

    template, created = ResultTemplate.objects.get_or_create(
        test=test, name='Standard CBC', version=1,
        defaults={
            'status': 'DRAFT',
            'instructions': 'Enter CBC numerics, differential %, then review calculated absolute neutrophils.',
            'report_header': 'Complete Blood Count',
            'report_footer': 'Reference intervals are laboratory-approved for the matched population.',
            'is_active': True,
        },
    )
    if created or not template.fields.exists():
        template.fields.all().delete()
        for idx, code in enumerate([
            'SECTION_CBC', 'HGB', 'WBC', 'PLT',
            'SECTION_DIFF', 'NEUT_PCT', 'LYMPH_PCT', 'MONO_PCT', 'NEUT_ABS',
            'MORPH', 'FLAGS', 'INTERP', 'SPEC_NOTE',
        ]):
            ResultTemplateField.objects.create(
                template=template,
                parameter=params[code],
                display_order=idx * 10,
                section_label=params[code].name if params[code].result_type == 'SECTION' else '',
                show_reference_range=params[code].result_type in ('NUMBER', 'TABLE', 'CALCULATED'),
                include_in_report=True,
            )
    if publish and template.status != 'PUBLISHED':
        publish_template(template)
    if test.default_template_id != template.pk and template.status == 'PUBLISHED':
        test.default_template = template
        test.save(update_fields=['default_template', 'updated_at'])
    return template


def _range(parameter, *, population='', sex='', age_min=None, age_max=None, low=None, high=None,
           critical_low=None, critical_high=None, version=1):
    exists = parameter.reference_ranges.filter(
        population_label=population, sex=sex, version=version, is_active=True,
    ).exists()
    if exists:
        return
    display = ''
    if low is not None or high is not None:
        display = f'{low or "*"}–{high or "*"}'
    ReferenceRange.objects.create(
        parameter=parameter,
        population_label=population,
        sex=sex,
        age_min_years=Decimal(str(age_min)) if age_min is not None else None,
        age_max_years=Decimal(str(age_max)) if age_max is not None else None,
        low=Decimal(str(low)) if low is not None else None,
        high=Decimal(str(high)) if high is not None else None,
        critical_low=Decimal(str(critical_low)) if critical_low is not None else None,
        critical_high=Decimal(str(critical_high)) if critical_high is not None else None,
        display_text=display,
        version=version,
        is_active=True,
    )
