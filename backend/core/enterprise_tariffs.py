"""Tariff master: create, edit, clone, upload/download, safe delete, activity history."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.access import is_platform_admin
from core.models import Centre
from core.paging import PAGE_SIZES, paginate
from laboratory.models import ServiceGroup, Tariff, TariffActivity, Test, TestPrice
from laboratory.tariff_io import build_csv_bytes, build_xlsx_bytes, validate_and_apply_upload
from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure


def _admin_only(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not is_platform_admin(request.user):
        raise PermissionDenied('Only a platform administrator can manage tariffs.')


def _text(post, name, default=''):
    return (post.get(name) or default).strip()


def _checked(post, name):
    return post.get(name) in ('on', 'true', '1', 'yes')


def _decimal(post, name, default='0'):
    raw = (post.get(name) or default or '0').strip().replace(',', '')
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f'{name.replace("_", " ").title()} must be a number.') from exc


def _date(post, name):
    raw = _text(post, name)
    if not raw:
        return None
    from datetime import date
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(f'{name.replace("_", " ").title()} must be a valid date.') from exc


def _log(tariff, action, actor, detail=None):
    TariffActivity.objects.create(
        tariff=tariff,
        tariff_code=getattr(tariff, 'code', '') or '',
        tariff_name=getattr(tariff, 'name', '') or '',
        action=action,
        detail=detail or {},
        actor=actor,
    )


@require_http_methods(['GET', 'POST'])
def tariff_master(request):
    _admin_only(request)
    tab = request.GET.get('tab') or 'list'
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                name = _text(request.POST, 'name')
                if not name:
                    raise ValidationError('Tariff name is required.')
                centre = None
                if request.POST.get('centre'):
                    centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
                tariff = Tariff(
                    name=name,
                    centre=centre,
                    effective_from=_date(request.POST, 'effective_from') or timezone.localdate(),
                    effective_to=_date(request.POST, 'effective_to'),
                    status='DRAFT',
                    created_by=request.user,
                    modified_by=request.user,
                    is_outsource_enabled=_checked(request.POST, 'is_outsource_enabled'),
                )
                tariff.save()
                _log(tariff, 'CREATED', request.user, {'name': tariff.name, 'code': tariff.code})
                messages.success(request, f'Tariff {tariff.name} ({tariff.code}) was created.')
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit')
            if action == 'clone':
                if not request.user.has_perm('laboratory.clone_tariff') and not is_platform_admin(request.user):
                    raise PermissionDenied('You cannot clone tariffs.')
                source = get_object_or_404(Tariff, pk=request.POST.get('source_tariff'))
                name = _text(request.POST, 'name')
                if not name:
                    raise ValidationError('New tariff name is required.')
                with transaction.atomic():
                    clone = Tariff(
                        name=name,
                        centre=source.centre,
                        effective_from=_date(request.POST, 'effective_from') or timezone.localdate(),
                        effective_to=_date(request.POST, 'effective_to') or source.effective_to,
                        status='DRAFT',
                        is_outsource_enabled=source.is_outsource_enabled,
                        copied_from=source,
                        created_by=request.user,
                        modified_by=request.user,
                        version=1,
                    )
                    clone.save()
                    TestPrice.objects.bulk_create([
                        TestPrice(
                            tariff=clone, test=row.test, price=row.price,
                            outsource_price=row.outsource_price, percentage=row.percentage,
                        )
                        for row in TestPrice.objects.filter(tariff=source)
                    ])
                    ImagingPrice.objects.bulk_create([
                        ImagingPrice(
                            tariff=clone, procedure=row.procedure, price=row.price,
                            outsource_price=row.outsource_price, percentage=row.percentage,
                        )
                        for row in ImagingPrice.objects.filter(tariff=source)
                    ])
                    _log(clone, 'CLONED', request.user, {'from': source.code, 'from_name': source.name})
                messages.success(request, f'Cloned {source.name} into {clone.name}.')
                return redirect(f'/enterprise/tariffs/{clone.pk}/?mode=edit')
            if action in ('activate', 'deactivate', 'approve'):
                tariff = get_object_or_404(Tariff, pk=request.POST.get('tariff'))
                if action == 'approve':
                    if not request.user.has_perm('laboratory.approve_tariff') and not is_platform_admin(request.user):
                        raise PermissionDenied('You cannot approve tariffs.')
                    tariff.status = 'APPROVAL'
                    tariff.approved_by = request.user
                    tariff.approved_at = timezone.now()
                    tariff.is_active = True
                    tariff.modified_by = request.user
                    tariff.save()
                    _log(tariff, 'APPROVED', request.user)
                    messages.success(request, f'{tariff.name} was approved.')
                elif action == 'activate':
                    tariff.status = 'APPROVAL'
                    tariff.is_active = True
                    tariff.modified_by = request.user
                    tariff.save()
                    _log(tariff, 'ACTIVATED', request.user)
                    messages.success(request, f'{tariff.name} is active.')
                else:
                    tariff.status = 'INACTIVE'
                    tariff.is_active = False
                    tariff.modified_by = request.user
                    tariff.save()
                    _log(tariff, 'DEACTIVATED', request.user)
                    messages.success(request, f'{tariff.name} is inactive.')
                return redirect('enterprise-tariffs')
            if action == 'request_delete':
                tariff = get_object_or_404(Tariff, pk=request.POST.get('tariff'))
                return redirect(f'/enterprise/tariffs/{tariff.pk}/delete/')
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect('enterprise-tariffs')
        messages.error(request, 'Choose a tariff action.')
        return redirect('enterprise-tariffs')

    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    rows = Tariff.objects.annotate(
        lab_price_count=Count('testprice', distinct=True),
        imaging_price_count=Count('imagingprice', distinct=True),
    ).select_related('centre', 'created_by', 'modified_by', 'copied_from').order_by('-updated_at')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))
    if status == 'active':
        rows = rows.filter(is_active=True)
    elif status == 'inactive':
        rows = rows.filter(is_active=False)
    elif status == 'draft':
        rows = rows.filter(status='DRAFT')
    elif status == 'approval':
        rows = rows.filter(status='APPROVAL')
    return render(request, 'enterprise/tariff_master.html', {
        'tab': tab,
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
        'tariffs': Tariff.objects.order_by('name'),
        'today': timezone.localdate().isoformat(),
    })


@require_http_methods(['GET', 'POST'])
def tariff_editor(request, pk):
    _admin_only(request)
    tariff = get_object_or_404(Tariff.objects.select_related('centre', 'copied_from'), pk=pk)
    mode = request.GET.get('mode') or 'edit'
    group_id = request.GET.get('group') or ''
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'update_meta':
                name = _text(request.POST, 'name')
                if not name:
                    raise ValidationError('Tariff name is required.')
                tariff.name = name
                tariff.effective_from = _date(request.POST, 'effective_from') or tariff.effective_from
                tariff.effective_to = _date(request.POST, 'effective_to')
                tariff.is_outsource_enabled = _checked(request.POST, 'is_outsource_enabled')
                tariff.modified_by = request.user
                tariff.save()
                _log(tariff, 'UPDATED', request.user, {'name': tariff.name})
                messages.success(request, 'Tariff details saved.')
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit')
            if action == 'save_prices':
                selected = request.POST.getlist('selected_tests')
                if not selected:
                    raise ValidationError('Select at least one test to update.')
                saved = 0
                with transaction.atomic():
                    for test_id in selected:
                        test = Test.objects.filter(pk=test_id, is_active=True).first()
                        if test is None:
                            continue
                        price = _decimal(request.POST, f'price_{test_id}')
                        outsource = _decimal(request.POST, f'outsource_{test_id}', '0')
                        percentage = _decimal(request.POST, f'percentage_{test_id}', '0')
                        TestPrice.objects.update_or_create(
                            tariff=tariff, test=test,
                            defaults={
                                'price': price,
                                'outsource_price': outsource,
                                'percentage': percentage,
                            },
                        )
                        saved += 1
                    tariff.modified_by = request.user
                    tariff.version = (tariff.version or 1) + 1
                    tariff.save(update_fields=['modified_by', 'version', 'updated_at'])
                    _log(tariff, 'PRICES_SAVED', request.user, {'count': saved, 'kind': 'laboratory'})
                messages.success(request, f'Saved prices for {saved} laboratory test(s).')
                suffix = f'&group={group_id}' if group_id else ''
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit{suffix}')
            if action == 'save_imaging_prices':
                selected = request.POST.getlist('selected_procedures')
                modality = _text(request.POST, 'modality_type') or request.GET.get('imaging') or ''
                if not selected:
                    raise ValidationError('Select at least one imaging procedure to update.')
                saved = 0
                with transaction.atomic():
                    for procedure_id in selected:
                        procedure = ImagingProcedure.objects.filter(pk=procedure_id, is_active=True).first()
                        if procedure is None:
                            continue
                        price = _decimal(request.POST, f'img_price_{procedure_id}')
                        outsource = _decimal(request.POST, f'img_outsource_{procedure_id}', '0')
                        percentage = _decimal(request.POST, f'img_percentage_{procedure_id}', '0')
                        ImagingPrice.objects.update_or_create(
                            tariff=tariff, procedure=procedure,
                            defaults={
                                'price': price,
                                'outsource_price': outsource,
                                'percentage': percentage,
                            },
                        )
                        saved += 1
                    tariff.modified_by = request.user
                    tariff.version = (tariff.version or 1) + 1
                    tariff.save(update_fields=['modified_by', 'version', 'updated_at'])
                    _log(tariff, 'PRICES_SAVED', request.user, {'count': saved, 'kind': 'imaging'})
                messages.success(request, f'Saved prices for {saved} imaging procedure(s).')
                suffix = f'&imaging={modality}' if modality else ''
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit{suffix}')
            if action == 'apply_clone_prices':
                source = get_object_or_404(Tariff, pk=request.POST.get('source_tariff'))
                with transaction.atomic():
                    for row in TestPrice.objects.filter(tariff=source):
                        TestPrice.objects.update_or_create(
                            tariff=tariff, test=row.test,
                            defaults={
                                'price': row.price,
                                'outsource_price': row.outsource_price,
                                'percentage': row.percentage,
                            },
                        )
                    for row in ImagingPrice.objects.filter(tariff=source):
                        ImagingPrice.objects.update_or_create(
                            tariff=tariff, procedure=row.procedure,
                            defaults={
                                'price': row.price,
                                'outsource_price': row.outsource_price,
                                'percentage': row.percentage,
                            },
                        )
                    tariff.copied_from = source
                    tariff.modified_by = request.user
                    tariff.version = (tariff.version or 1) + 1
                    tariff.save()
                    _log(tariff, 'CLONED', request.user, {'from': source.code, 'applied_to_existing': True})
                messages.success(request, f'Copied prices from {source.name}.')
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit')
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit')
        messages.error(request, 'Choose a tariff editor action.')
        return redirect(f'/enterprise/tariffs/{tariff.pk}/')

    groups = ServiceGroup.objects.filter(is_active=True).order_by('name')
    imaging_modalities = list(ImagingModality.MODALITY_TYPES)
    selected_group = None
    selected_imaging = (request.GET.get('imaging') or '').strip().upper()
    tests = Test.objects.none()
    test_rows = []
    imaging_rows = []
    if group_id:
        selected_group = ServiceGroup.objects.filter(pk=group_id).first()
        if selected_group:
            tests = Test.objects.filter(is_active=True, service_group=selected_group).order_by('name')
            price_map = {
                row.test_id: row
                for row in TestPrice.objects.filter(tariff=tariff, test__in=tests)
            }
            test_rows = [
                {
                    'test': test,
                    'old_price': price_map[test.pk].price if test.pk in price_map else None,
                    'current_price': price_map[test.pk].price if test.pk in price_map else '',
                    'percentage': price_map[test.pk].percentage if test.pk in price_map else Decimal('0'),
                    'outsource': price_map[test.pk].outsource_price if test.pk in price_map else Decimal('0'),
                }
                for test in tests
            ]
    elif selected_imaging:
        valid = {code for code, _label in ImagingModality.MODALITY_TYPES}
        if selected_imaging not in valid:
            selected_imaging = ''
        else:
            procedures = ImagingProcedure.objects.filter(
                is_active=True, modality_type=selected_imaging,
            ).order_by('name')
            price_map = {
                row.procedure_id: row
                for row in ImagingPrice.objects.filter(tariff=tariff, procedure__in=procedures)
            }
            imaging_rows = [
                {
                    'procedure': procedure,
                    'old_price': price_map[procedure.pk].price if procedure.pk in price_map else None,
                    'current_price': price_map[procedure.pk].price if procedure.pk in price_map else '',
                    'percentage': price_map[procedure.pk].percentage if procedure.pk in price_map else Decimal('0'),
                    'outsource': price_map[procedure.pk].outsource_price if procedure.pk in price_map else Decimal('0'),
                }
                for procedure in procedures
            ]
    activities = TariffActivity.objects.filter(Q(tariff=tariff) | Q(tariff_code=tariff.code)).select_related('actor')[:40]
    return render(request, 'enterprise/tariff_editor.html', {
        'tariff': tariff,
        'mode': mode,
        'groups': groups,
        'imaging_modalities': imaging_modalities,
        'selected_group': selected_group,
        'selected_imaging': selected_imaging,
        'test_rows': test_rows,
        'imaging_rows': imaging_rows,
        'activities': activities,
        'all_tariffs': Tariff.objects.exclude(pk=tariff.pk).order_by('name'),
        'lab_count': TestPrice.objects.filter(tariff=tariff).count(),
        'imaging_count': ImagingPrice.objects.filter(tariff=tariff).count(),
    })


@require_http_methods(['GET', 'POST'])
def tariff_upload(request):
    _admin_only(request)
    if not request.user.has_perm('laboratory.upload_tariff') and not is_platform_admin(request.user):
        raise PermissionDenied('You need the upload_tariff privilege.')
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            tariff = get_object_or_404(Tariff, pk=request.POST.get('tariff'))
            if action == 'upload':
                uploaded = request.FILES.get('tariff_file')
                if uploaded is None:
                    raise ValidationError('Choose a CSV or Excel file to upload.')
                count = validate_and_apply_upload(tariff=tariff, uploaded_file=uploaded, actor=request.user)
                messages.success(request, f'Upload succeeded. {count} service price(s) applied to {tariff.name}.')
                return redirect(f'/enterprise/tariffs/{tariff.pk}/?mode=edit')
            if action == 'download':
                fmt = _text(request.POST, 'format') or 'xlsx'
                if fmt == 'csv':
                    payload = build_csv_bytes(tariff)
                    response = HttpResponse(payload, content_type='text/csv')
                    response['Content-Disposition'] = f'attachment; filename="{tariff.code or tariff.name}.csv"'
                    return response
                payload = build_xlsx_bytes(tariff)
                response = HttpResponse(
                    payload,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                response['Content-Disposition'] = f'attachment; filename="{tariff.code or tariff.name}.xlsx"'
                return response
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect('enterprise-tariff-upload')
        messages.error(request, 'Choose upload or download.')
        return redirect('enterprise-tariff-upload')
    return render(request, 'enterprise/tariff_upload.html', {
        'tariffs': Tariff.objects.order_by('name'),
        'headers': ['TariffCode', 'TariffName', 'ServiceCode', 'ServicePrice'],
    })


@require_http_methods(['GET', 'POST'])
def tariff_delete_confirm(request, pk):
    _admin_only(request)
    tariff = get_object_or_404(Tariff, pk=pk)
    if request.method == 'POST':
        step = request.POST.get('step')
        if step == 'insist':
            if _checked(request.POST, 'insist'):
                return render(request, 'enterprise/tariff_delete_agree.html', {'tariff': tariff})
            messages.error(request, 'Deletion cancelled.')
            return redirect(f'/enterprise/tariffs/{tariff.pk}/')
        if step == 'agree':
            if not _checked(request.POST, 'agree'):
                messages.error(request, 'You must accept the agreement to continue.')
                return render(request, 'enterprise/tariff_delete_agree.html', {'tariff': tariff})
            username = _text(request.POST, 'username')
            if username.casefold() != (request.user.username or '').casefold():
                messages.error(request, 'Username does not match the signed-in account.')
                return render(request, 'enterprise/tariff_delete_agree.html', {'tariff': tariff})
            if not request.user.has_perm('laboratory.delete_tariff_confirmed') and not is_platform_admin(request.user):
                raise PermissionDenied('You cannot confirm tariff deletion.')
            code, name = tariff.code, tariff.name
            with transaction.atomic():
                _log(tariff, 'DELETE_REQUESTED', request.user, {'username': username})
                TestPrice.objects.filter(tariff=tariff).delete()
                ImagingPrice.objects.filter(tariff=tariff).delete()
                _log(tariff, 'DELETED', request.user, {'code': code, 'name': name})
                tariff.delete()
            messages.success(request, f'Tariff {name} was deleted.')
            return redirect('enterprise-tariffs')
    return render(request, 'enterprise/tariff_delete_confirm.html', {'tariff': tariff})
