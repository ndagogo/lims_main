"""Bulk-upload datasets: lab tests, imaging, doctors, clients, tariffs, tariff prices."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.bulk_upload.base import Column, Dataset, RowOutcome, ValidationReport, get_dataset, register
from core.models import Centre
from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
from laboratory.test_names import normalize_test_name, test_name_taken
from patients.channel_seed import ensure_default_client_types
from patients.models import ClientType, OrganisationClient
from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure
from referrals.models import ReferralDoctor, ReferralInstitution


def _cell(row, *keys):
    for key in keys:
        value = (row.get(key) or '').strip()
        if value:
            return value
    return ''


def _id(value):
    """Session-safe primary key (UUID/int → str)."""
    return None if value is None else str(value)


def _bool(value, default=True):
    raw = (value or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'y', 'active', 'on')


def _decimal(value, default=None):
    raw = (value or '').strip().replace(',', '')
    if not raw:
        return default
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f'Invalid number: {value}')


def _date(value):
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError(f'Invalid date (use YYYY-MM-DD): {value}') from exc


def _check_headers(dataset: Dataset, headers: list[str]) -> list[str]:
    present = {h for h in headers}
    return [h for h in dataset.required_headers if h not in present]


def _allocate_imaging_code(name: str) -> str:
    from core.models import SequenceCounter
    import re
    letters = re.sub(r'[^A-Za-z]', '', name or '').upper()[:3] or 'IMG'
    with transaction.atomic():
        counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key=f'imaging-code-{letters}')
        counter.value += 1
        counter.save(update_fields=['value'])
        return f'{letters}{counter.value:05d}'


def _allocate_doctor_code(centre: Centre, first: str, last: str) -> str:
    from core.models import SequenceCounter
    import re
    prefix = re.sub(r'[^A-Za-z0-9]', '', f'{centre.code}{(first or "X")[:1]}{(last or "X")[:1]}').upper()[:8] or 'DOC'
    with transaction.atomic():
        counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key=f'doctor-code-{prefix}')
        counter.value += 1
        counter.save(update_fields=['value'])
        return f'{prefix}{counter.value:04d}'


def _resolve_service_group(name: str, code: str = '') -> ServiceGroup:
    name = (name or '').strip()
    code = (code or '').strip()
    if not name and not code:
        raise ValueError('ServiceGroupName or ServiceGroupCode is required.')
    if name:
        existing = ServiceGroup.objects.filter(name__iexact=name).first()
        if existing:
            return existing
    if code and len(code) == 1 and code.isdigit():
        existing = ServiceGroup.objects.filter(code=code, name__iexact=name or code).first()
        if existing:
            return existing
        if name:
            group = ServiceGroup(name=name, code=code, is_active=True)
            group.full_clean()
            group.save()
            return group
    # Allocate a free digit for a new named group.
    if not name:
        raise ValueError('ServiceGroupName is required to create a new group.')
    used = set(ServiceGroup.objects.values_list('code', flat=True))
    digit = next((d for d in '1234567890' if d not in used), None)
    if digit is None:
        raise ValueError('No free service-group digit left; create a service group manually first.')
    group = ServiceGroup(name=name, code=digit, is_active=True)
    group.full_clean()
    group.save()
    return group


# --- Lab tests -----------------------------------------------------------

def validate_lab_tests(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('lab_tests')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('lab_tests', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    seen_names = set()
    for index, row in enumerate(rows, start=2):
        try:
            name = normalize_test_name(_cell(row, 'TestName'))
            if not name:
                raise ValueError('TestName is required.')
            key = name.casefold()
            if key in seen_names:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate TestName in this file.', {'TestName': name}))
                continue
            seen_names.add(key)
            if test_name_taken(name):
                report.outcomes.append(RowOutcome(
                    index, 'duplicate', 'This test already exist. You can not proceed!',
                    {'TestName': name},
                ))
                continue
            group_name = _cell(row, 'ServiceGroupName') or 'General'
            group_code = _cell(row, 'ServiceGroupCode')
            group = ServiceGroup.objects.filter(name__iexact=group_name).first()
            if group is None and group_code and len(group_code) == 1 and group_code.isdigit():
                group = ServiceGroup.objects.filter(code=group_code).first()
            department = _cell(row, 'Department') or 'PATHOLOGY'
            dept_code = _cell(row, 'DepartmentCode') or '1'
            if len(dept_code) != 1 or not dept_code.isdigit():
                raise ValueError('DepartmentCode must be a single digit.')
            price = _decimal(_cell(row, 'Price'), None)
            tariff_name = _cell(row, 'TariffName')
            if price is not None and price < 0:
                raise ValueError('Price cannot be negative.')
            if price is not None and not tariff_name:
                raise ValueError('TariffName is required when Price is provided.')
            tariff = None
            if tariff_name:
                tariff = Tariff.objects.filter(name__iexact=tariff_name).first()
                if tariff is None:
                    raise ValueError(f'Tariff "{tariff_name}" was not found. Create the tariff first or omit Price.')
            payload = {
                'name': name,
                'service_group_id': _id(group.pk) if group else None,
                'service_group_name': group_name,
                'service_group_code': group_code,
                'department': department,
                'department_code': dept_code,
                'sample_volume': _cell(row, 'SampleVolume'),
                'collection_requirements': _cell(row, 'CollectionRequirements'),
                'handling_instructions': _cell(row, 'HandlingInstructions'),
                'is_outsourceable': _bool(_cell(row, 'IsOutsourceable'), False),
                'comments_required': _bool(_cell(row, 'CommentsRequired'), False),
                'is_active': _bool(_cell(row, 'IsActive'), True),
                'tariff_id': _id(tariff.pk) if tariff else None,
                'price': str(price) if price is not None else None,
            }
            report.outcomes.append(RowOutcome(
                index, 'ready', 'Ready to create',
                {
                    'TestName': name,
                    'ServiceGroup': group.name if group else f'{group_name} (new)',
                    'Department': department,
                    'Price': payload['price'] or '—', 'Tariff': tariff_name or '—',
                },
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'TestName': _cell(row, 'TestName')}))
    return report.summarize()


def apply_lab_tests(payloads, *, actor=None) -> dict:
    created = 0
    priced = 0
    with transaction.atomic():
        for payload in payloads:
            group_id = payload.get('service_group_id')
            if not group_id:
                group = _resolve_service_group(
                    payload.get('service_group_name') or 'General',
                    payload.get('service_group_code') or '',
                )
                group_id = group.pk
            test = Test(
                name=payload['name'],
                department=payload['department'],
                department_code=payload['department_code'],
                service_group_id=group_id,
                sample_volume=payload.get('sample_volume') or '',
                collection_requirements=payload.get('collection_requirements') or '',
                handling_instructions=payload.get('handling_instructions') or '',
                is_outsourceable=bool(payload.get('is_outsourceable')),
                comments_required=bool(payload.get('comments_required')),
                is_active=bool(payload.get('is_active', True)),
            )
            test.save()
            created += 1
            if payload.get('tariff_id') and payload.get('price') is not None:
                TestPrice.objects.update_or_create(
                    tariff_id=payload['tariff_id'], test=test,
                    defaults={'price': Decimal(payload['price']), 'outsource_price': Decimal('0'), 'percentage': Decimal('0')},
                )
                priced += 1
    return {'created': created, 'priced': priced}


# --- Imaging procedures --------------------------------------------------

def validate_imaging(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('imaging_procedures')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('imaging_procedures', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    valid_modalities = {code for code, _ in ImagingModality.MODALITY_TYPES}
    seen = set()
    for index, row in enumerate(rows, start=2):
        try:
            name = normalize_test_name(_cell(row, 'ProcedureName'))
            if not name:
                raise ValueError('ProcedureName is required.')
            modality = _cell(row, 'ModalityType').upper()
            if modality not in valid_modalities:
                raise ValueError(f'ModalityType must be one of: {", ".join(sorted(valid_modalities))}.')
            key = name.casefold()
            if key in seen:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate ProcedureName in this file.', {'ProcedureName': name}))
                continue
            seen.add(key)
            if ImagingProcedure.objects.filter(name__iexact=name).exists():
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Procedure already exists.', {'ProcedureName': name}))
                continue
            code = _cell(row, 'Code').upper()
            if code and ImagingProcedure.objects.filter(code__iexact=code).exists():
                report.outcomes.append(RowOutcome(index, 'duplicate', f'Code {code} already exists.', {'ProcedureName': name, 'Code': code}))
                continue
            price = _decimal(_cell(row, 'Price'), None)
            tariff_name = _cell(row, 'TariffName')
            tariff = None
            if price is not None and not tariff_name:
                raise ValueError('TariffName is required when Price is provided.')
            if tariff_name:
                tariff = Tariff.objects.filter(name__iexact=tariff_name).first()
                if tariff is None:
                    raise ValueError(f'Tariff "{tariff_name}" was not found.')
            payload = {
                'name': name,
                'code': code,
                'modality_type': modality,
                'is_active': _bool(_cell(row, 'IsActive'), True),
                'tariff_id': _id(tariff.pk) if tariff else None,
                'price': str(price) if price is not None else None,
            }
            report.outcomes.append(RowOutcome(
                index, 'ready', 'Ready to create',
                {'ProcedureName': name, 'ModalityType': modality, 'Code': code or '(auto)', 'Price': payload['price'] or '—'},
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'ProcedureName': _cell(row, 'ProcedureName')}))
    return report.summarize()


def apply_imaging(payloads, *, actor=None) -> dict:
    created = 0
    priced = 0
    with transaction.atomic():
        for payload in payloads:
            code = payload.get('code') or _allocate_imaging_code(payload['name'])
            procedure = ImagingProcedure(
                code=code, name=payload['name'], modality_type=payload['modality_type'],
                is_active=bool(payload.get('is_active', True)),
            )
            procedure.save()
            created += 1
            if payload.get('tariff_id') and payload.get('price') is not None:
                ImagingPrice.objects.update_or_create(
                    tariff_id=payload['tariff_id'], procedure=procedure,
                    defaults={'price': Decimal(payload['price']), 'outsource_price': Decimal('0'), 'percentage': Decimal('0')},
                )
                priced += 1
    return {'created': created, 'priced': priced}


# --- Referring doctors ---------------------------------------------------

def validate_doctors(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('referral_doctors')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('referral_doctors', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    seen_codes = set()
    seen_names = set()
    for index, row in enumerate(rows, start=2):
        try:
            first = _cell(row, 'FirstName')
            last = _cell(row, 'LastName')
            centre_code = _cell(row, 'CentreCode').upper()
            if not first or not last:
                raise ValueError('FirstName and LastName are required.')
            if not centre_code:
                raise ValueError('CentreCode is required.')
            centre = Centre.objects.filter(code__iexact=centre_code, is_active=True).first()
            if centre is None:
                raise ValueError(f'Centre "{centre_code}" was not found.')
            doctor_code = _cell(row, 'DoctorCode').upper()
            name_key = f'{first.casefold()}|{last.casefold()}|{centre.pk}'
            if name_key in seen_names:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate doctor name for centre in this file.', {'FirstName': first, 'LastName': last}))
                continue
            seen_names.add(name_key)
            if doctor_code:
                if doctor_code in seen_codes:
                    report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate DoctorCode in this file.', {'DoctorCode': doctor_code}))
                    continue
                seen_codes.add(doctor_code)
                if ReferralDoctor.objects.filter(doctor_code__iexact=doctor_code).exists():
                    report.outcomes.append(RowOutcome(index, 'duplicate', 'Doctor code already exists.', {'DoctorCode': doctor_code}))
                    continue
            elif ReferralDoctor.objects.filter(
                centre=centre, first_name__iexact=first, last_name__iexact=last, is_default_placeholder=False,
            ).exists():
                report.outcomes.append(RowOutcome(
                    index, 'duplicate', 'A doctor with this name already exists at the centre.',
                    {'FirstName': first, 'LastName': last, 'CentreCode': centre.code},
                ))
                continue
            commission_type = (_cell(row, 'CommissionType') or 'PERCENT').upper()
            if commission_type not in ('PERCENT', 'FIXED'):
                raise ValueError('CommissionType must be PERCENT or FIXED.')
            commission_value = _decimal(_cell(row, 'CommissionValue'), Decimal('0'))
            if commission_value is None or commission_value < 0:
                raise ValueError('CommissionValue must be zero or greater.')
            institutions = [p.strip() for p in _cell(row, 'Institutions').split('|') if p.strip()]
            payload = {
                'centre_id': _id(centre.pk),
                'doctor_code': doctor_code,
                'title': _cell(row, 'Title'),
                'first_name': first,
                'last_name': last,
                'facility_name': _cell(row, 'FacilityName'),
                'phone': _cell(row, 'Phone'),
                'email': _cell(row, 'Email'),
                'commission_type': commission_type,
                'commission_value': str(commission_value),
                'bank_name': _cell(row, 'BankName'),
                'account_name': _cell(row, 'AccountName'),
                'account_number': _cell(row, 'AccountNumber'),
                'appear_on_reports': _bool(_cell(row, 'AppearOnReports'), True),
                'is_active': _bool(_cell(row, 'IsActive'), True),
                'institutions': institutions,
            }
            report.outcomes.append(RowOutcome(
                index, 'ready', 'Ready to create',
                {
                    'DoctorCode': doctor_code or '(auto)', 'Name': f'{first} {last}',
                    'Centre': centre.code, 'Commission': f'{commission_value} {commission_type}',
                },
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'FirstName': _cell(row, 'FirstName'), 'LastName': _cell(row, 'LastName')}))
    return report.summarize()


def apply_doctors(payloads, *, actor=None) -> dict:
    created = 0
    with transaction.atomic():
        for payload in payloads:
            centre = Centre.objects.get(pk=payload['centre_id'])
            code = payload.get('doctor_code') or _allocate_doctor_code(centre, payload['first_name'], payload['last_name'])
            doctor = ReferralDoctor(
                centre=centre,
                doctor_code=code,
                title=payload.get('title') or '',
                first_name=payload['first_name'],
                last_name=payload['last_name'],
                facility_name=payload.get('facility_name') or '',
                phone=payload.get('phone') or '',
                email=payload.get('email') or '',
                commission_type=payload['commission_type'],
                commission_value=Decimal(payload['commission_value']),
                bank_name=payload.get('bank_name') or '',
                account_name=payload.get('account_name') or '',
                account_number=payload.get('account_number') or '',
                appear_on_reports=bool(payload.get('appear_on_reports', True)),
                is_active=bool(payload.get('is_active', True)),
                onboarded_by=actor if getattr(actor, 'is_authenticated', False) else None,
            )
            doctor.save()
            doctor.assign_centres([centre])
            for name in payload.get('institutions') or []:
                ReferralInstitution.objects.get_or_create(doctor=doctor, name=name, defaults={'is_active': True})
            created += 1
    return {'created': created}


# --- Clients -------------------------------------------------------------

def validate_clients(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('clients')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('clients', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    ensure_default_client_types()
    seen = set()
    for index, row in enumerate(rows, start=2):
        try:
            name = _cell(row, 'ClientName')
            centre_code = _cell(row, 'CentreCode').upper()
            type_code = (_cell(row, 'ClientTypeCode') or 'CREDIT').upper()
            if not name:
                raise ValueError('ClientName is required.')
            if not centre_code:
                raise ValueError('CentreCode is required.')
            centre = Centre.objects.filter(code__iexact=centre_code, is_active=True).first()
            if centre is None:
                raise ValueError(f'Centre "{centre_code}" was not found.')
            client_type = ClientType.objects.filter(code__iexact=type_code, is_active=True).first()
            if client_type is None:
                raise ValueError(f'ClientTypeCode "{type_code}" was not found.')
            key = f'{centre.pk}|{type_code}|{name.casefold()}'
            if key in seen:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate client in this file.', {'ClientName': name}))
                continue
            seen.add(key)
            if OrganisationClient.objects.filter(
                centre=centre, client_type_code=type_code, name__iexact=name,
            ).exists():
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Client already exists at this centre.', {'ClientName': name, 'CentreCode': centre.code}))
                continue
            tariff_name = _cell(row, 'TariffName')
            tariff = None
            if tariff_name:
                tariff = Tariff.objects.filter(name__iexact=tariff_name).first()
                if tariff is None:
                    raise ValueError(f'Tariff "{tariff_name}" was not found.')
            payload = {
                'centre_id': _id(centre.pk),
                'client_type_id': _id(client_type.pk),
                'client_type_code': client_type.code,
                'name': name,
                'code': _cell(row, 'ClientCode'),
                'tariff_id': _id(tariff.pk) if tariff else None,
                'phone': _cell(row, 'Phone'),
                'contact_person': _cell(row, 'ContactPerson'),
                'report_email': _cell(row, 'Email'),
                'credit_limit': str(_decimal(_cell(row, 'CreditLimit'), Decimal('0'))),
                'address_1': _cell(row, 'Address'),
                'city': _cell(row, 'City'),
                'state': _cell(row, 'State'),
                'show_test_prices': _bool(_cell(row, 'ShowTestPrices'), client_type.show_test_prices_default),
                'is_active': _bool(_cell(row, 'IsActive'), True),
            }
            report.outcomes.append(RowOutcome(
                index, 'ready', 'Ready to create',
                {'ClientName': name, 'Centre': centre.code, 'Type': client_type.code, 'Tariff': tariff_name or '—'},
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'ClientName': _cell(row, 'ClientName')}))
    return report.summarize()


def apply_clients(payloads, *, actor=None) -> dict:
    created = 0
    with transaction.atomic():
        for payload in payloads:
            client = OrganisationClient(
                centre_id=payload['centre_id'],
                client_type_id=payload['client_type_id'],
                client_type_code=payload['client_type_code'],
                name=payload['name'],
                code=payload.get('code') or '',
                tariff_id=payload.get('tariff_id'),
                phone=payload.get('phone') or '',
                contact_person=payload.get('contact_person') or '',
                report_email=payload.get('report_email') or '',
                credit_limit=Decimal(payload.get('credit_limit') or '0'),
                address_1=payload.get('address_1') or '',
                city=payload.get('city') or '',
                state=payload.get('state') or '',
                show_test_prices=bool(payload.get('show_test_prices', True)),
                is_active=bool(payload.get('is_active', True)),
                created_by=actor if getattr(actor, 'is_authenticated', False) else None,
                approval_status='APPROVED',
            )
            client.sync_type_fields()
            client.save()
            created += 1
    return {'created': created}


# --- Tariffs (master) ----------------------------------------------------

def validate_tariffs(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('tariffs')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('tariffs', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    seen = set()
    for index, row in enumerate(rows, start=2):
        try:
            name = _cell(row, 'TariffName')
            if not name:
                raise ValueError('TariffName is required.')
            key = name.casefold()
            if key in seen:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate TariffName in this file.', {'TariffName': name}))
                continue
            seen.add(key)
            if Tariff.objects.filter(name__iexact=name).exists():
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Tariff already exists.', {'TariffName': name}))
                continue
            centre = None
            centre_code = _cell(row, 'CentreCode').upper()
            if centre_code:
                centre = Centre.objects.filter(code__iexact=centre_code, is_active=True).first()
                if centre is None:
                    raise ValueError(f'Centre "{centre_code}" was not found.')
            status = (_cell(row, 'Status') or 'DRAFT').upper()
            if status not in ('DRAFT', 'APPROVAL', 'INACTIVE'):
                raise ValueError('Status must be DRAFT, APPROVAL, or INACTIVE.')
            effective_from = _date(_cell(row, 'EffectiveFrom'))
            effective_to = _date(_cell(row, 'EffectiveTo'))
            payload = {
                'name': name,
                'centre_id': centre.pk if centre else None,
                'effective_from': effective_from.isoformat() if effective_from else None,
                'effective_to': effective_to.isoformat() if effective_to else None,
                'status': status,
                'is_outsource_enabled': _bool(_cell(row, 'IsOutsourceEnabled'), False),
                'is_active': _bool(_cell(row, 'IsActive'), status != 'INACTIVE'),
            }
            report.outcomes.append(RowOutcome(
                index, 'ready', 'Ready to create',
                {'TariffName': name, 'Centre': centre_code or '—', 'Status': status},
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'TariffName': _cell(row, 'TariffName')}))
    return report.summarize()


def apply_tariffs(payloads, *, actor=None) -> dict:
    created = 0
    with transaction.atomic():
        for payload in payloads:
            tariff = Tariff(
                name=payload['name'],
                centre_id=payload.get('centre_id'),
                effective_from=date.fromisoformat(payload['effective_from']) if payload.get('effective_from') else timezone.localdate(),
                effective_to=date.fromisoformat(payload['effective_to']) if payload.get('effective_to') else None,
                status=payload.get('status') or 'DRAFT',
                is_outsource_enabled=bool(payload.get('is_outsource_enabled')),
                is_active=bool(payload.get('is_active', True)),
                created_by=actor if getattr(actor, 'is_authenticated', False) else None,
                modified_by=actor if getattr(actor, 'is_authenticated', False) else None,
            )
            tariff.save()
            created += 1
    return {'created': created}


# --- Tariff prices (lab) -------------------------------------------------

def validate_tariff_prices(headers, rows, *, actor=None) -> ValidationReport:
    dataset = get_dataset('tariff_prices')
    missing = _check_headers(dataset, headers)
    report = ValidationReport('tariff_prices', headers_ok=not missing, missing_headers=missing, outcomes=[])
    if missing:
        return report.summarize()
    seen = set()
    for index, row in enumerate(rows, start=2):
        try:
            tariff_name = _cell(row, 'TariffName')
            test_name = normalize_test_name(_cell(row, 'TestName'))
            test_code = _cell(row, 'TestCode').upper()
            if not tariff_name:
                raise ValueError('TariffName is required.')
            if not test_name and not test_code:
                raise ValueError('TestName or TestCode is required.')
            tariff = Tariff.objects.filter(name__iexact=tariff_name).first()
            if tariff is None:
                raise ValueError(f'Tariff "{tariff_name}" was not found.')
            test = None
            if test_code:
                test = Test.objects.filter(code__iexact=test_code).first()
            if test is None and test_name:
                test = Test.objects.filter(name__iexact=test_name).first()
            if test is None:
                raise ValueError('Test was not found. Upload lab tests first, then prices.')
            price = _decimal(_cell(row, 'Price'))
            if price is None:
                raise ValueError('Price is required.')
            if price < 0:
                raise ValueError('Price cannot be negative.')
            key = f'{tariff.pk}|{test.pk}'
            if key in seen:
                report.outcomes.append(RowOutcome(index, 'duplicate', 'Duplicate tariff/test in this file.', {'TariffName': tariff_name, 'Test': test.code}))
                continue
            seen.add(key)
            outsource = _decimal(_cell(row, 'OutsourcePrice'), Decimal('0'))
            percentage = _decimal(_cell(row, 'Percentage'), Decimal('0'))
            existing = TestPrice.objects.filter(tariff=tariff, test=test).first()
            payload = {
                'tariff_id': tariff.pk,
                'test_id': test.pk,
                'price': str(price),
                'outsource_price': str(outsource or 0),
                'percentage': str(percentage or 0),
                'update_existing': bool(existing),
            }
            msg = 'Will update existing price' if existing else 'Ready to create price'
            report.outcomes.append(RowOutcome(
                index, 'ready', msg,
                {'Tariff': tariff.name, 'Test': test.code, 'TestName': test.name, 'Price': str(price)},
                payload,
            ))
        except Exception as exc:
            report.outcomes.append(RowOutcome(index, 'error', str(exc), {'TariffName': _cell(row, 'TariffName'), 'TestName': _cell(row, 'TestName')}))
    return report.summarize()


def apply_tariff_prices(payloads, *, actor=None) -> dict:
    created = 0
    updated = 0
    with transaction.atomic():
        for payload in payloads:
            _, was_created = TestPrice.objects.update_or_create(
                tariff_id=payload['tariff_id'], test_id=payload['test_id'],
                defaults={
                    'price': Decimal(payload['price']),
                    'outsource_price': Decimal(payload.get('outsource_price') or '0'),
                    'percentage': Decimal(payload.get('percentage') or '0'),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return {'created': created, 'updated': updated}


# --- Registry ------------------------------------------------------------

register(Dataset(
    key='lab_tests',
    label='Laboratory tests',
    description='Upload test names (codes auto-assigned). Optional Price + TariffName create a price line.',
    columns=[
        Column('TestName', True, 'Required. Unique test name.'),
        Column('ServiceGroupName', False, 'Preferred. Created if missing.'),
        Column('ServiceGroupCode', False, 'Single digit 0-9 when creating a group.'),
        Column('Department', False, 'Defaults to PATHOLOGY.'),
        Column('DepartmentCode', False, 'Single digit. Defaults to 1.'),
        Column('SampleVolume', False),
        Column('CollectionRequirements', False),
        Column('HandlingInstructions', False),
        Column('IsOutsourceable', False, 'yes/no'),
        Column('CommentsRequired', False, 'yes/no'),
        Column('IsActive', False, 'yes/no — default yes'),
        Column('TariffName', False, 'Required only when Price is set.'),
        Column('Price', False, 'Optional lab price on the named tariff.'),
    ],
    sample_rows=[{
        'TestName': 'Full blood count', 'ServiceGroupName': 'Haematology', 'ServiceGroupCode': '1',
        'Department': 'HAEMATOLOGY', 'DepartmentCode': '1', 'SampleVolume': '3 mL',
        'IsActive': 'yes', 'TariffName': '', 'Price': '',
    }],
    validate_rows=validate_lab_tests,
    apply_rows=apply_lab_tests,
))

register(Dataset(
    key='imaging_procedures',
    label='Radiology procedures',
    description='Upload imaging procedure names. Codes auto-assigned when blank. ModalityType required.',
    columns=[
        Column('ProcedureName', True),
        Column('ModalityType', True, 'XRAY, CT, MRI, US, MAMMO, ECG, or OTHER'),
        Column('Code', False, 'Leave blank to auto-assign.'),
        Column('IsActive', False),
        Column('TariffName', False),
        Column('Price', False),
    ],
    sample_rows=[{
        'ProcedureName': 'Chest X-ray PA', 'ModalityType': 'XRAY', 'Code': '', 'IsActive': 'yes',
        'TariffName': '', 'Price': '',
    }],
    validate_rows=validate_imaging,
    apply_rows=apply_imaging,
))

register(Dataset(
    key='referral_doctors',
    label='Referring doctors',
    description='Upload referring physicians. DoctorCode auto-assigned when blank.',
    columns=[
        Column('FirstName', True),
        Column('LastName', True),
        Column('CentreCode', True, 'Existing centre code, e.g. NWS'),
        Column('DoctorCode', False, 'Leave blank to auto-assign.'),
        Column('Title', False),
        Column('FacilityName', False),
        Column('Phone', False),
        Column('Email', False),
        Column('CommissionType', False, 'PERCENT or FIXED — default PERCENT'),
        Column('CommissionValue', False, 'Default 0'),
        Column('BankName', False),
        Column('AccountName', False),
        Column('AccountNumber', False),
        Column('Institutions', False, 'Pipe-separated clinic names'),
        Column('AppearOnReports', False),
        Column('IsActive', False),
    ],
    sample_rows=[{
        'FirstName': 'Ada', 'LastName': 'Okoro', 'CentreCode': 'NWS', 'DoctorCode': '',
        'Title': 'Dr', 'FacilityName': 'City Clinic', 'CommissionType': 'PERCENT',
        'CommissionValue': '10', 'Institutions': 'City Clinic|General Hospital',
    }],
    validate_rows=validate_doctors,
    apply_rows=apply_doctors,
))

register(Dataset(
    key='clients',
    label='Organisation clients',
    description='Upload Credit / On Payment / Default clients for a centre.',
    columns=[
        Column('ClientName', True),
        Column('CentreCode', True),
        Column('ClientTypeCode', True, 'CREDIT, ON_PAYMENT, or DEFAULT'),
        Column('ClientCode', False),
        Column('TariffName', False),
        Column('Phone', False),
        Column('ContactPerson', False),
        Column('Email', False),
        Column('CreditLimit', False),
        Column('Address', False),
        Column('City', False),
        Column('State', False),
        Column('ShowTestPrices', False),
        Column('IsActive', False),
    ],
    sample_rows=[{
        'ClientName': 'Acme Health Ltd', 'CentreCode': 'NWS', 'ClientTypeCode': 'CREDIT',
        'TariffName': '', 'CreditLimit': '500000', 'ShowTestPrices': 'no',
    }],
    validate_rows=validate_clients,
    apply_rows=apply_clients,
))

register(Dataset(
    key='tariffs',
    label='Tariffs (master)',
    description='Create tariff shells. Use Tariff prices dataset (or lab test Price column) for line prices.',
    columns=[
        Column('TariffName', True),
        Column('CentreCode', False),
        Column('EffectiveFrom', False, 'YYYY-MM-DD'),
        Column('EffectiveTo', False, 'YYYY-MM-DD'),
        Column('Status', False, 'DRAFT, APPROVAL, or INACTIVE'),
        Column('IsOutsourceEnabled', False),
        Column('IsActive', False),
    ],
    sample_rows=[{
        'TariffName': 'Walk-in 2026', 'CentreCode': 'NWS', 'EffectiveFrom': '2026-01-01',
        'Status': 'APPROVAL', 'IsActive': 'yes',
    }],
    validate_rows=validate_tariffs,
    apply_rows=apply_tariffs,
))

register(Dataset(
    key='tariff_prices',
    label='Tariff prices (laboratory)',
    description='Attach prices to existing tests on an existing tariff (by TestName or TestCode).',
    columns=[
        Column('TariffName', True),
        Column('TestName', False, 'Required if TestCode blank.'),
        Column('TestCode', False, 'Required if TestName blank.'),
        Column('Price', True),
        Column('OutsourcePrice', False),
        Column('Percentage', False),
    ],
    sample_rows=[{
        'TariffName': 'Walk-in 2026', 'TestName': 'Full blood count', 'TestCode': '', 'Price': '5500',
        'OutsourcePrice': '0', 'Percentage': '0',
    }],
    validate_rows=validate_tariff_prices,
    apply_rows=apply_tariff_prices,
))
