"""CSV / Excel tariff download and upload against the required template headers."""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import Workbook, load_workbook

from laboratory.models import Tariff, TariffActivity, Test, TestPrice


REQUIRED_HEADERS = ['TariffCode', 'TariffName', 'ServiceCode', 'ServicePrice']


def _normalize_header(value) -> str:
    return str(value or '').strip()


def export_tariff_rows(tariff: Tariff):
    rows = []
    for price in TestPrice.objects.filter(tariff=tariff).select_related('test').order_by('test__code'):
        rows.append({
            'TariffCode': tariff.code,
            'TariffName': tariff.name,
            'ServiceCode': price.test.code,
            'ServicePrice': f'{price.price:.2f}',
        })
    return rows


def build_csv_bytes(tariff: Tariff) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REQUIRED_HEADERS)
    writer.writeheader()
    for row in export_tariff_rows(tariff):
        writer.writerow(row)
    return buffer.getvalue().encode('utf-8-sig')


def build_xlsx_bytes(tariff: Tariff) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = 'Tariff Data'
    sheet.append(REQUIRED_HEADERS)
    for row in export_tariff_rows(tariff):
        sheet.append([row[h] for h in REQUIRED_HEADERS])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _read_uploaded_rows(uploaded_file):
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        book = load_workbook(uploaded_file, data_only=True)
        sheet = book.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValidationError('The uploaded workbook is empty.')
        headers = [_normalize_header(cell) for cell in rows[0]]
        body = rows[1:]
        dict_rows = []
        for raw in body:
            if raw is None or all(cell is None or str(cell).strip() == '' for cell in raw):
                continue
            item = {}
            for idx, header in enumerate(headers):
                item[header] = '' if idx >= len(raw) or raw[idx] is None else str(raw[idx]).strip()
            dict_rows.append(item)
        return headers, dict_rows
    # CSV / text
    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        text = raw.decode('utf-8-sig')
    else:
        text = str(raw)
    reader = csv.DictReader(io.StringIO(text))
    headers = [_normalize_header(h) for h in (reader.fieldnames or [])]
    dict_rows = []
    for row in reader:
        cleaned = {_normalize_header(k): str(v or '').strip() for k, v in row.items()}
        if not any(cleaned.values()):
            continue
        dict_rows.append(cleaned)
    return headers, dict_rows


def validate_and_apply_upload(*, tariff: Tariff, uploaded_file, actor=None):
    """
    Compare uploaded sheet with system requirements.
    On success, replace lab prices for this tariff with the uploaded rows.
    """
    headers, rows = _read_uploaded_rows(uploaded_file)
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        raise ValidationError(
            f'Upload failed: missing required header(s): {", ".join(missing)}. '
            f'Expected exactly {", ".join(REQUIRED_HEADERS)}.'
        )
    if not rows:
        raise ValidationError('Upload failed: the file has headers but no tariff rows.')

    errors = []
    parsed = []
    seen_codes = set()
    for index, row in enumerate(rows, start=2):
        tariff_code = row.get('TariffCode', '').strip()
        tariff_name = row.get('TariffName', '').strip()
        service_code = row.get('ServiceCode', '').strip().upper()
        service_price = row.get('ServicePrice', '').strip().replace(',', '')
        if tariff_code and tariff_code != tariff.code:
            errors.append(f'Row {index}: TariffCode "{tariff_code}" does not match {tariff.code}.')
        if tariff_name and tariff_name.casefold() != tariff.name.casefold():
            errors.append(f'Row {index}: TariffName "{tariff_name}" does not match {tariff.name}.')
        if not service_code:
            errors.append(f'Row {index}: ServiceCode is required.')
            continue
        if service_code in seen_codes:
            errors.append(f'Row {index}: duplicate ServiceCode {service_code}.')
            continue
        seen_codes.add(service_code)
        test = Test.objects.filter(code__iexact=service_code, is_active=True).first()
        if test is None:
            errors.append(f'Row {index}: ServiceCode {service_code} is not an active test in the system.')
            continue
        try:
            price = Decimal(service_price)
        except (InvalidOperation, ValueError):
            errors.append(f'Row {index}: ServicePrice "{service_price}" is not a valid number.')
            continue
        if price < 0:
            errors.append(f'Row {index}: ServicePrice cannot be negative.')
            continue
        parsed.append((test, price))

    if errors:
        raise ValidationError('Upload failed:\n' + '\n'.join(errors[:40]))

    with transaction.atomic():
        TestPrice.objects.filter(tariff=tariff).delete()
        TestPrice.objects.bulk_create([
            TestPrice(tariff=tariff, test=test, price=price, outsource_price=Decimal('0'), percentage=Decimal('0'))
            for test, price in parsed
        ])
        tariff.modified_by = actor
        tariff.version = (tariff.version or 1) + 1
        tariff.save(update_fields=['modified_by', 'version', 'updated_at'])
        TariffActivity.objects.create(
            tariff=tariff,
            tariff_code=tariff.code,
            tariff_name=tariff.name,
            action='UPLOADED',
            detail={'rows': len(parsed)},
            actor=actor,
        )
    return len(parsed)
