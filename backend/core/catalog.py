"""Platform-admin catalog: tariffs, tests, doctors, imaging, centres, and clients."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect

from core.access import is_platform_admin
from core.models import AuditEvent, Centre, CentreBank, CentrePhone
from core.paging import PAGE_SIZES, paginate
from laboratory.collection_catalog import refresh_tests_for_container, refresh_tests_for_specimen, sync_test_collection_labels
from laboratory.models import (
    ContainerType, ReferenceRange, ResultTemplate, SampleCodeConfig, ServiceGroup, SpecimenType, StatType,
    Tariff, Test, TestParameter,
)
from laboratory.stat_catalog import clean_stat_color
from patients.models import ClientType, OrganisationClient, PatientTitle, RegistrationChannel
from radiology.models import ImagingModality, ImagingProcedure
from referrals.models import ReferralDoctor, ReferralInstitution


def platform_admin_only(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not is_platform_admin(request.user):
        raise PermissionDenied('Only a platform administrator can manage the catalog.')


def _audit(request, action, obj, detail=None, centre=None, **kwargs):
    from core.audit import record_audit
    record_audit(
        request=request,
        actor=request.user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        centre=centre,
        detail=detail or {},
        previous_values=kwargs.get('previous_values'),
        new_values=kwargs.get('new_values') or detail,
        reason=kwargs.get('reason', ''),
        department=kwargs.get('department', ''),
        patient_ref=kwargs.get('patient_ref', ''),
        visit_ref=kwargs.get('visit_ref', ''),
        order_ref=kwargs.get('order_ref', ''),
        result_ref=kwargs.get('result_ref', ''),
    )


def _checked(post, name):
    return post.get(name) in ('on', 'true', '1', 'yes')


def _text(post, name, default=''):
    return (post.get(name) or default).strip()


def _published_workflows():
    from workflows.services import published_workflows
    return published_workflows()


def _posted_workflow_code(request):
    from workflows.services import require_published_workflow
    return require_published_workflow(request.POST.get('default_workflow_code'))


def _optional_pk(request, name):
    raw = (request.GET.get(name) or '').strip()
    return int(raw) if raw.isdigit() else None


def _decimal(post, name, default='0'):
    raw = (post.get(name) or default or '0').strip().replace(',', '')
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f'{name.replace("_", " ").title()} must be a number.') from exc


def _active_filter(queryset, status):
    if status == 'active':
        return queryset.filter(is_active=True)
    if status == 'inactive':
        return queryset.filter(is_active=False)
    return queryset


def _try_delete(request, obj, redirect_name, label):
    try:
        pk = str(obj.pk)
        object_type = obj.__class__.__name__
        obj.delete()
    except ProtectedError:
        messages.error(request, f'{label} is still in use and cannot be deleted. Deactivate it instead.')
        return redirect(redirect_name)
    AuditEvent.objects.create(
        actor=request.user, action=f'{object_type.upper()}_DELETED', object_type=object_type,
        object_id=pk, path=request.path, method='POST', detail={'label': label},
    )
    messages.success(request, f'{label} was deleted.')
    return redirect(redirect_name)


# --- Hub -----------------------------------------------------------------

def build_hub_context(request):
    counts = {
        'tariffs': Tariff.objects.count(),
        'tests': Test.objects.count(),
        'parameters': TestParameter.objects.count(),
        'formats': ResultTemplate.objects.count(),
        'service_groups': ServiceGroup.objects.count(),
        'doctors': ReferralDoctor.objects.count(),
        'procedures': ImagingProcedure.objects.count(),
        'modalities': ImagingModality.objects.count(),
        'centres': Centre.objects.count(),
        'clients': OrganisationClient.objects.count(),
        'channels': RegistrationChannel.objects.count(),
        'client_types': ClientType.objects.count(),
        'patient_titles': PatientTitle.objects.count(),
    }
    entities = (
        ('Bulk upload', 'enterprise-bulk-upload', 'tests', 'bi-cloud-upload', 'CSV/Excel templates for tests, doctors, clients, tariffs'),
        ('Tariff master', 'enterprise-tariffs', 'tariffs', 'bi-tags', 'Create, clone, upload, and price tariffs'),
        ('Clients master', 'enterprise-clients', 'clients', 'bi-briefcase', 'Credit / on-payment clients, wallets, receipts'),
        ('Channels', 'enterprise-channels', 'channels', 'bi-signpost-2', 'Self, doctors, credit & on-payment channel tariffs'),
        ('Client types', 'enterprise-client-types', 'client_types', 'bi-diagram-2', 'Credit, On Payment, Default, and custom types'),
        ('Patient titles', 'enterprise-patient-titles', 'patient_titles', 'bi-person-vcard', 'Mr, Mrs, Chief, Alhaji, and country-specific honorifics'),
        ('Laboratory tests', 'catalog-tests', 'tests', 'bi-droplet', 'Code, specimen, workflow, TAT, and outsourcing'),
        ('Test parameters', 'catalog-parameters', 'parameters', 'bi-sliders', 'Units, format types, decimals, and analyser codes'),
        ('Test formats', 'catalog-formats', 'formats', 'bi-layout-text-window', 'How each test is arranged on the patient result'),
        ('Service groups', 'catalog-service-groups', 'service_groups', 'bi-diagram-3', 'Sample ID group digits'),
        ('Referral business', 'enterprise-referrals', 'doctors', 'bi-people', 'Commission due, visits, payouts, and exports'),
        ('Referral doctors', 'catalog-doctors', 'doctors', 'bi-person-badge', 'Doctor master data and institutions'),
        ('Imaging', 'catalog-imaging', 'procedures', 'bi-radioactive', 'Procedures and centre modalities'),
        ('Centres', 'catalog-centres', 'centres', 'bi-building', 'Sites and sample digits'),
    )
    return {
        'counts': counts,
        'entities': [
            (label, url_name, counts[count_key], icon, blurb)
            for label, url_name, count_key, icon, blurb in entities
        ],
    }


# --- Laboratory tests ----------------------------------------------------

def build_tests_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Test.objects.select_related('service_group', 'specimen_type').annotate(param_count=Count('parameters')).order_by('name')
    if query:
        rows = rows.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(department__icontains=query))
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('test')
    if selected_id:
        selected = (
            Test.objects.select_related('service_group', 'default_template', 'specimen_type')
            .prefetch_related('parameters__reference_ranges', 'result_templates', 'container_types')
            .filter(pk=selected_id)
            .first()
        )
    selected_container_ids = set(selected.container_types.values_list('pk', flat=True)) if selected else set()
    specimen_choices = SpecimenType.objects.filter(is_active=True)
    container_choices = ContainerType.objects.filter(is_active=True)
    if selected and selected.specimen_type_id and not selected.specimen_type.is_active:
        specimen_choices = SpecimenType.objects.filter(Q(is_active=True) | Q(pk=selected.specimen_type_id))
    if selected_container_ids:
        container_choices = ContainerType.objects.filter(Q(is_active=True) | Q(pk__in=selected_container_ids))
    editing_specimen = SpecimenType.objects.filter(pk=_optional_pk(request, 'specimen') or 0).first()
    editing_container = ContainerType.objects.filter(pk=_optional_pk(request, 'container') or 0).first()
    editing_stat = StatType.objects.filter(pk=_optional_pk(request, 'stat_type') or 0).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
        'service_groups': ServiceGroup.objects.filter(is_active=True).order_by('name', 'code'),
        'specimen_choices': specimen_choices.order_by('name'),
        'container_choices': container_choices.order_by('name'),
        'selected_container_ids': selected_container_ids,
        'specimen_types': SpecimenType.objects.annotate(test_count=Count('tests')).order_by('name'),
        'container_types': ContainerType.objects.annotate(test_count=Count('tests')).order_by('name'),
        'editing_specimen': editing_specimen,
        'editing_container': editing_container,
        'stat_types': StatType.objects.annotate(
            lab_count=Count('lab_orders', distinct=True),
            imaging_count=Count('imaging_orders', distinct=True),
        ).order_by('sort_order', 'name'),
        'editing_stat': editing_stat,
        'result_types': TestParameter.RESULT_TYPES,
        'sex_choices': ReferenceRange.SEX,
        'templates': (
            ResultTemplate.objects.filter(test=selected).order_by('-version', 'name')
            if selected else ResultTemplate.objects.none()
        ),
        'published_workflows': _published_workflows(),
        'existing_test_names': list(Test.objects.values_list('name', flat=True)),
    }


def handle_tests_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_test(request, None)
        if action in ('create_specimen', 'update_specimen', 'delete_specimen', 'create_container', 'update_container', 'delete_container'):
            return _handle_collection_type(request, action)
        if action in ('create_stat', 'update_stat', 'delete_stat'):
            return _handle_stat_type(request, action)
        test = get_object_or_404(Test, pk=request.POST.get('test'))
        if action == 'update':
            return _save_test(request, test)
        if action == 'deactivate':
            test.is_active = False
            test.save(update_fields=['is_active'])
            _audit(request, 'TEST_DEACTIVATED', test, {'code': test.code})
            messages.success(request, f'{test.code} is inactive.')
            return redirect('catalog-tests')
        if action == 'activate':
            test.is_active = True
            test.save(update_fields=['is_active'])
            _audit(request, 'TEST_ACTIVATED', test, {'code': test.code})
            messages.success(request, f'{test.code} is active again.')
            return redirect(f'/catalog/tests/?test={test.pk}')
        if action == 'delete':
            return _try_delete(request, test, 'catalog-tests', test.code)
        if action == 'save_parameter':
            return _save_parameter(request, test)
        if action == 'delete_parameter':
            param = get_object_or_404(TestParameter, pk=request.POST.get('parameter'), test=test)
            code = param.code
            param.delete()
            _audit(request, 'TEST_PARAMETER_DELETED', test, {'parameter': code})
            messages.success(request, f'Parameter {code} was removed.')
            return redirect(f'/catalog/tests/?test={test.pk}')
        if action == 'save_range':
            return _save_reference_range(request, test)
        if action == 'delete_range':
            param = get_object_or_404(TestParameter, pk=request.POST.get('parameter'), test=test)
            row = get_object_or_404(ReferenceRange, pk=request.POST.get('range'), parameter=param)
            row.delete()
            messages.success(request, 'Reference range removed.')
            return redirect(f'/catalog/tests/?test={test.pk}')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('test')
        return redirect(f'/catalog/tests/?test={target}' if target else 'catalog-tests')
    messages.error(request, 'Choose a laboratory test action.')
    return redirect('catalog-tests')


def _save_test(request, test):
    from laboratory.test_names import assert_test_name_available
    name = assert_test_name_available(
        _text(request.POST, 'name'),
        exclude_pk=test.pk if test else None,
    )
    department = _text(request.POST, 'department') or 'PATHOLOGY'
    department_code = _text(request.POST, 'department_code') or '1'
    if len(department_code) != 1 or not department_code.isdigit():
        raise ValidationError('Department code must be a single digit.')
    group_id = (request.POST.get('service_group') or '').strip()
    if not group_id:
        raise ValidationError('Each test must be mapped to a service group.')
    group = ServiceGroup.objects.filter(pk=group_id, is_active=True).first()
    if group is None:
        raise ValidationError('Choose an active service group for this test.')
    collection_requirements = _text(request.POST, 'collection_requirements')
    sample_volume = _text(request.POST, 'sample_volume')
    handling_instructions = _text(request.POST, 'handling_instructions')
    previous_specimen_id = test.specimen_type_id if test else None
    previous_container_ids = set(test.container_types.values_list('pk', flat=True)) if test else set()
    specimen_type, containers = _posted_collection_types(
        request, previous_specimen_id=previous_specimen_id, previous_container_ids=previous_container_ids,
    )
    default_workflow_code = _posted_workflow_code(request)
    tat_raw = _text(request.POST, 'tat_target_minutes')
    try:
        tat_target_minutes = int(tat_raw) if tat_raw else None
    except ValueError as exc:
        raise ValidationError('TAT target must be a whole number of minutes.') from exc
    is_outsourceable = _checked(request.POST, 'is_outsourceable')
    comments_required = _checked(request.POST, 'comments_required')
    default_template = None
    if request.POST.get('default_template'):
        default_template = ResultTemplate.objects.filter(pk=request.POST.get('default_template'), test=test if test else None).first()
        if test is None:
            default_template = None
        elif default_template is None:
            default_template = ResultTemplate.objects.filter(pk=request.POST.get('default_template'), test_id=test.pk).first()
    is_active = _checked(request.POST, 'is_active') if request.POST.get('is_active_submitted') or test else True
    if test is None:
        test = Test(
            name=name, department=department, department_code=department_code,
            service_group=group, specimen_type=specimen_type, collection_requirements=collection_requirements,
            sample_volume=sample_volume, handling_instructions=handling_instructions,
            default_workflow_code=default_workflow_code, tat_target_minutes=tat_target_minutes,
            is_outsourceable=is_outsourceable, comments_required=comments_required, is_active=is_active,
        )
        test.save()
        test.container_types.set(containers)
        sync_test_collection_labels(test)
        from workflows.services import bind_catalog_workflow
        bind_catalog_workflow(default_workflow_code, test=test)
        _audit(request, 'TEST_CREATED', test, {'code': test.code, 'name': test.name, 'workflow': test.default_workflow_code})
        messages.success(request, f'Test {test.code} was created. Code is auto-assigned and locked.')
    else:
        test.name = name
        test.department = department
        test.department_code = department_code
        test.service_group = group
        test.specimen_type = specimen_type
        test.collection_requirements = collection_requirements
        test.sample_volume = sample_volume
        test.handling_instructions = handling_instructions
        test.default_workflow_code = default_workflow_code
        test.tat_target_minutes = tat_target_minutes
        test.is_outsourceable = is_outsourceable
        test.comments_required = comments_required
        if request.POST.get('default_template') == '':
            test.default_template = None
        elif request.POST.get('default_template'):
            tpl = ResultTemplate.objects.filter(pk=request.POST.get('default_template'), test=test).first()
            if tpl is None:
                raise ValidationError('Default template must belong to this test.')
            test.default_template = tpl
        test.is_active = _checked(request.POST, 'is_active')
        test.save()
        test.container_types.set(containers)
        sync_test_collection_labels(test)
        from workflows.services import bind_catalog_workflow
        bind_catalog_workflow(default_workflow_code, test=test)
        _audit(request, 'TEST_UPDATED', test, {'code': test.code, 'name': test.name, 'is_active': test.is_active, 'workflow': test.default_workflow_code})
        messages.success(request, f'Test {test.code} was saved.')
    return redirect(f'/catalog/tests/?test={test.pk}')


def _posted_collection_types(request, *, previous_specimen_id=None, previous_container_ids=None):
    raw_specimen = (request.POST.get('specimen_type') or '').strip()
    specimen = None
    if raw_specimen:
        specimen = SpecimenType.objects.filter(pk=raw_specimen).first()
        if specimen is None:
            raise ValidationError('Choose a specimen type from the list.')
        if not specimen.is_active and specimen.pk != previous_specimen_id:
            raise ValidationError('Choose an active specimen type.')
    raw_ids = [value for value in request.POST.getlist('container_types') if value]
    containers = list(ContainerType.objects.filter(pk__in=raw_ids))
    if len(containers) != len(set(raw_ids)):
        raise ValidationError('Choose container types from the list.')
    allowed_inactive = set(previous_container_ids or [])
    for row in containers:
        if not row.is_active and row.pk not in allowed_inactive:
            raise ValidationError('Choose active container types.')
    return specimen, containers


def _handle_collection_type(request, action):
    if action in ('create_specimen', 'update_specimen', 'delete_specimen'):
        return _save_named_catalogue(
            request, SpecimenType, 'specimen', action,
            refresh=refresh_tests_for_specimen,
        )
    return _save_named_catalogue(
        request, ContainerType, 'container', action,
        refresh=refresh_tests_for_container,
    )


def _save_named_catalogue(request, model, kind, action, *, refresh):
    label = 'Specimen type' if kind == 'specimen' else 'Container type'
    query_key = 'specimen' if kind == 'specimen' else 'container'
    if action == f'delete_{kind}':
        row = get_object_or_404(model, pk=request.POST.get(query_key))
        if row.tests.exists():
            raise ValidationError(f'{row.name} is assigned to tests. Reassign those tests before deleting it.')
        return _try_delete(request, row, 'catalog-tests', row.name)
    row = None
    if action == f'update_{kind}':
        row = get_object_or_404(model, pk=request.POST.get(query_key))
    name = _text(request.POST, 'name')
    if not name:
        raise ValidationError(f'{label} name is required.')
    if len(name) > 80:
        raise ValidationError(f'{label} name must be 80 characters or fewer.')
    clash = model.objects.filter(name__iexact=name)
    if row is not None:
        clash = clash.exclude(pk=row.pk)
    if clash.exists():
        raise ValidationError(f'A {kind} type named "{name}" already exists.')
    if row is None:
        is_active = _checked(request.POST, 'is_active') if 'is_active_submitted' in request.POST else True
        row = model.objects.create(name=name, is_active=is_active)
        _audit(request, f'{kind.upper()}_TYPE_CREATED', row, {'name': row.name})
        messages.success(request, f'{label} {row.name} was created.')
    else:
        row.name = name
        if 'is_active_submitted' in request.POST:
            row.is_active = _checked(request.POST, 'is_active')
        row.save()
        refresh(row)
        _audit(request, f'{kind.upper()}_TYPE_UPDATED', row, {'name': row.name, 'is_active': row.is_active})
        messages.success(request, f'{label} {row.name} was saved.')
    return redirect(f'/catalog/tests/?{query_key}={row.pk}')


def _handle_stat_type(request, action):
    if action == 'delete_stat':
        row = get_object_or_404(StatType, pk=request.POST.get('stat_type'))
        if row.lab_orders.exists() or row.imaging_orders.exists():
            raise ValidationError(f'{row.name} is assigned to tests. Reassign those tests before deleting it.')
        was_default = row.is_default
        name = row.name
        _audit(request, 'STAT_TYPE_DELETED', row, {'name': name})
        try:
            row.delete()
        except ProtectedError:
            raise ValidationError(f'{name} is still in use and cannot be deleted.')
        if was_default:
            nxt = StatType.objects.filter(is_active=True).order_by('sort_order', 'name').first()
            if nxt is not None:
                nxt.is_default = True
                nxt.save(update_fields=['is_default'])
        messages.success(request, f'{name} was deleted.')
        return redirect('catalog-tests')
    row = None
    if action == 'update_stat':
        row = get_object_or_404(StatType, pk=request.POST.get('stat_type'))
    name = _text(request.POST, 'name')
    if not name:
        raise ValidationError('Stat type name is required.')
    if len(name) > 40:
        raise ValidationError('Stat type name must be 40 characters or fewer.')
    clash = StatType.objects.filter(name__iexact=name)
    if row is not None:
        clash = clash.exclude(pk=row.pk)
    if clash.exists():
        raise ValidationError(f'A stat type named "{name}" already exists.')
    color = clean_stat_color(request.POST.get('color'), 'Stat colour')
    text_color = clean_stat_color(request.POST.get('text_color') or '#ffffff', 'Text colour')
    sort_raw = _text(request.POST, 'sort_order') or '0'
    try:
        sort_order = int(sort_raw)
    except ValueError as exc:
        raise ValidationError('Sort order must be a whole number.') from exc
    if sort_order < 0:
        raise ValidationError('Sort order must be a whole number.')
    is_active = _checked(request.POST, 'is_active') if 'is_active_submitted' in request.POST else True
    is_default = _checked(request.POST, 'is_default')
    if row is None and not is_default and not StatType.objects.filter(is_default=True).exists():
        is_default = True
    if row is not None and row.is_default and not is_default and not StatType.objects.exclude(pk=row.pk).filter(is_default=True).exists():
        raise ValidationError('Keep one default stat. Normal is the usual default.')
    if row is None:
        row = StatType.objects.create(
            name=name, color=color, text_color=text_color, sort_order=sort_order,
            is_active=is_active, is_default=is_default,
        )
        _audit(request, 'STAT_TYPE_CREATED', row, {'name': row.name, 'color': row.color})
        messages.success(request, f'Stat type {row.name} was created.')
    else:
        row.name = name
        row.color = color
        row.text_color = text_color
        row.sort_order = sort_order
        row.is_active = is_active
        row.is_default = is_default
        row.save()
        _audit(request, 'STAT_TYPE_UPDATED', row, {'name': row.name, 'color': row.color, 'is_default': row.is_default})
        messages.success(request, f'Stat type {row.name} was saved.')
    if row.is_default:
        StatType.objects.filter(is_default=True).exclude(pk=row.pk).update(is_default=False)
    return redirect(f'/catalog/tests/?stat_type={row.pk}#stat-types')


def _parse_choices(raw):
    text = (raw or '').strip()
    if not text:
        return []
    if text.startswith('['):
        import json
        try:
            data = json.loads(text)
            return [str(x) for x in data]
        except Exception as exc:
            raise ValidationError('Choices must be a JSON list or comma-separated values.') from exc
    return [part.strip() for part in text.split(',') if part.strip()]


def _save_parameter(request, test):
    code = _text(request.POST, 'param_code').upper()
    name = _text(request.POST, 'param_name')
    if not code or not name:
        raise ValidationError('Parameter code and name are required.')
    result_type = _text(request.POST, 'result_type') or 'NUMBER'
    if result_type not in dict(TestParameter.RESULT_TYPES):
        raise ValidationError('Choose a valid result type.')
    unit = _text(request.POST, 'unit')
    reference_range = _text(request.POST, 'reference_range')
    choices = _parse_choices(request.POST.get('choices'))
    calculation = _text(request.POST, 'calculation')
    section_code = _text(request.POST, 'section_code').upper()
    try:
        display_order = int(request.POST.get('display_order') or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError('Display order must be a whole number.') from exc
    dec_raw = _text(request.POST, 'decimal_places')
    try:
        decimal_places = int(dec_raw) if dec_raw != '' else None
    except ValueError as exc:
        raise ValidationError('Decimal places must be a whole number.') from exc
    is_required = _checked(request.POST, 'is_required') if request.POST.get('is_required_submitted') else True
    require_comment_if_abnormal = _checked(request.POST, 'require_comment_if_abnormal')
    critical_comment_required = _checked(request.POST, 'critical_comment_required')
    param_id = request.POST.get('parameter')
    fields = dict(
        code=code, name=name, result_type=result_type, unit=unit, reference_range=reference_range,
        choices=choices, calculation=calculation, section_code=section_code, display_order=display_order,
        decimal_places=decimal_places, require_comment_if_abnormal=require_comment_if_abnormal,
        critical_comment_required=critical_comment_required,
    )
    if param_id:
        param = get_object_or_404(TestParameter, pk=param_id, test=test)
        if TestParameter.objects.filter(test=test, code__iexact=code).exclude(pk=param.pk).exists():
            raise ValidationError('That parameter code already exists on this test.')
        for key, value in fields.items():
            setattr(param, key, value)
        param.is_required = _checked(request.POST, 'is_required')
        param.save()
        action = 'TEST_PARAMETER_UPDATED'
    else:
        if TestParameter.objects.filter(test=test, code__iexact=code).exists():
            raise ValidationError('That parameter code already exists on this test.')
        param = TestParameter.objects.create(test=test, is_required=is_required, **fields)
        action = 'TEST_PARAMETER_CREATED'
    _audit(request, action, param, {'test': test.code, 'parameter': param.code})
    messages.success(request, f'Parameter {param.code} was saved.')
    return redirect(f'/catalog/tests/?test={test.pk}')


def _decimal_or_none(raw, label):
    text = (raw or '').strip()
    if not text:
        return None
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f'{label} must be a number.') from exc


def _save_reference_range(request, test):
    param = get_object_or_404(TestParameter, pk=request.POST.get('parameter'), test=test)
    sex = _text(request.POST, 'sex')
    if sex not in dict(ReferenceRange.SEX):
        sex = ''
    try:
        version = int(request.POST.get('range_version') or 1)
    except (TypeError, ValueError) as exc:
        raise ValidationError('Range version must be a whole number.') from exc
    row = ReferenceRange.objects.create(
        parameter=param,
        population_label=_text(request.POST, 'population_label'),
        sex=sex,
        age_min_years=_decimal_or_none(request.POST.get('age_min_years'), 'Age min'),
        age_max_years=_decimal_or_none(request.POST.get('age_max_years'), 'Age max'),
        low=_decimal_or_none(request.POST.get('low'), 'Low'),
        high=_decimal_or_none(request.POST.get('high'), 'High'),
        critical_low=_decimal_or_none(request.POST.get('critical_low'), 'Critical low'),
        critical_high=_decimal_or_none(request.POST.get('critical_high'), 'Critical high'),
        display_text=_text(request.POST, 'display_text'),
        version=version,
        is_active=True,
    )
    _audit(request, 'REFERENCE_RANGE_CREATED', row, {'test': test.code, 'parameter': param.code})
    messages.success(request, f'Reference range added for {param.code}.')
    return redirect(f'/catalog/tests/?test={test.pk}')


# --- Service groups ------------------------------------------------------

def build_service_groups_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = ServiceGroup.objects.annotate(test_count=Count('tests')).order_by('name', 'code')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('group')
    if selected_id:
        selected = ServiceGroup.objects.filter(pk=selected_id).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
    }


def handle_service_groups_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_service_group(request, None)
        group = get_object_or_404(ServiceGroup, pk=request.POST.get('group'))
        if action == 'update':
            return _save_service_group(request, group)
        if action == 'deactivate':
            group.is_active = False
            group.save(update_fields=['is_active'])
            _audit(request, 'SERVICE_GROUP_DEACTIVATED', group, {'name': group.name, 'code': group.code})
            messages.success(request, f'{group.name} is inactive.')
            return redirect('catalog-service-groups')
        if action == 'activate':
            group.is_active = True
            group.save(update_fields=['is_active'])
            _audit(request, 'SERVICE_GROUP_ACTIVATED', group, {'name': group.name, 'code': group.code})
            messages.success(request, f'{group.name} is active again.')
            return redirect(f'/catalog/service-groups/?group={group.pk}')
        if action == 'delete':
            return _try_delete(request, group, 'catalog-service-groups', f'{group.code} — {group.name}')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('group')
        return redirect(f'/catalog/service-groups/?group={target}' if target else 'catalog-service-groups')
    messages.error(request, 'Choose a service group action.')
    return redirect('catalog-service-groups')


def _service_group_duplicate_error(name, *, exclude_pk=None):
    qs = ServiceGroup.objects.filter(name__iexact=name)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        return 'Hey, Service group already exists.'
    return None


def _save_service_group(request, group):
    name = _text(request.POST, 'name')
    code = _text(request.POST, 'code')
    if not name or not code:
        raise ValidationError('Name and a single-digit code are required.')
    if len(code) != 1 or not code.isdigit():
        raise ValidationError('Service group code must be a single digit.')
    is_active = _checked(request.POST, 'is_active') if group or request.POST.get('is_active_submitted') else True
    if group is None:
        dup = _service_group_duplicate_error(name)
        if dup:
            raise ValidationError(dup)
        group = ServiceGroup(name=name, code=code, is_active=is_active)
        group.full_clean()
        group.save()
        _audit(request, 'SERVICE_GROUP_CREATED', group, {'name': group.name, 'code': group.code})
        messages.success(request, f'Service group {group.name} was created.')
    else:
        dup = _service_group_duplicate_error(name, exclude_pk=group.pk)
        if dup:
            raise ValidationError(dup)
        group.name = name
        group.code = code
        group.is_active = _checked(request.POST, 'is_active')
        group.full_clean()
        group.save()
        # Keep denormalized codes on linked tests in sync.
        Test.objects.filter(service_group=group).exclude(service_group_code=group.code).update(service_group_code=group.code)
        _audit(request, 'SERVICE_GROUP_UPDATED', group, {'name': group.name, 'code': group.code, 'is_active': group.is_active})
        messages.success(request, f'Service group {group.name} was saved.')
    return redirect(f'/catalog/service-groups/?group={group.pk}')


# --- Referral doctors ----------------------------------------------------

def build_doctors_context(request):
    from django.contrib.auth import get_user_model
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    centre_id = request.GET.get('centre') or ''
    rows = (
        ReferralDoctor.objects.select_related('centre', 'onboarded_by')
        .prefetch_related('centres')
        .annotate(institution_count=Count('institutions', distinct=True))
        .order_by('last_name', 'first_name')
    )
    if query:
        rows = rows.filter(
            Q(doctor_code__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(facility_name__icontains=query) | Q(phone__icontains=query)
        )
    if centre_id:
        rows = rows.filter(Q(centre_id=centre_id) | Q(centres=centre_id)).distinct()
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('doctor')
    if selected_id:
        selected = (
            ReferralDoctor.objects.select_related('centre', 'onboarded_by')
            .prefetch_related('institutions', 'centres')
            .filter(pk=selected_id)
            .first()
        )
    User = get_user_model()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centre_id': centre_id,
        'selected': selected,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
        'staff': User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username'),
        'commission_types': ReferralDoctor._meta.get_field('commission_type').choices,
    }


def handle_doctors_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_doctor(request, None)
        doctor = get_object_or_404(ReferralDoctor, pk=request.POST.get('doctor'))
        if action == 'update':
            return _save_doctor(request, doctor)
        if action == 'deactivate':
            if doctor.is_default_placeholder:
                raise ValidationError('DEFAULT DOCTOR cannot be deactivated. It is required for unknown referrals.')
            doctor.is_active = False
            doctor.save(update_fields=['is_active'])
            _audit(request, 'REFERRAL_DOCTOR_DEACTIVATED', doctor, {'doctor_code': doctor.doctor_code}, centre=doctor.centre)
            messages.success(request, f'{doctor.doctor_code} is inactive.')
            return redirect('catalog-doctors')
        if action == 'activate':
            doctor.is_active = True
            doctor.save(update_fields=['is_active'])
            _audit(request, 'REFERRAL_DOCTOR_ACTIVATED', doctor, {'doctor_code': doctor.doctor_code}, centre=doctor.centre)
            messages.success(request, f'{doctor.doctor_code} is active again.')
            return redirect(f'/catalog/doctors/?doctor={doctor.pk}')
        if action == 'delete':
            if doctor.is_default_placeholder:
                raise ValidationError('DEFAULT DOCTOR cannot be deleted. It is required for unknown referrals.')
            return _try_delete(request, doctor, 'catalog-doctors', doctor.doctor_code)
        if action == 'add_institution':
            name = _text(request.POST, 'institution_name')
            if not name:
                raise ValidationError('Institution name is required.')
            institution, created = ReferralInstitution.objects.get_or_create(
                doctor=doctor, name=name, defaults={'is_active': True},
            )
            if not created and not institution.is_active:
                institution.is_active = True
                institution.save(update_fields=['is_active'])
            elif not created:
                raise ValidationError('That institution is already on this doctor.')
            _audit(request, 'REFERRAL_INSTITUTION_SAVED', institution, {'doctor': doctor.doctor_code, 'name': name}, centre=doctor.centre)
            messages.success(request, f'Institution {name} was saved.')
            return redirect(f'/catalog/doctors/?doctor={doctor.pk}')
        if action == 'deactivate_institution':
            institution = get_object_or_404(ReferralInstitution, pk=request.POST.get('institution'), doctor=doctor)
            institution.is_active = False
            institution.save(update_fields=['is_active'])
            _audit(request, 'REFERRAL_INSTITUTION_DEACTIVATED', institution, {'name': institution.name}, centre=doctor.centre)
            messages.success(request, f'{institution.name} is inactive.')
            return redirect(f'/catalog/doctors/?doctor={doctor.pk}')
        if action == 'remove_institution':
            institution = get_object_or_404(ReferralInstitution, pk=request.POST.get('institution'), doctor=doctor)
            name = institution.name
            try:
                institution.delete()
            except ProtectedError:
                institution.is_active = False
                institution.save(update_fields=['is_active'])
                messages.error(request, f'{name} is in use; it was deactivated instead.')
                return redirect(f'/catalog/doctors/?doctor={doctor.pk}')
            _audit(request, 'REFERRAL_INSTITUTION_DELETED', doctor, {'name': name}, centre=doctor.centre)
            messages.success(request, f'{name} was removed.')
            return redirect(f'/catalog/doctors/?doctor={doctor.pk}')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('doctor')
        return redirect(f'/catalog/doctors/?doctor={target}' if target else 'catalog-doctors')
    messages.error(request, 'Choose a referral doctor action.')
    return redirect('catalog-doctors')


def _save_doctor(request, doctor):
    from django.contrib.auth import get_user_model
    centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
    if doctor and doctor.is_default_placeholder and doctor.centre_id != centre.pk:
        raise ValidationError('DEFAULT DOCTOR stays on its home centre.')
    doctor_code = _text(request.POST, 'doctor_code').upper()
    first_name = _text(request.POST, 'first_name')
    last_name = _text(request.POST, 'last_name')
    if not doctor_code or not first_name or not last_name:
        raise ValidationError('Doctor code, first name, and last name are required.')
    commission_type = _text(request.POST, 'commission_type') or 'PERCENT'
    if commission_type not in dict(ReferralDoctor._meta.get_field('commission_type').choices):
        raise ValidationError('Choose a valid commission type.')
    commission_value = _decimal(request.POST, 'commission_value')
    if commission_value < 0:
        raise ValidationError('Commission value cannot be negative.')
    account_number = _text(request.POST, 'account_number').replace(' ', '')
    if account_number and not account_number.isdigit():
        raise ValidationError('Account number should contain digits only.')
    onboarded_by = None
    onboarded_id = _text(request.POST, 'onboarded_by')
    if onboarded_id:
        onboarded_by = get_user_model().objects.filter(pk=onboarded_id, is_active=True).first()
        if onboarded_by is None:
            raise ValidationError('Choose the staff member who introduced this doctor.')
    if request.POST.get('appear_on_reports_submitted'):
        appear_on_reports = _checked(request.POST, 'appear_on_reports')
    elif doctor is None:
        appear_on_reports = True
    else:
        appear_on_reports = doctor.appear_on_reports
    fields = {
        'centre': centre,
        'doctor_code': doctor_code,
        'title': _text(request.POST, 'title'),
        'first_name': first_name,
        'last_name': last_name,
        'facility_name': _text(request.POST, 'facility_name'),
        'phone': _text(request.POST, 'phone'),
        'email': _text(request.POST, 'email'),
        'commission_type': commission_type,
        'commission_value': commission_value,
        'notes': _text(request.POST, 'notes'),
        'bank_name': _text(request.POST, 'bank_name'),
        'account_name': _text(request.POST, 'account_name'),
        'account_number': account_number,
        'onboarded_by': onboarded_by,
        'appear_on_reports': False if (doctor and doctor.is_default_placeholder) else appear_on_reports,
        'is_active': _checked(request.POST, 'is_active') if doctor or request.POST.get('is_active_submitted') else True,
    }
    assigned = list(Centre.objects.filter(pk__in=request.POST.getlist('centres'), is_active=True))
    if doctor is None:
        if ReferralDoctor.objects.filter(doctor_code__iexact=doctor_code).exists():
            raise ValidationError('A doctor with that code already exists.')
        doctor = ReferralDoctor(**fields)
        doctor.save()
        doctor.assign_centres(assigned)
        _audit(request, 'REFERRAL_DOCTOR_CREATED', doctor, {'doctor_code': doctor.doctor_code}, centre=centre)
        messages.success(request, f'Doctor {doctor.doctor_code} was created.')
    else:
        if ReferralDoctor.objects.filter(doctor_code__iexact=doctor_code).exclude(pk=doctor.pk).exists():
            raise ValidationError('A doctor with that code already exists.')
        for key, value in fields.items():
            setattr(doctor, key, value)
        doctor.is_active = _checked(request.POST, 'is_active')
        doctor.save()
        doctor.assign_centres(assigned)
        _audit(request, 'REFERRAL_DOCTOR_UPDATED', doctor, {'doctor_code': doctor.doctor_code}, centre=doctor.centre)
        messages.success(request, f'Doctor {doctor.doctor_code} was saved.')
    return redirect(f'/catalog/doctors/?doctor={doctor.pk}')


# --- Imaging -------------------------------------------------------------

def build_imaging_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    centre_id = request.GET.get('centre') or ''
    procedures = ImagingProcedure.objects.annotate(price_count=Count('prices')).order_by('name')
    modalities = ImagingModality.objects.select_related('centre').order_by('centre__name', 'name')
    if query:
        procedures = procedures.filter(Q(code__icontains=query) | Q(name__icontains=query))
        modalities = modalities.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(ae_title__icontains=query))
    procedures = _active_filter(procedures, status)
    modalities = _active_filter(modalities, status)
    if centre_id:
        modalities = modalities.filter(centre_id=centre_id)
    selected_procedure = None
    if request.GET.get('procedure'):
        selected_procedure = ImagingProcedure.objects.filter(pk=request.GET.get('procedure')).first()
    selected_modality = None
    if request.GET.get('modality'):
        selected_modality = ImagingModality.objects.select_related('centre').filter(pk=request.GET.get('modality')).first()
    return {
        'procedures': paginate(request, procedures, 'ppage'),
        'modalities': paginate(request, modalities, 'mpage'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centre_id': centre_id,
        'selected_procedure': selected_procedure,
        'selected_modality': selected_modality,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
        'modality_types': ImagingModality.MODALITY_TYPES,
        'published_workflows': _published_workflows(),
    }


def handle_imaging_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create_procedure':
            return _save_procedure(request, None)
        if action == 'update_procedure':
            procedure = get_object_or_404(ImagingProcedure, pk=request.POST.get('procedure'))
            return _save_procedure(request, procedure)
        if action in ('deactivate_procedure', 'activate_procedure', 'delete_procedure'):
            procedure = get_object_or_404(ImagingProcedure, pk=request.POST.get('procedure'))
            if action == 'delete_procedure':
                return _try_delete(request, procedure, 'catalog-imaging', procedure.code)
            procedure.is_active = action == 'activate_procedure'
            procedure.save(update_fields=['is_active'])
            _audit(request, 'IMAGING_PROCEDURE_ACTIVATED' if procedure.is_active else 'IMAGING_PROCEDURE_DEACTIVATED', procedure, {'code': procedure.code})
            messages.success(request, f'{procedure.code} is {"active" if procedure.is_active else "inactive"}.')
            return redirect('catalog-imaging')
        if action == 'create_modality':
            return _save_modality(request, None)
        if action == 'update_modality':
            modality = get_object_or_404(ImagingModality, pk=request.POST.get('modality'))
            return _save_modality(request, modality)
        if action in ('deactivate_modality', 'activate_modality', 'delete_modality'):
            modality = get_object_or_404(ImagingModality, pk=request.POST.get('modality'))
            if action == 'delete_modality':
                return _try_delete(request, modality, 'catalog-imaging', modality.code)
            modality.is_active = action == 'activate_modality'
            modality.save(update_fields=['is_active'])
            _audit(request, 'IMAGING_MODALITY_ACTIVATED' if modality.is_active else 'IMAGING_MODALITY_DEACTIVATED', modality, {'code': modality.code}, centre=modality.centre)
            messages.success(request, f'{modality.code} is {"active" if modality.is_active else "inactive"}.')
            return redirect('catalog-imaging')
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('catalog-imaging')
    messages.error(request, 'Choose an imaging catalog action.')
    return redirect('catalog-imaging')


def _save_procedure(request, procedure):
    code = _text(request.POST, 'code').upper()
    name = _text(request.POST, 'name')
    modality_type = _text(request.POST, 'modality_type')
    if not code or not name or not modality_type:
        raise ValidationError('Procedure code, name, and modality type are required.')
    if modality_type not in dict(ImagingModality.MODALITY_TYPES):
        raise ValidationError('Choose a valid modality type.')
    is_active = _checked(request.POST, 'is_active') if procedure or request.POST.get('is_active_submitted') else True
    workflow_code = _posted_workflow_code(request)
    if procedure is None:
        if ImagingProcedure.objects.filter(code__iexact=code).exists():
            raise ValidationError('A procedure with that code already exists.')
        procedure = ImagingProcedure(
            code=code, name=name, modality_type=modality_type, is_active=is_active,
            default_workflow_code=workflow_code,
        )
        procedure.save()
        from workflows.services import bind_catalog_workflow
        bind_catalog_workflow(workflow_code, procedure=procedure)
        _audit(request, 'IMAGING_PROCEDURE_CREATED', procedure, {'code': procedure.code, 'workflow': procedure.default_workflow_code})
        messages.success(request, f'Procedure {procedure.code} was created.')
    else:
        if ImagingProcedure.objects.filter(code__iexact=code).exclude(pk=procedure.pk).exists():
            raise ValidationError('A procedure with that code already exists.')
        procedure.code = code
        procedure.name = name
        procedure.modality_type = modality_type
        procedure.is_active = _checked(request.POST, 'is_active')
        procedure.default_workflow_code = workflow_code
        procedure.save()
        from workflows.services import bind_catalog_workflow
        bind_catalog_workflow(workflow_code, procedure=procedure)
        _audit(request, 'IMAGING_PROCEDURE_UPDATED', procedure, {'code': procedure.code, 'workflow': procedure.default_workflow_code})
        messages.success(request, f'Procedure {procedure.code} was saved.')
    return redirect(f'/catalog/imaging/?procedure={procedure.pk}')


def _save_modality(request, modality):
    centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
    code = _text(request.POST, 'code').upper()
    name = _text(request.POST, 'name')
    modality_type = _text(request.POST, 'modality_type')
    if not code or not name or not modality_type:
        raise ValidationError('Modality code, name, and type are required.')
    if modality_type not in dict(ImagingModality.MODALITY_TYPES):
        raise ValidationError('Choose a valid modality type.')
    port_raw = _text(request.POST, 'port')
    try:
        port = int(port_raw) if port_raw else None
    except (TypeError, ValueError) as exc:
        raise ValidationError('Port must be a whole number.') from exc
    host = _text(request.POST, 'host') or None
    fields = {
        'centre': centre,
        'code': code,
        'name': name,
        'modality_type': modality_type,
        'ae_title': _text(request.POST, 'ae_title'),
        'host': host,
        'port': port,
        'wado_url': _text(request.POST, 'wado_url'),
        'is_active': _checked(request.POST, 'is_active') if modality or request.POST.get('is_active_submitted') else True,
    }
    if modality is None:
        if ImagingModality.objects.filter(centre=centre, code__iexact=code).exists():
            raise ValidationError('That modality code already exists at this centre.')
        modality = ImagingModality(**fields)
        modality.save()
        _audit(request, 'IMAGING_MODALITY_CREATED', modality, {'code': modality.code}, centre=centre)
        messages.success(request, f'Modality {modality.code} was created.')
    else:
        if ImagingModality.objects.filter(centre=centre, code__iexact=code).exclude(pk=modality.pk).exists():
            raise ValidationError('That modality code already exists at this centre.')
        for key, value in fields.items():
            setattr(modality, key, value)
        modality.is_active = _checked(request.POST, 'is_active')
        modality.save()
        _audit(request, 'IMAGING_MODALITY_UPDATED', modality, {'code': modality.code}, centre=modality.centre)
        messages.success(request, f'Modality {modality.code} was saved.')
    return redirect(f'/catalog/imaging/?modality={modality.pk}')


# --- Centres -------------------------------------------------------------

def build_centres_context(request):
    from core.models import CentreReportFormat
    from core.report_format import get_report_format

    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Centre.objects.select_related('sample_code_config').prefetch_related('phone_numbers').order_by('name')
    if query:
        rows = rows.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(phone_numbers__number__icontains=query)
        ).distinct()
    rows = _active_filter(rows, status)
    selected = None
    report_format = None
    centre_phones = []
    centre_banks = []
    selected_id = request.GET.get('centre')
    if selected_id:
        selected = (
            Centre.objects.select_related('sample_code_config', 'report_format')
            .prefetch_related('phone_numbers', 'bank_accounts')
            .filter(pk=selected_id)
            .first()
        )
        if selected:
            report_format = get_report_format(selected)
            centre_phones = list(selected.phone_numbers.filter(is_active=True).order_by('sort_order', 'created_at'))
            centre_banks = list(selected.bank_accounts.filter(is_active=True).order_by('bank_name', 'account_number'))
    from core.access import can_set_result_watermark
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
        'centre_phones': centre_phones,
        'centre_banks': centre_banks,
        'report_format': report_format,
        'layout_choices': CentreReportFormat.LAYOUTS,
        'font_choices': CentreReportFormat.FONTS,
        'can_set_result_watermark': can_set_result_watermark(request.user),
    }


def handle_centres_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_centre(request, None)
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        if action == 'update':
            return _save_centre(request, centre)
        if action == 'save_report_format':
            return _save_report_format(request, centre)
        if action == 'add_bank':
            return _add_centre_bank(request, centre)
        if action == 'update_bank':
            return _update_centre_bank(request, centre)
        if action == 'deactivate_bank':
            return _deactivate_centre_bank(request, centre)
        if action == 'deactivate':
            centre.is_active = False
            centre.save(update_fields=['is_active'])
            _audit(request, 'CENTRE_DEACTIVATED', centre, {'code': centre.code}, centre=centre)
            messages.success(request, f'{centre.code} is inactive.')
            return redirect('catalog-centres')
        if action == 'activate':
            centre.is_active = True
            centre.save(update_fields=['is_active'])
            _audit(request, 'CENTRE_ACTIVATED', centre, {'code': centre.code}, centre=centre)
            messages.success(request, f'{centre.code} is active again.')
            return redirect(f'/catalog/centres/?centre={centre.pk}')
        if action == 'delete':
            return _try_delete(request, centre, 'catalog-centres', centre.code)
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('centre')
        return redirect(f'/catalog/centres/?centre={target}' if target else 'catalog-centres')
    messages.error(request, 'Choose a centre action.')
    return redirect('catalog-centres')


def _parse_centre_phone_rows(request):
    """Parse phone_number_* / phone_label_* / phone_receipt_* / phone_id_* form lists."""
    numbers = request.POST.getlist('phone_number')
    labels = request.POST.getlist('phone_label')
    receipts = set(request.POST.getlist('phone_receipt'))
    ids = request.POST.getlist('phone_id')
    rows = []
    for index, number in enumerate(numbers):
        number = (number or '').strip()
        if not number:
            continue
        label = (labels[index] if index < len(labels) else '').strip()
        phone_id = (ids[index] if index < len(ids) else '').strip()
        show = str(index) in receipts or (phone_id and phone_id in receipts)
        rows.append({
            'id': phone_id or None,
            'number': number[:40],
            'label': label[:40],
            'show_on_receipt': show,
            'sort_order': len(rows),
        })
    return rows


def _sync_centre_phones(centre, phone_rows, *, legacy_phone=''):
    keep_ids = []
    for row in phone_rows:
        phone_id = row.get('id')
        existing = None
        if phone_id:
            existing = CentrePhone.objects.filter(pk=phone_id, centre=centre).first()
        if existing:
            existing.number = row['number']
            existing.label = row['label']
            existing.show_on_receipt = row['show_on_receipt']
            existing.sort_order = row['sort_order']
            existing.is_active = True
            existing.save(update_fields=['number', 'label', 'show_on_receipt', 'sort_order', 'is_active', 'updated_at'])
            keep_ids.append(existing.pk)
        else:
            created = CentrePhone.objects.create(
                centre=centre,
                number=row['number'],
                label=row['label'],
                show_on_receipt=row['show_on_receipt'],
                sort_order=row['sort_order'],
                is_active=True,
            )
            keep_ids.append(created.pk)
    if not phone_rows and legacy_phone:
        # Preserve a single legacy number as an editable phone row.
        created = CentrePhone.objects.create(
            centre=centre,
            number=legacy_phone[:40],
            label='',
            show_on_receipt=True,
            sort_order=0,
            is_active=True,
        )
        keep_ids.append(created.pk)
        phone_rows = [{'number': legacy_phone}]
    CentrePhone.objects.filter(centre=centre).exclude(pk__in=keep_ids).update(is_active=False)
    primary = phone_rows[0]['number'] if phone_rows else ''
    if centre.phone != primary:
        centre.phone = primary
        centre.save(update_fields=['phone', 'updated_at'])
    return keep_ids


def _save_centre(request, centre):
    from core.uploads import validate_upload

    code = _text(request.POST, 'code').upper()
    name = _text(request.POST, 'name')
    if not code or not name:
        raise ValidationError('Centre code and name are required.')
    sample_digit = _text(request.POST, 'sample_digit')
    if sample_digit and (len(sample_digit) != 1 or not sample_digit.isdigit()):
        raise ValidationError('Sample digit must be a single digit.')
    logo = request.FILES.get('logo')
    if logo:
        validate_upload(logo, kinds=('jpeg', 'png'), max_bytes=2 * 1024 * 1024)
    clear_logo = _checked(request.POST, 'clear_logo')
    phone_rows = _parse_centre_phone_rows(request)
    legacy_phone = _text(request.POST, 'phone')
    if not phone_rows and legacy_phone:
        phone_rows = [{'id': None, 'number': legacy_phone[:40], 'label': '', 'show_on_receipt': True, 'sort_order': 0}]
    primary_phone = phone_rows[0]['number'] if phone_rows else ''
    fields = {
        'code': code,
        'name': name,
        'address': _text(request.POST, 'address'),
        'phone': primary_phone,
        'email': _text(request.POST, 'email'),
        'timezone': _text(request.POST, 'timezone') or 'Africa/Lagos',
        'receipt_footer': _text(request.POST, 'receipt_footer'),
        'is_active': _checked(request.POST, 'is_active') if centre or request.POST.get('is_active_submitted') else True,
    }
    with transaction.atomic():
        if centre is None:
            if Centre.objects.filter(code__iexact=code).exists():
                raise ValidationError('A centre with that code already exists.')
            centre = Centre(**fields)
            if logo:
                centre.logo = logo
            centre.save()
            from referrals.defaults import ensure_default_doctor
            ensure_default_doctor(centre)
            action = 'CENTRE_CREATED'
            messages.success(request, f'Centre {centre.code} was created.')
        else:
            if Centre.objects.filter(code__iexact=code).exclude(pk=centre.pk).exists():
                raise ValidationError('A centre with that code already exists.')
            for key, value in fields.items():
                setattr(centre, key, value)
            centre.is_active = _checked(request.POST, 'is_active')
            if clear_logo and centre.logo:
                centre.logo.delete(save=False)
                centre.logo = None
            if logo:
                centre.logo = logo
            centre.save()
            action = 'CENTRE_UPDATED'
            messages.success(request, f'Centre {centre.code} was saved.')
        _sync_centre_phones(centre, phone_rows, legacy_phone='')
        if sample_digit:
            if SampleCodeConfig.objects.filter(numeric_code=sample_digit).exclude(centre=centre).exists():
                raise ValidationError('That sample digit is already assigned to another centre.')
            config, _created = SampleCodeConfig.objects.update_or_create(
                centre=centre,
                defaults={'numeric_code': sample_digit, 'is_active': True},
            )
            _audit(request, 'SAMPLE_CODE_CONFIG_SAVED', config, {'centre': centre.code, 'digit': sample_digit}, centre=centre)
        _audit(
            request,
            action,
            centre,
            {
                'code': centre.code,
                'name': centre.name,
                'has_logo': bool(centre.logo),
                'phones': len(phone_rows),
                'receipt_phones': sum(1 for row in phone_rows if row.get('show_on_receipt')),
            },
            centre=centre,
        )
    return redirect(f'/catalog/centres/?centre={centre.pk}')


def _add_centre_bank(request, centre):
    bank_name = _text(request.POST, 'bank_name')
    account_number = _text(request.POST, 'account_number')
    account_name = _text(request.POST, 'account_name')
    if not bank_name or not account_number:
        raise ValidationError('Bank name and account number are required.')
    if CentreBank.objects.filter(
        centre=centre, bank_name__iexact=bank_name, account_number=account_number, is_active=True,
    ).exists():
        raise ValidationError('That bank account is already listed for this centre.')
    bank = CentreBank.objects.create(
        centre=centre,
        bank_name=bank_name,
        account_name=account_name,
        account_number=account_number,
        is_active=True,
    )
    _audit(
        request, 'CENTRE_BANK_ADDED', bank,
        {'bank': bank.bank_name, 'account': bank.account_number},
        centre=centre,
    )
    messages.success(request, f'{bank.display_label()} was added for {centre.code}.')
    return redirect(f'/catalog/centres/?centre={centre.pk}#centre-banks')


def _update_centre_bank(request, centre):
    bank = get_object_or_404(CentreBank, pk=request.POST.get('bank'), centre=centre)
    bank_name = _text(request.POST, 'bank_name')
    account_number = _text(request.POST, 'account_number')
    account_name = _text(request.POST, 'account_name')
    if not bank_name or not account_number:
        raise ValidationError('Bank name and account number are required.')
    if CentreBank.objects.filter(
        centre=centre, bank_name__iexact=bank_name, account_number=account_number, is_active=True,
    ).exclude(pk=bank.pk).exists():
        raise ValidationError('That bank account is already listed for this centre.')
    bank.bank_name = bank_name
    bank.account_name = account_name
    bank.account_number = account_number
    bank.save(update_fields=['bank_name', 'account_name', 'account_number', 'updated_at'])
    _audit(
        request, 'CENTRE_BANK_UPDATED', bank,
        {'bank': bank.bank_name, 'account': bank.account_number},
        centre=centre,
    )
    messages.success(request, f'Bank account for {centre.code} was updated.')
    return redirect(f'/catalog/centres/?centre={centre.pk}#centre-banks')


def _deactivate_centre_bank(request, centre):
    bank = get_object_or_404(CentreBank, pk=request.POST.get('bank'), centre=centre)
    bank.is_active = False
    bank.save(update_fields=['is_active', 'updated_at'])
    _audit(
        request, 'CENTRE_BANK_DEACTIVATED', bank,
        {'bank': bank.bank_name, 'account': bank.account_number},
        centre=centre,
    )
    messages.success(request, f'{bank.display_label()} was removed from {centre.code}.')
    return redirect(f'/catalog/centres/?centre={centre.pk}#centre-banks')


def _save_report_format(request, centre):
    from core.models import CentreReportFormat
    from core.report_format import get_report_format

    fmt = get_report_format(centre)
    layout = _text(request.POST, 'layout_style').upper() or 'CLASSIC'
    font = _text(request.POST, 'font_family') or 'Helvetica'
    if layout not in dict(CentreReportFormat.LAYOUTS):
        raise ValidationError('Choose a valid layout style.')
    if font not in dict(CentreReportFormat.FONTS):
        raise ValidationError('Choose a valid font.')
    try:
        body_font_size = int(request.POST.get('body_font_size') or 10)
        margin_mm = int(request.POST.get('margin_mm') or 18)
    except (TypeError, ValueError):
        raise ValidationError('Font size and margin must be numbers.')
    if not 8 <= body_font_size <= 14:
        raise ValidationError('Body font size must be between 8 and 14.')
    if not 10 <= margin_mm <= 30:
        raise ValidationError('Margin must be between 10 and 30 mm.')
    primary = _text(request.POST, 'primary_color') or '#102033'
    accent = _text(request.POST, 'accent_color') or '#0f766e'
    if not primary.startswith('#') or len(primary) != 7:
        raise ValidationError('Primary colour must be a #RRGGBB value.')
    if not accent.startswith('#') or len(accent) != 7:
        raise ValidationError('Accent colour must be a #RRGGBB value.')
    fmt.title_lab = _text(request.POST, 'title_lab') or 'Laboratory Report'
    fmt.title_radiology = _text(request.POST, 'title_radiology') or 'Radiology Report'
    from core.uploads import validate_upload

    fmt.header_text = _text(request.POST, 'header_text')
    fmt.footer_text = _text(request.POST, 'footer_text')
    header_upload = request.FILES.get('header_file')
    footer_upload = request.FILES.get('footer_file')
    if header_upload:
        validate_upload(header_upload, kinds=('jpeg', 'png', 'pdf'), max_bytes=4 * 1024 * 1024)
        fmt.header_file = header_upload
    elif _checked(request.POST, 'clear_header_file') and fmt.header_file:
        fmt.header_file.delete(save=False)
        fmt.header_file = None
    if footer_upload:
        validate_upload(footer_upload, kinds=('jpeg', 'png', 'pdf'), max_bytes=4 * 1024 * 1024)
        fmt.footer_file = footer_upload
    elif _checked(request.POST, 'clear_footer_file') and fmt.footer_file:
        fmt.footer_file.delete(save=False)
        fmt.footer_file = None
    fmt.show_logo = _checked(request.POST, 'show_logo')
    fmt.show_centre_address = _checked(request.POST, 'show_centre_address')
    fmt.show_centre_contact = _checked(request.POST, 'show_centre_contact')
    fmt.show_authenticator_signature = _checked(request.POST, 'show_authenticator_signature')
    fmt.signature_label = _text(request.POST, 'signature_label') or 'Authenticated by'
    from core.access import can_set_result_watermark
    if can_set_result_watermark(request.user):
        fmt.watermark_text = _text(request.POST, 'watermark_text') or 'UNVERIFIED'
    fmt.layout_style = layout
    fmt.font_family = font
    fmt.body_font_size = body_font_size
    fmt.margin_mm = margin_mm
    fmt.primary_color = primary
    fmt.accent_color = accent
    fmt.save()
    _audit(request, 'CENTRE_REPORT_FORMAT_SAVED', centre, {
        'layout': layout, 'font': font, 'title_lab': fmt.title_lab, 'title_radiology': fmt.title_radiology,
    }, centre=centre)
    messages.success(request, f'Result formatting for {centre.code} was saved.')
    return redirect(f'/catalog/centres/?centre={centre.pk}#report-format')

