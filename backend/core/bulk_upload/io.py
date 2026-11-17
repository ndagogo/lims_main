"""CSV / Excel read and template builders for bulk catalogue upload."""
from __future__ import annotations

import csv
import io

from django.core.exceptions import ValidationError
from openpyxl import Workbook, load_workbook


def normalize_header(value) -> str:
    return str(value or '').strip()


def read_uploaded_table(uploaded_file):
    """Return (headers, list[dict]) from CSV or Excel."""
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        book = load_workbook(uploaded_file, data_only=True)
        sheet = book.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValidationError('The uploaded workbook is empty.')
        headers = [normalize_header(cell) for cell in rows[0]]
        dict_rows = []
        for raw in rows[1:]:
            if raw is None or all(cell is None or str(cell).strip() == '' for cell in raw):
                continue
            item = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                item[header] = '' if idx >= len(raw) or raw[idx] is None else str(raw[idx]).strip()
            dict_rows.append(item)
        return headers, dict_rows

    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        text = raw.decode('utf-8-sig')
    else:
        text = str(raw)
    reader = csv.DictReader(io.StringIO(text))
    headers = [normalize_header(h) for h in (reader.fieldnames or [])]
    dict_rows = []
    for row in reader:
        cleaned = {normalize_header(k): str(v or '').strip() for k, v in row.items() if normalize_header(k)}
        if not any(cleaned.values()):
            continue
        dict_rows.append(cleaned)
    return headers, dict_rows


def build_template_csv(headers, sample_rows=None) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction='ignore')
    writer.writeheader()
    for row in sample_rows or []:
        writer.writerow({h: row.get(h, '') for h in headers})
    return buffer.getvalue().encode('utf-8-sig')


def build_template_xlsx(headers, sample_rows=None, *, sheet_title='Upload') -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title[:31]
    sheet.append(list(headers))
    for row in sample_rows or []:
        sheet.append([row.get(h, '') for h in headers])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
