"""Clients master, client types, registration channels, and credit wallet top-ups."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from billing.pricing import top_up_credit_wallet
from core.access import is_platform_admin
from core.audit import record_audit
from core.models import Centre
from core.paging import PAGE_SIZES, paginate
from laboratory.models import Tariff
from patients.channel_seed import ensure_centre_channels, ensure_default_client_types
from patients.models import (
    BILLING_TYPE_CHOICES, CLIENT_CATEGORY_CHOICES, ClientType, CreditWalletLedger,
    INVOICE_FREQUENCY_CHOICES, OrganisationClient, PatientTitle, RegistrationChannel,
)
from patients.titles import ensure_default_titles


def _admin_only(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if not (is_platform_admin(request.user) or request.user.has_perm('patients.manage_clients')):
        raise PermissionDenied('Only administrators can manage clients and channels.')


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


@require_http_methods(['GET', 'POST'])
def client_types_page(request):
    _admin_only(request)
    ensure_default_client_types()
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                code = _text(request.POST, 'code').upper().replace(' ', '_')
                name = _text(request.POST, 'name')
                if not code or not name:
                    raise ValidationError('Code and name are required.')
                row = ClientType(
                    code=code, name=name,
                    show_test_prices_default=_checked(request.POST, 'show_test_prices_default'),
                    is_active=True,
                )
                row.save()
                messages.success(request, f'Client type {name} was created.')
            elif action == 'update':
                row = get_object_or_404(ClientType, pk=request.POST.get('client_type'))
                if not row.is_system:
                    row.code = _text(request.POST, 'code').upper().replace(' ', '_') or row.code
                row.name = _text(request.POST, 'name') or row.name
                row.show_test_prices_default = _checked(request.POST, 'show_test_prices_default')
                row.is_active = _checked(request.POST, 'is_active')
                row.save()
                messages.success(request, f'Client type {row.name} was saved.')
            else:
                messages.error(request, 'Choose a client type action.')
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
        return redirect('enterprise-client-types')
    return render(request, 'enterprise/client_types.html', {
        'types': ClientType.objects.order_by('sort_order', 'name'),
    })


@require_http_methods(['GET', 'POST'])
def patient_titles_page(request):
    """CRUD for patient honorific titles used on registration."""
    _admin_only(request)
    ensure_default_titles()
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                code = _text(request.POST, 'code')
                label = _text(request.POST, 'label') or code
                if not code:
                    raise ValidationError('Title code is required.')
                if len(code) > 20:
                    raise ValidationError('Title code must be 20 characters or fewer.')
                row = PatientTitle(
                    code=code,
                    label=label[:40],
                    sort_order=int(_text(request.POST, 'sort_order') or '0'),
                    is_active=True,
                    is_system=False,
                )
                row.full_clean()
                row.save()
                messages.success(request, f'Title “{row.label}” was created.')
            elif action == 'update':
                row = get_object_or_404(PatientTitle, pk=request.POST.get('title'))
                if not row.is_system:
                    row.code = _text(request.POST, 'code') or row.code
                row.label = _text(request.POST, 'label') or row.label
                row.sort_order = int(_text(request.POST, 'sort_order') or str(row.sort_order) or '0')
                row.is_active = _checked(request.POST, 'is_active')
                row.full_clean()
                row.save()
                messages.success(request, f'Title “{row.label}” was saved.')
            elif action == 'delete':
                row = get_object_or_404(PatientTitle, pk=request.POST.get('title'))
                if row.is_system:
                    raise ValidationError('System titles cannot be deleted. Deactivate them instead.')
                label = row.label
                row.delete()
                messages.success(request, f'Title “{label}” was deleted.')
            else:
                messages.error(request, 'Choose a title action.')
        except (ValidationError, IntegrityError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('enterprise-patient-titles')
    return render(request, 'enterprise/patient_titles.html', {
        'titles': PatientTitle.objects.order_by('sort_order', 'label'),
    })


@require_http_methods(['GET', 'POST'])
def channels_page(request):
    _admin_only(request)
    centres = Centre.objects.filter(is_active=True).order_by('name')
    for centre in centres:
        ensure_centre_channels(centre)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            channel = get_object_or_404(RegistrationChannel, pk=request.POST.get('channel'))
            if action == 'save':
                tariff_id = _text(request.POST, 'tariff')
                channel.tariff = Tariff.objects.filter(pk=tariff_id, is_active=True).first() if tariff_id else None
                channel.show_test_prices = _checked(request.POST, 'show_test_prices')
                channel.is_active = _checked(request.POST, 'is_active')
                channel.name = _text(request.POST, 'name') or channel.get_code_display()
                channel.full_clean()
                channel.save()
                record_audit(
                    request=request, actor=request.user, action='REGISTRATION_CHANNEL_UPDATED',
                    object_type='RegistrationChannel', object_id=str(channel.pk), centre=channel.centre,
                    detail={'code': channel.code, 'tariff': channel.tariff_id, 'show_test_prices': channel.show_test_prices},
                )
                messages.success(request, f'{channel.centre.code} / {channel.get_code_display()} was saved.')
            else:
                messages.error(request, 'Choose a channel action.')
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('enterprise-channels')
    centre_id = request.GET.get('centre') or ''
    rows = RegistrationChannel.objects.select_related('centre', 'tariff').order_by('centre__code', 'code')
    if centre_id:
        rows = rows.filter(centre_id=centre_id)
    return render(request, 'enterprise/channels.html', {
        'channels': rows,
        'centres': centres,
        'centre_id': centre_id,
        'tariffs': Tariff.objects.filter(is_active=True).order_by('name'),
    })


def _client_form_values(request, client=None):
    ensure_default_client_types()
    client_type = get_object_or_404(ClientType, pk=request.POST.get('client_type'), is_active=True)
    centre = get_object_or_404(Centre, pk=request.POST.get('centre'), is_active=True)
    name = _text(request.POST, 'name')
    if not name:
        raise ValidationError('Client name is required.')
    tariff_id = _text(request.POST, 'tariff')
    tariff = Tariff.objects.filter(pk=tariff_id, is_active=True).first() if tariff_id else None
    if tariff is None:
        raise ValidationError('Each client must have a mapped tariff.')
    values = {
        'centre': centre,
        'client_type': client_type,
        'client_type_code': client_type.code,
        'category': _text(request.POST, 'category'),
        'name': name,
        'code': _text(request.POST, 'code'),
        'tariff': tariff,
        'show_test_prices': _checked(request.POST, 'show_test_prices'),
        'effective_from': _date(request.POST, 'effective_from'),
        'effective_to': _date(request.POST, 'effective_to'),
        'credit_limit': _decimal(request.POST, 'credit_limit', '0'),
        'credit_limit_days': int(_text(request.POST, 'credit_limit_days') or '0'),
        'threshold_value': _decimal(request.POST, 'threshold_value', '0'),
        'security_deposit': _decimal(request.POST, 'security_deposit', '0'),
        'org_percent': _decimal(request.POST, 'org_percent', '100'),
        'emp_percent': _decimal(request.POST, 'emp_percent', '0'),
        'enable_auto_locking': _checked(request.POST, 'enable_auto_locking'),
        'is_copayment': _checked(request.POST, 'is_copayment'),
        'billing_type': _text(request.POST, 'billing_type'),
        'invoice_frequency': _text(request.POST, 'invoice_frequency') or 'MONTHLY',
        'address_1': _text(request.POST, 'address_1'),
        'address_2': _text(request.POST, 'address_2'),
        'area': _text(request.POST, 'area'),
        'city': _text(request.POST, 'city'),
        'state': _text(request.POST, 'state'),
        'country': _text(request.POST, 'country') or 'Nigeria',
        'pin_zip': _text(request.POST, 'pin_zip'),
        'phone': _text(request.POST, 'phone'),
        'sap_code': _text(request.POST, 'sap_code'),
        'gst_number': _text(request.POST, 'gst_number'),
        'tin_number': _text(request.POST, 'tin_number'),
        'pan_number': _text(request.POST, 'pan_number'),
        'report_dispatch_type': _text(request.POST, 'report_dispatch_type'),
        'contact_person': _text(request.POST, 'contact_person'),
        'contact_designation': _text(request.POST, 'contact_designation'),
        'contact_phone': _text(request.POST, 'contact_phone'),
        'contact_mobile_1': _text(request.POST, 'contact_mobile_1'),
        'contact_mobile_2': _text(request.POST, 'contact_mobile_2'),
        'report_email': _text(request.POST, 'report_email'),
        'comm_email': _text(request.POST, 'comm_email'),
        'nature_of_business': _text(request.POST, 'nature_of_business'),
        'scope_of_business': _text(request.POST, 'scope_of_business'),
        'covering_letter_info': _text(request.POST, 'covering_letter_info'),
        'invoice_footer': _text(request.POST, 'invoice_footer'),
        'portal_username': _text(request.POST, 'portal_username'),
        'email_required': _checked(request.POST, 'email_required'),
        'sms_required': _checked(request.POST, 'sms_required'),
        'trf_mandatory': _checked(request.POST, 'trf_mandatory'),
        'employee_id_mandatory': _checked(request.POST, 'employee_id_mandatory'),
        'do_not_process_invoice': _checked(request.POST, 'do_not_process_invoice'),
        'is_locked': _checked(request.POST, 'is_locked'),
        'is_active': _checked(request.POST, 'is_active') if client or request.POST.get('is_active_submitted') else True,
    }
    if client_type.code == 'CREDIT' and client is None:
        # Initial wallet allocation on create.
        values['credit_wallet_balance'] = values['credit_limit']
    return values


@require_http_methods(['GET', 'POST'])
def clients_master(request):
    _admin_only(request)
    ensure_default_client_types()
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                values = _client_form_values(request)
                if OrganisationClient.objects.filter(
                    centre=values['centre'], client_type_code=values['client_type_code'], name__iexact=values['name'],
                ).exists():
                    raise ValidationError(
                        'That organisation already exists for this client type at this centre. '
                        'Create a separate Credit and On Payment record when both are needed.'
                    )
                client = OrganisationClient(**values, created_by=request.user, approval_status='APPROVED')
                if request.FILES.get('agreement_file'):
                    client.agreement_file = request.FILES['agreement_file']
                client.save()
                if client.client_type_code == 'CREDIT' and client.credit_wallet_balance > 0:
                    CreditWalletLedger.objects.create(
                        client=client, entry_type='TOP_UP', amount=client.credit_wallet_balance,
                        balance_after=client.credit_wallet_balance, created_by=request.user,
                        note='Initial credit wallet allocation at client creation',
                    )
                record_audit(
                    request=request, actor=request.user, action='ORGANISATION_CLIENT_CREATED',
                    object_type='OrganisationClient', object_id=str(client.pk), centre=client.centre,
                    detail={'name': client.name, 'client_type': client.client_type_code, 'tariff': client.tariff_id},
                )
                messages.success(request, f'{client.name} was created.')
                return redirect(f'/enterprise/clients/?client={client.pk}')
            client = get_object_or_404(OrganisationClient, pk=request.POST.get('client'))
            if action == 'update':
                values = _client_form_values(request, client)
                if OrganisationClient.objects.filter(
                    centre=values['centre'], client_type_code=values['client_type_code'], name__iexact=values['name'],
                ).exclude(pk=client.pk).exists():
                    raise ValidationError('That organisation already exists for this client type at this centre.')
                for key, value in values.items():
                    if key == 'credit_wallet_balance':
                        continue
                    setattr(client, key, value)
                if request.FILES.get('agreement_file'):
                    client.agreement_file = request.FILES['agreement_file']
                client.save()
                record_audit(
                    request=request, actor=request.user, action='ORGANISATION_CLIENT_UPDATED',
                    object_type='OrganisationClient', object_id=str(client.pk), centre=client.centre,
                    detail={'name': client.name, 'client_type': client.client_type_code},
                )
                messages.success(request, f'{client.name} was saved.')
                return redirect(f'/enterprise/clients/?client={client.pk}')
            if action == 'top_up':
                if not (
                    is_platform_admin(request.user)
                    or request.user.has_perm('patients.topup_credit_wallet')
                ):
                    raise PermissionDenied('You cannot top up credit wallets.')
                amount = _decimal(request.POST, 'amount')
                entry = top_up_credit_wallet(
                    client=client, amount=amount, actor=request.user,
                    reference=_text(request.POST, 'reference'),
                    note=_text(request.POST, 'note') or 'Admin wallet top-up',
                )
                messages.success(
                    request,
                    f'Top-up of ₦{amount:.2f} recorded. New balance ₦{entry.balance_after:.2f}.',
                )
                return redirect(f'/enterprise/clients/?client={client.pk}')
            if action == 'deactivate':
                client.is_active = False
                client.save(update_fields=['is_active', 'updated_at'])
                messages.success(request, f'{client.name} is inactive.')
                return redirect('enterprise-clients')
            if action == 'activate':
                client.is_active = True
                client.save(update_fields=['is_active', 'updated_at'])
                messages.success(request, f'{client.name} is active again.')
                return redirect(f'/enterprise/clients/?client={client.pk}')
        except ValidationError as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            target = request.POST.get('client')
            return redirect(f'/enterprise/clients/?client={target}' if target else 'enterprise-clients')
        messages.error(request, 'Choose a client action.')
        return redirect('enterprise-clients')

    query = _text(request.GET, 'q')
    status = request.GET.get('status') or 'all'
    centre_id = request.GET.get('centre') or ''
    type_code = request.GET.get('type') or ''
    rows = OrganisationClient.objects.select_related('centre', 'client_type', 'tariff').order_by('name')
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))
    if centre_id:
        rows = rows.filter(centre_id=centre_id)
    if type_code:
        rows = rows.filter(client_type_code=type_code)
    if status == 'active':
        rows = rows.filter(is_active=True)
    elif status == 'inactive':
        rows = rows.filter(is_active=False)
    selected = None
    selected_id = request.GET.get('client')
    ledger = []
    if selected_id:
        selected = OrganisationClient.objects.select_related('centre', 'client_type', 'tariff').filter(pk=selected_id).first()
        if selected:
            ledger = list(selected.wallet_ledger.select_related('created_by')[:50])
    return render(request, 'enterprise/clients_master.html', {
        'directory': paginate(request, rows, 'page'),
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'centre_id': centre_id,
        'type_code': type_code,
        'selected': selected,
        'ledger': ledger,
        'centres': Centre.objects.filter(is_active=True).order_by('name'),
        'client_types': ClientType.objects.filter(is_active=True),
        'categories': CLIENT_CATEGORY_CHOICES,
        'billing_types': BILLING_TYPE_CHOICES,
        'invoice_frequencies': INVOICE_FREQUENCY_CHOICES,
        'tariffs': Tariff.objects.filter(is_active=True).order_by('name'),
        'today': timezone.localdate().isoformat(),
    })
