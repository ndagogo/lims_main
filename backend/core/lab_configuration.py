"""Platform-admin setup for test parameters and result formats."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect

from core.catalog import _audit, _checked, _decimal_or_none, _parse_choices, _text
from core.paging import PAGE_SIZES, paginate
from laboratory.models import ReferenceRange, ResultTemplate, ResultTemplateField, Test, TestParameter, UnitOfMeasure
from laboratory.parameter_codes import allocate_parameter_code
from laboratory.templates_svc import publish_template


def build_parameters_context(request):
    query = _text(request.GET, 'q')
    test_id = (request.GET.get('test') or '').strip()
    selected_test = Test.objects.filter(pk=test_id).first() if test_id else None
    rows = TestParameter.objects.select_related('test', 'unit_of_measure').order_by('test__name', 'display_order', 'name')
    if selected_test:
        rows = rows.filter(test=selected_test)
    if query:
        rows = rows.filter(
            Q(code__icontains=query) | Q(name__icontains=query) | Q(test__name__icontains=query) | Q(test__code__icontains=query)
        )
    editing = None
    edit_id = (request.GET.get('parameter') or '').strip()
    if edit_id:
        editing = TestParameter.objects.select_related('test', 'unit_of_measure').filter(pk=edit_id).first()
        if editing and selected_test is None:
            selected_test = editing.test
    copy_from = None
    copy_id = (request.GET.get('copy') or '').strip()
    if copy_id and editing is None:
        copy_from = TestParameter.objects.filter(pk=copy_id).first()
    units = UnitOfMeasure.objects.filter(is_active=True).order_by('name')
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'tests': Test.objects.filter(is_active=True).order_by('name'),
        'selected_test': selected_test,
        'editing': editing,
        'copy_from': copy_from,
        'units': units,
        'all_units': UnitOfMeasure.objects.order_by('name'),
        'result_types': TestParameter.RESULT_TYPES,
        'text_sizes': TestParameter.TEXT_SIZES,
        'result_positions': TestParameter.RESULT_POSITIONS,
        'preview_code': allocate_parameter_code(selected_test) if selected_test and editing is None else '',
        'copy_choices': TestParameter.objects.select_related('test').order_by('test__code', 'name')[:400],
    }


def handle_parameters_post(request):
    action = request.POST.get('action')
    try:
        if action == 'save_unit':
            return _save_unit(request)
        if action == 'deactivate_unit':
            unit = get_object_or_404(UnitOfMeasure, pk=request.POST.get('unit'))
            unit.is_active = False
            unit.save(update_fields=['is_active', 'updated_at'])
            messages.success(request, f'{unit.symbol} is inactive.')
            return redirect('catalog-parameters')
        if action == 'save_parameter':
            return _save_setup_parameter(request)
        if action == 'delete_parameter':
            param = get_object_or_404(TestParameter, pk=request.POST.get('parameter'))
            test_pk = param.test_id
            code = param.code
            param.delete()
            messages.success(request, f'Parameter {code} was removed.')
            return redirect(f'/catalog/parameters/?test={test_pk}')
        if action == 'delete_range':
            row = get_object_or_404(ReferenceRange, pk=request.POST.get('range'))
            param = row.parameter
            row.delete()
            messages.success(request, 'Reference range removed.')
            return redirect(f'/catalog/parameters/?test={param.test_id}&parameter={param.pk}')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        test = request.POST.get('test') or ''
        return redirect(f'/catalog/parameters/?test={test}' if test else 'catalog-parameters')
    messages.error(request, 'Choose a parameter action.')
    return redirect('catalog-parameters')


def _save_unit(request):
    symbol = _text(request.POST, 'symbol')
    name = _text(request.POST, 'name') or symbol
    if not symbol:
        raise ValidationError('Enter the unit symbol, for example g/dL.')
    code = _text(request.POST, 'unit_code').upper() or ''.join(ch for ch in symbol.upper() if ch.isalnum())[:20]
    if not code:
        raise ValidationError('Enter a short code for this unit.')
    if UnitOfMeasure.objects.filter(code__iexact=code).exists():
        raise ValidationError(f'Unit code {code} already exists.')
    if UnitOfMeasure.objects.filter(symbol__iexact=symbol).exists():
        raise ValidationError(f'{symbol} is already on the unit list.')
    unit = UnitOfMeasure.objects.create(code=code, name=name, symbol=symbol, is_active=True)
    _audit(request, 'UNIT_CREATED', unit, {'code': unit.code, 'symbol': unit.symbol})
    messages.success(request, f'Unit {unit.symbol} was added.')
    return redirect('catalog-parameters')


def _save_setup_parameter(request):
    test = get_object_or_404(Test, pk=request.POST.get('test'))
    name = _text(request.POST, 'param_name')
    if not name:
        raise ValidationError('Parameter display name is required.')
    result_type = _text(request.POST, 'result_type') or 'NUMBER'
    if result_type not in dict(TestParameter.RESULT_TYPES):
        raise ValidationError('Choose a result format type.')
    unit = None
    unit_symbol = ''
    if request.POST.get('unit_of_measure'):
        unit = UnitOfMeasure.objects.filter(pk=request.POST.get('unit_of_measure'), is_active=True).first()
        if unit is None:
            raise ValidationError('Choose a unit of measurement from the list.')
        unit_symbol = unit.symbol if unit.symbol != '—' else ''
    text_size = _text(request.POST, 'text_size') or 'SAME'
    if text_size not in dict(TestParameter.TEXT_SIZES):
        text_size = 'SAME'
    position = _text(request.POST, 'result_position') or 'BENEATH'
    if position not in dict(TestParameter.RESULT_POSITIONS):
        position = 'BENEATH'
    dec_raw = _text(request.POST, 'decimal_places')
    try:
        decimal_places = int(dec_raw) if dec_raw != '' else None
    except ValueError as exc:
        raise ValidationError('Number of decimals must be a whole number.') from exc
    try:
        display_order = int(request.POST.get('display_order') or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError('Sequence must be a whole number.') from exc
    choices = _parse_choices(request.POST.get('choices'))
    param_id = (request.POST.get('parameter') or '').strip()
    fields = dict(
        name=name,
        result_type=result_type,
        unit_of_measure=unit,
        unit=unit_symbol,
        text_size=text_size,
        result_position=position,
        note=_text(request.POST, 'note'),
        barcode_suffix=_text(request.POST, 'barcode_suffix'),
        method_name=_text(request.POST, 'method_name'),
        decimal_places=decimal_places,
        display_order=display_order,
        choices=choices,
        calculation=_text(request.POST, 'calculation'),
        section_code=_text(request.POST, 'section_code').upper(),
        show_in_report=_checked(request.POST, 'show_in_report'),
        parameter_in_bold=_checked(request.POST, 'parameter_in_bold'),
        is_required=_checked(request.POST, 'is_required'),
        require_comment_if_abnormal=_checked(request.POST, 'require_comment_if_abnormal'),
        critical_comment_required=_checked(request.POST, 'critical_comment_required'),
    )
    if param_id:
        param = get_object_or_404(TestParameter, pk=param_id, test=test)
        for key, value in fields.items():
            setattr(param, key, value)
        param.save()
        action = 'TEST_PARAMETER_UPDATED'
    else:
        param = TestParameter.objects.create(
            test=test, code=allocate_parameter_code(test), **fields,
        )
        action = 'TEST_PARAMETER_CREATED'
    _save_inline_range(request, param)
    _audit(request, action, param, {'test': test.code, 'parameter': param.code, 'name': param.name})
    messages.success(request, f'Parameter {param.code} — {param.name} was saved. Use this code for analyser mapping.')
    return redirect(f'/catalog/parameters/?test={test.pk}&parameter={param.pk}')


def _save_inline_range(request, param):
    low = _decimal_or_none(request.POST.get('low'), 'Low')
    high = _decimal_or_none(request.POST.get('high'), 'High')
    critical_low = _decimal_or_none(request.POST.get('critical_low'), 'Critical low')
    critical_high = _decimal_or_none(request.POST.get('critical_high'), 'Critical high')
    display = _text(request.POST, 'display_text')
    if low is None and high is None and critical_low is None and critical_high is None and not display:
        return
    row = param.reference_ranges.filter(is_active=True).order_by('-version').first()
    if row is None:
        ReferenceRange.objects.create(
            parameter=param, population_label=_text(request.POST, 'population_label') or 'Adult',
            low=low, high=high, critical_low=critical_low, critical_high=critical_high,
            display_text=display, version=1, is_active=True,
        )
        return
    row.low = low
    row.high = high
    row.critical_low = critical_low
    row.critical_high = critical_high
    if display:
        row.display_text = display
    if _text(request.POST, 'population_label'):
        row.population_label = _text(request.POST, 'population_label')
    row.save()


def build_formats_context(request):
    query = _text(request.GET, 'q')
    test_id = (request.GET.get('test') or '').strip()
    selected_test = Test.objects.select_related('service_group', 'specimen_type', 'default_template').filter(pk=test_id).first() if test_id else None
    rows = ResultTemplate.objects.select_related('test', 'test__service_group').annotate(field_count=Count('fields')).order_by('test__name', '-version')
    if query:
        rows = rows.filter(Q(format_code__icontains=query) | Q(name__icontains=query) | Q(test__name__icontains=query) | Q(test__code__icontains=query))
    template = None
    template_id = (request.GET.get('format') or '').strip()
    if template_id:
        template = ResultTemplate.objects.select_related('test').filter(pk=template_id).first()
        if template and selected_test is None:
            selected_test = template.test
    if template is None and selected_test:
        current = selected_test.default_template if selected_test.default_template_id else None
        if current and current.is_active and current.status != 'RETIRED':
            template = current
        else:
            template = (
                ResultTemplate.objects.filter(test=selected_test, status='PUBLISHED', is_active=True)
                .order_by('-version')
                .first()
            ) or current
    fields = []
    available = TestParameter.objects.none()
    if template:
        fields = list(template.fields.select_related('parameter').order_by('display_order', 'parameter__name'))
    if selected_test:
        used = {row.parameter_id for row in fields}
        available = selected_test.parameters.exclude(pk__in=used).order_by('display_order', 'name')
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'tests': Test.objects.filter(is_active=True).select_related('service_group').order_by('name'),
        'selected_test': selected_test,
        'template': template,
        'fields': fields,
        'available_parameters': available,
        'unmapped_tests': Test.objects.filter(is_active=True, default_template__isnull=True).count(),
    }


def handle_formats_post(request):
    action = request.POST.get('action')
    try:
        if action == 'save_format':
            return _save_format(request)
        if action == 'remove_field':
            field = get_object_or_404(ResultTemplateField, pk=request.POST.get('field'))
            template = field.template
            field.delete()
            messages.success(request, 'Parameter removed from this format.')
            return redirect(f'/catalog/formats/?test={template.test_id}&format={template.pk}')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        test = request.POST.get('test') or ''
        return redirect(f'/catalog/formats/?test={test}' if test else 'catalog-formats')
    messages.error(request, 'Choose a format action.')
    return redirect('catalog-formats')


def _save_format(request):
    test = get_object_or_404(Test, pk=request.POST.get('test'))
    template_id = (request.POST.get('format') or '').strip()
    template = ResultTemplate.objects.filter(pk=template_id, test=test).first() if template_id else None
    if template is None and test.default_template_id:
        template = test.default_template
    report_title = _text(request.POST, 'report_title') or test.name
    captions = {
        'report_title': report_title,
        'column_caption_1': _text(request.POST, 'column_caption_1') or 'Test',
        'column_caption_2': _text(request.POST, 'column_caption_2') or 'Result',
        'column_caption_3': _text(request.POST, 'column_caption_3') or 'Units',
        'column_caption_4': _text(request.POST, 'column_caption_4') or 'Biological Reference Interval',
        'interpretation': _text(request.POST, 'interpretation'),
        'report_header': _text(request.POST, 'report_header'),
        'report_footer': _text(request.POST, 'report_footer'),
        'is_default_male': _checked(request.POST, 'is_default_male'),
        'is_default_female': _checked(request.POST, 'is_default_female'),
    }
    if template is None:
        template = ResultTemplate.objects.create(
            test=test, name='Standard', version=1, status='DRAFT', instructions=_text(request.POST, 'interpretation'),
            **captions,
        )
    else:
        for key, value in captions.items():
            setattr(template, key, value)
        template.instructions = captions['interpretation']
        template.save()
    add_id = (request.POST.get('add_parameter') or '').strip()
    if add_id:
        parameter = get_object_or_404(TestParameter, pk=add_id, test=test)
        if not template.fields.filter(parameter=parameter).exists():
            ResultTemplateField.objects.create(
                template=template, parameter=parameter,
                display_order=int(request.POST.get('add_sequence') or (template.fields.count() + 1)),
                section_label=_text(request.POST, 'add_subtitle'),
                is_gross=_checked(request.POST, 'add_gross'),
                show_reference_range=True,
                include_in_report=parameter.show_in_report,
            )
    for field in template.fields.all():
        key = str(field.pk)
        if request.POST.get(f'remove-{key}'):
            field.delete()
            continue
        seq = request.POST.get(f'sequence-{key}')
        if seq is not None and str(seq).strip() != '':
            try:
                field.display_order = int(seq)
            except ValueError as exc:
                raise ValidationError('Sequence must be a whole number.') from exc
        field.section_label = _text(request.POST, f'subtitle-{key}')
        field.is_gross = _checked(request.POST, f'gross-{key}')
        field.include_in_report = field.parameter.show_in_report
        field.save()
    publish_template(template, request.user)
    test.default_template = template
    test.save(update_fields=['default_template', 'updated_at'])
    _audit(request, 'TEST_FORMAT_SAVED', template, {'test': test.code, 'format': template.format_code})
    messages.success(request, f'Format {template.format_code} is mapped to {test.code}.')
    return redirect(f'/catalog/formats/?test={test.pk}&format={template.pk}')
