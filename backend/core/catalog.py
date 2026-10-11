"""Platform-admin catalog: tariffs, tests, doctors, imaging, centres, and clients."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect

from core.access import is_platform_admin
from core.models import AuditEvent, Centre
from core.paging import PAGE_SIZES, paginate
from laboratory.models import SampleCodeConfig, ServiceGroup, Tariff, Test, TestParameter, TestPrice
from patients.models import OrganisationClient
from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure
from referrals.models import ReferralDoctor, ReferralInstitution


def platform_admin_only(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not is_platform_admin(request.user):
        raise PermissionDenied('Only a platform administrator can manage the catalog.')


def _audit(request, action, obj, detail=None, centre=None):
    AuditEvent.objects.create(
        actor=request.user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        path=request.path,
        method='POST',
        centre=centre,
        detail=detail or {},
    )


def _checked(post, name):
    return post.get(name) in ('on', 'true', '1', 'yes')


def _text(post, name, default=''):
    return (post.get(name) or default).strip()


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
        'service_groups': ServiceGroup.objects.count(),
        'doctors': ReferralDoctor.objects.count(),
        'procedures': ImagingProcedure.objects.count(),
        'modalities': ImagingModality.objects.count(),
        'centres': Centre.objects.count(),
        'clients': OrganisationClient.objects.count(),
    }
    entities = (
        ('Tariffs & prices', 'catalog-tariffs', 'tariffs', 'bi-tags', 'Lab and imaging prices by tariff'),
        ('Laboratory tests', 'catalog-tests', 'tests', 'bi-droplet', 'Codes, departments, and parameters'),
        ('Service groups', 'catalog-service-groups', 'service_groups', 'bi-diagram-3', 'Sample ID group digits'),
        ('Referral doctors', 'catalog-doctors', 'doctors', 'bi-person-badge', 'Doctors and institutions'),
        ('Imaging', 'catalog-imaging', 'procedures', 'bi-radioactive', 'Procedures and centre modalities'),
        ('Centres', 'catalog-centres', 'centres', 'bi-building', 'Sites and sample digits'),
        ('Organisation clients', 'catalog-clients', 'clients', 'bi-briefcase', 'HMO and corporate accounts'),
    )
    return {
        'counts': counts,
        'entities': [
            (label, url_name, counts[count_key], icon, blurb)
            for label, url_name, count_key, icon, blurb in entities
        ],
    }


# --- Tariffs -------------------------------------------------------------

def build_tariffs_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Tariff.objects.annotate(
        lab_price_count=Count('testprice', distinct=True),
        imaging_price_count=Count('imagingprice', distinct=True),
    ).order_by('name')
    if query:
        rows = rows.filter(name__icontains=query)
    rows = _active_filter(rows, status)
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
    }


def handle_tariffs_post(request):
    action = request.POST.get('action')
    if action == 'create':
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Tariff name is required.')
            return redirect('catalog-tariffs')
        is_active = _checked(request.POST, 'is_active') if request.POST.get('is_active_submitted') else True
        try:
            tariff = Tariff.objects.create(name=name, is_active=is_active)
        except IntegrityError:
            messages.error(request, 'A tariff with that name already exists.')
            return redirect('catalog-tariffs')
        _audit(request, 'TARIFF_CREATED', tariff, {'name': tariff.name})
        messages.success(request, f'Tariff {tariff.name} was created.')
        return redirect('catalog-tariff-detail', pk=tariff.pk)
    tariff = get_object_or_404(Tariff, pk=request.POST.get('tariff'))
    if action == 'update':
        name = _text(request.POST, 'name')
        if not name:
            messages.error(request, 'Tariff name is required.')
            return redirect('catalog-tariff-detail', pk=tariff.pk)
        tariff.name = name
        tariff.is_active = _checked(request.POST, 'is_active')
        try:
            tariff.save()
        except IntegrityError:
            messages.error(request, 'A tariff with that name already exists.')
            return redirect('catalog-tariff-detail', pk=tariff.pk)
        _audit(request, 'TARIFF_UPDATED', tariff, {'name': tariff.name, 'is_active': tariff.is_active})
        messages.success(request, f'Tariff {tariff.name} was saved.')
        return redirect('catalog-tariff-detail', pk=tariff.pk)
    if action == 'deactivate':
        tariff.is_active = False
        tariff.save(update_fields=['is_active'])
        _audit(request, 'TARIFF_DEACTIVATED', tariff, {'name': tariff.name})
        messages.success(request, f'Tariff {tariff.name} is inactive.')
        return redirect('catalog-tariffs')
    if action == 'activate':
        tariff.is_active = True
        tariff.save(update_fields=['is_active'])
        _audit(request, 'TARIFF_ACTIVATED', tariff, {'name': tariff.name})
        messages.success(request, f'Tariff {tariff.name} is active again.')
        return redirect('catalog-tariffs')
    if action == 'delete':
        return _try_delete(request, tariff, 'catalog-tariffs', tariff.name)
    messages.error(request, 'Choose a tariff action.')
    return redirect('catalog-tariffs')


def build_tariff_detail_context(request, pk):
    tariff = get_object_or_404(Tariff, pk=pk)
    lab_prices = TestPrice.objects.filter(tariff=tariff).select_related('test').order_by('test__name')
    imaging_prices = ImagingPrice.objects.filter(tariff=tariff).select_related('procedure').order_by('procedure__name')
    priced_tests = lab_prices.values_list('test_id', flat=True)
    priced_procedures = imaging_prices.values_list('procedure_id', flat=True)
    return {
        'tariff': tariff,
        'lab_prices': paginate(request, lab_prices, 'lpage'),
        'imaging_prices': paginate(request, imaging_prices, 'ipage'),
        'page_sizes': PAGE_SIZES,
        'available_tests': Test.objects.filter(is_active=True).exclude(pk__in=priced_tests).order_by('name'),
        'available_procedures': ImagingProcedure.objects.filter(is_active=True).exclude(pk__in=priced_procedures).order_by('name'),
    }


def handle_tariff_detail_post(request, pk):
    tariff = get_object_or_404(Tariff, pk=pk)
    action = request.POST.get('action')
    try:
        if action == 'update_tariff':
            name = _text(request.POST, 'name')
            if not name:
                messages.error(request, 'Tariff name is required.')
                return redirect('catalog-tariff-detail', pk=tariff.pk)
            tariff.name = name
            tariff.is_active = _checked(request.POST, 'is_active')
            try:
                tariff.save()
            except IntegrityError:
                messages.error(request, 'A tariff with that name already exists.')
                return redirect('catalog-tariff-detail', pk=tariff.pk)
            _audit(request, 'TARIFF_UPDATED', tariff, {'name': tariff.name, 'is_active': tariff.is_active})
            messages.success(request, f'Tariff {tariff.name} was saved.')
            return redirect('catalog-tariff-detail', pk=tariff.pk)
        if action == 'save_lab_price':
            return _save_lab_price(request, tariff)
        if action == 'delete_lab_price':
            price = get_object_or_404(TestPrice, pk=request.POST.get('price'), tariff=tariff)
            label = f'{price.test.code} @ {tariff.name}'
            price.delete()
            _audit(request, 'TEST_PRICE_DELETED', tariff, {'test': label})
            messages.success(request, f'Lab price for {label} was removed.')
            return redirect('catalog-tariff-detail', pk=tariff.pk)
        if action == 'save_imaging_price':
            return _save_imaging_price(request, tariff)
        if action == 'delete_imaging_price':
            price = get_object_or_404(ImagingPrice, pk=request.POST.get('price'), tariff=tariff)
            label = f'{price.procedure.code} @ {tariff.name}'
            price.delete()
            _audit(request, 'IMAGING_PRICE_DELETED', tariff, {'procedure': label})
            messages.success(request, f'Imaging price for {label} was removed.')
            return redirect('catalog-tariff-detail', pk=tariff.pk)
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('catalog-tariff-detail', pk=tariff.pk)
    messages.error(request, 'Choose a tariff price action.')
    return redirect('catalog-tariff-detail', pk=tariff.pk)


def _save_lab_price(request, tariff):
    test = get_object_or_404(Test, pk=request.POST.get('test'))
    price_value = _decimal(request.POST, 'price')
    outsource = _decimal(request.POST, 'outsource_price', '0')
    price_id = request.POST.get('price_id')
    if price_id:
        row = get_object_or_404(TestPrice, pk=price_id, tariff=tariff)
        row.price = price_value
        row.outsource_price = outsource
        row.save()
        action = 'TEST_PRICE_UPDATED'
    else:
        row, created = TestPrice.objects.update_or_create(
            tariff=tariff, test=test,
            defaults={'price': price_value, 'outsource_price': outsource},
        )
        action = 'TEST_PRICE_CREATED' if created else 'TEST_PRICE_UPDATED'
    _audit(request, action, row, {'tariff': tariff.name, 'test': test.code, 'price': str(price_value)})
    messages.success(request, f'Lab price for {test.code} was saved.')
    return redirect('catalog-tariff-detail', pk=tariff.pk)


def _save_imaging_price(request, tariff):
    procedure = get_object_or_404(ImagingProcedure, pk=request.POST.get('procedure'))
    price_value = _decimal(request.POST, 'price')
    price_id = request.POST.get('price_id')
    if price_id:
        row = get_object_or_404(ImagingPrice, pk=price_id, tariff=tariff)
        row.price = price_value
        row.save()
        action = 'IMAGING_PRICE_UPDATED'
    else:
        row, created = ImagingPrice.objects.update_or_create(
            tariff=tariff, procedure=procedure, defaults={'price': price_value},
        )
        action = 'IMAGING_PRICE_CREATED' if created else 'IMAGING_PRICE_UPDATED'
    _audit(request, action, row, {'tariff': tariff.name, 'procedure': procedure.code, 'price': str(price_value)})
    messages.success(request, f'Imaging price for {procedure.code} was saved.')
    return redirect('catalog-tariff-detail', pk=tariff.pk)


# --- Laboratory tests ----------------------------------------------------

def build_tests_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Test.objects.select_related('service_group').annotate(param_count=Count('parameters')).order_by('name')
    if query:
        rows = rows.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(department__icontains=query))
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('test')
    if selected_id:
        selected = Test.objects.select_related('service_group').prefetch_related('parameters').filter(pk=selected_id).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
        'service_groups': ServiceGroup.objects.filter(is_active=True).order_by('name', 'code'),
        'result_types': TestParameter.RESULT_TYPES,
    }


def handle_tests_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_test(request, None)
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
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('test')
        return redirect(f'/catalog/tests/?test={target}' if target else 'catalog-tests')
    messages.error(request, 'Choose a laboratory test action.')
    return redirect('catalog-tests')


def _save_test(request, test):
    code = _text(request.POST, 'code').upper()
    name = _text(request.POST, 'name')
    if not code or not name:
        raise ValidationError('Test code and name are required.')
    department = _text(request.POST, 'department') or 'PATHOLOGY'
    department_code = _text(request.POST, 'department_code') or '1'
    if len(department_code) != 1 or not department_code.isdigit():
        raise ValidationError('Department code must be a single digit.')
    group_id = request.POST.get('service_group') or None
    group = ServiceGroup.objects.filter(pk=group_id, is_active=True).first() if group_id else None
    specimen = _text(request.POST, 'specimen')
    is_active = _checked(request.POST, 'is_active') if request.POST.get('is_active_submitted') or test else True
    if test is None:
        if Test.objects.filter(code__iexact=code).exists():
            raise ValidationError('A test with that code already exists.')
        test = Test(
            code=code, name=name, department=department, department_code=department_code,
            service_group=group, specimen=specimen, is_active=is_active,
        )
        test.save()
        _audit(request, 'TEST_CREATED', test, {'code': test.code, 'name': test.name})
        messages.success(request, f'Test {test.code} was created.')
    else:
        if Test.objects.filter(code__iexact=code).exclude(pk=test.pk).exists():
            raise ValidationError('A test with that code already exists.')
        test.code = code
        test.name = name
        test.department = department
        test.department_code = department_code
        test.service_group = group
        test.specimen = specimen
        test.is_active = _checked(request.POST, 'is_active')
        test.save()
        _audit(request, 'TEST_UPDATED', test, {'code': test.code, 'name': test.name, 'is_active': test.is_active})
        messages.success(request, f'Test {test.code} was saved.')
    return redirect(f'/catalog/tests/?test={test.pk}')


def _save_parameter(request, test):
    code = _text(request.POST, 'param_code').upper()
    name = _text(request.POST, 'param_name')
    if not code or not name:
        raise ValidationError('Parameter code and name are required.')
    result_type = _text(request.POST, 'result_type') or 'TEXT'
    if result_type not in dict(TestParameter.RESULT_TYPES):
        raise ValidationError('Choose a valid result type.')
    unit = _text(request.POST, 'unit')
    reference_range = _text(request.POST, 'reference_range')
    try:
        display_order = int(request.POST.get('display_order') or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError('Display order must be a whole number.') from exc
    is_required = _checked(request.POST, 'is_required') if request.POST.get('is_required_submitted') else True
    param_id = request.POST.get('parameter')
    if param_id:
        param = get_object_or_404(TestParameter, pk=param_id, test=test)
        if TestParameter.objects.filter(test=test, code__iexact=code).exclude(pk=param.pk).exists():
            raise ValidationError('That parameter code already exists on this test.')
        param.code = code
        param.name = name
        param.result_type = result_type
        param.unit = unit
        param.reference_range = reference_range
        param.display_order = display_order
        param.is_required = _checked(request.POST, 'is_required')
        param.save()
        action = 'TEST_PARAMETER_UPDATED'
    else:
        if TestParameter.objects.filter(test=test, code__iexact=code).exists():
            raise ValidationError('That parameter code already exists on this test.')
        param = TestParameter.objects.create(
            test=test, code=code, name=name, result_type=result_type, unit=unit,
            reference_range=reference_range, display_order=display_order, is_required=is_required,
        )
        action = 'TEST_PARAMETER_CREATED'
    _audit(request, action, param, {'test': test.code, 'parameter': param.code})
    messages.success(request, f'Parameter {param.code} was saved.')
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


def _save_service_group(request, group):
    name = _text(request.POST, 'name')
    code = _text(request.POST, 'code')
    if not name or not code:
        raise ValidationError('Name and a single-digit code are required.')
    if len(code) != 1 or not code.isdigit():
        raise ValidationError('Service group code must be a single digit.')
    is_active = _checked(request.POST, 'is_active') if group or request.POST.get('is_active_submitted') else True
    if group is None:
        if ServiceGroup.objects.filter(name__iexact=name, code=code).exists():
            raise ValidationError('That service group already exists.')
        group = ServiceGroup(name=name, code=code, is_active=is_active)
        group.full_clean()
        group.save()
        _audit(request, 'SERVICE_GROUP_CREATED', group, {'name': group.name, 'code': group.code})
        messages.success(request, f'Service group {group.name} was created.')
    else:
        if ServiceGroup.objects.filter(name__iexact=name, code=code).exclude(pk=group.pk).exists():
            raise ValidationError('That service group already exists.')
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
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    centre_id = request.GET.get('centre') or ''
    rows = ReferralDoctor.objects.select_related('centre').annotate(institution_count=Count('institutions')).order_by('last_name', 'first_name')
    if query:
        rows = rows.filter(
            Q(doctor_code__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(facility_name__icontains=query) | Q(phone__icontains=query)
        )
    if centre_id:
        rows = rows.filter(centre_id=centre_id)
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('doctor')
    if selected_id:
        selected = ReferralDoctor.objects.select_related('centre').prefetch_related('institutions').filter(pk=selected_id).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centre_id': centre_id,
        'selected': selected,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
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
    centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
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
        'is_active': _checked(request.POST, 'is_active') if doctor or request.POST.get('is_active_submitted') else True,
    }
    if doctor is None:
        if ReferralDoctor.objects.filter(doctor_code__iexact=doctor_code).exists():
            raise ValidationError('A doctor with that code already exists.')
        doctor = ReferralDoctor(**fields)
        doctor.save()
        _audit(request, 'REFERRAL_DOCTOR_CREATED', doctor, {'doctor_code': doctor.doctor_code}, centre=centre)
        messages.success(request, f'Doctor {doctor.doctor_code} was created.')
    else:
        if ReferralDoctor.objects.filter(doctor_code__iexact=doctor_code).exclude(pk=doctor.pk).exists():
            raise ValidationError('A doctor with that code already exists.')
        for key, value in fields.items():
            setattr(doctor, key, value)
        doctor.is_active = _checked(request.POST, 'is_active')
        doctor.save()
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
    if procedure is None:
        if ImagingProcedure.objects.filter(code__iexact=code).exists():
            raise ValidationError('A procedure with that code already exists.')
        procedure = ImagingProcedure(code=code, name=name, modality_type=modality_type, is_active=is_active)
        procedure.save()
        _audit(request, 'IMAGING_PROCEDURE_CREATED', procedure, {'code': procedure.code})
        messages.success(request, f'Procedure {procedure.code} was created.')
    else:
        if ImagingProcedure.objects.filter(code__iexact=code).exclude(pk=procedure.pk).exists():
            raise ValidationError('A procedure with that code already exists.')
        procedure.code = code
        procedure.name = name
        procedure.modality_type = modality_type
        procedure.is_active = _checked(request.POST, 'is_active')
        procedure.save()
        _audit(request, 'IMAGING_PROCEDURE_UPDATED', procedure, {'code': procedure.code})
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
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Centre.objects.select_related('sample_code_config').order_by('name')
    if query:
        rows = rows.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(phone__icontains=query))
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('centre')
    if selected_id:
        selected = Centre.objects.select_related('sample_code_config').filter(pk=selected_id).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
    }


def handle_centres_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_centre(request, None)
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        if action == 'update':
            return _save_centre(request, centre)
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
    fields = {
        'code': code,
        'name': name,
        'address': _text(request.POST, 'address'),
        'phone': _text(request.POST, 'phone'),
        'email': _text(request.POST, 'email'),
        'timezone': _text(request.POST, 'timezone') or 'Africa/Lagos',
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
        if sample_digit:
            if SampleCodeConfig.objects.filter(numeric_code=sample_digit).exclude(centre=centre).exists():
                raise ValidationError('That sample digit is already assigned to another centre.')
            config, _created = SampleCodeConfig.objects.update_or_create(
                centre=centre,
                defaults={'numeric_code': sample_digit, 'is_active': True},
            )
            _audit(request, 'SAMPLE_CODE_CONFIG_SAVED', config, {'centre': centre.code, 'digit': sample_digit}, centre=centre)
        _audit(request, action, centre, {'code': centre.code, 'name': centre.name, 'has_logo': bool(centre.logo)}, centre=centre)
    return redirect(f'/catalog/centres/?centre={centre.pk}')


# --- Organisation clients ------------------------------------------------

def build_clients_context(request):
    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    centre_id = request.GET.get('centre') or ''
    kind = request.GET.get('kind') or ''
    rows = OrganisationClient.objects.select_related('centre').order_by('name')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))
    if centre_id:
        rows = rows.filter(centre_id=centre_id)
    if kind:
        rows = rows.filter(kind=kind)
    rows = _active_filter(rows, status)
    selected = None
    selected_id = request.GET.get('client')
    if selected_id:
        selected = OrganisationClient.objects.select_related('centre').filter(pk=selected_id).first()
    return {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centre_id': centre_id,
        'kind': kind,
        'selected': selected,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
        'kinds': OrganisationClient.KINDS,
    }


def handle_clients_post(request):
    action = request.POST.get('action')
    try:
        if action == 'create':
            return _save_client(request, None)
        client = get_object_or_404(OrganisationClient, pk=request.POST.get('client'))
        if action == 'update':
            return _save_client(request, client)
        if action == 'deactivate':
            client.is_active = False
            client.save(update_fields=['is_active'])
            _audit(request, 'ORGANISATION_CLIENT_DEACTIVATED', client, {'name': client.name}, centre=client.centre)
            messages.success(request, f'{client.name} is inactive.')
            return redirect('catalog-clients')
        if action == 'activate':
            client.is_active = True
            client.save(update_fields=['is_active'])
            _audit(request, 'ORGANISATION_CLIENT_ACTIVATED', client, {'name': client.name}, centre=client.centre)
            messages.success(request, f'{client.name} is active again.')
            return redirect(f'/catalog/clients/?client={client.pk}')
        if action == 'delete':
            return _try_delete(request, client, 'catalog-clients', client.name)
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        target = request.POST.get('client')
        return redirect(f'/catalog/clients/?client={target}' if target else 'catalog-clients')
    messages.error(request, 'Choose an organisation client action.')
    return redirect('catalog-clients')


def _save_client(request, client):
    centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
    kind = _text(request.POST, 'kind')
    name = _text(request.POST, 'name')
    if not kind or not name:
        raise ValidationError('Client kind and name are required.')
    if kind not in dict(OrganisationClient.KINDS):
        raise ValidationError('Choose HMO or corporate.')
    code = _text(request.POST, 'code')
    is_active = _checked(request.POST, 'is_active') if client or request.POST.get('is_active_submitted') else True
    if client is None:
        if OrganisationClient.objects.filter(centre=centre, kind=kind, name__iexact=name).exists():
            raise ValidationError('That client already exists at this centre.')
        client = OrganisationClient(centre=centre, kind=kind, name=name, code=code, is_active=is_active)
        client.save()
        _audit(request, 'ORGANISATION_CLIENT_CREATED', client, {'name': client.name, 'kind': client.kind}, centre=centre)
        messages.success(request, f'{client.name} was created.')
    else:
        if OrganisationClient.objects.filter(centre=centre, kind=kind, name__iexact=name).exclude(pk=client.pk).exists():
            raise ValidationError('That client already exists at this centre.')
        client.centre = centre
        client.kind = kind
        client.name = name
        client.code = code
        client.is_active = _checked(request.POST, 'is_active')
        client.save()
        _audit(request, 'ORGANISATION_CLIENT_UPDATED', client, {'name': client.name, 'kind': client.kind}, centre=client.centre)
        messages.success(request, f'{client.name} was saved.')
    return redirect(f'/catalog/clients/?client={client.pk}')
