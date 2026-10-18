"""Enterprise audit recording, filtering, and report export.

Timestamps are stored in UTC (Django USE_TZ). Display uses Africa/Lagos.
IP and browser strings are metadata only — they do not prove identity or location.
Precise geolocation is intentionally not collected.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, time

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import AuditEvent, Centre

LAGOS = 'Africa/Lagos'
EXPORT_CAP = 10000

ACTION_CATEGORY_HINTS = (
    (('LOGIN', 'LOGOUT', 'PASSWORD', 'MFA', 'AUTH'), 'AUTH'),
    (('ACCESS', 'PERMISSION', 'ROLE', 'USER_', 'CENTRE_ACCESS'), 'ACCESS'),
    (('LAB', 'SAMPLE', 'RESULT', 'RADIOLOGY', 'REPORT', 'EXAM', 'PATIENT', 'VISIT', 'PHLEB'), 'CLINICAL'),
    (('INVOICE', 'PAYMENT', 'REFUND', 'BILL', 'DISCOUNT', 'TARIFF', 'PRICE'), 'BILLING'),
    (('ANALYZER', 'PACS', 'GATEWAY', 'ORTHANC', 'INTEGRATION', 'NOTIFICATION'), 'INTEGRATION'),
    (('HTTP_',), 'SYSTEM'),
    (('CENTRE', 'TARIFF', 'TEST_', 'CATALOG', 'WORKFLOW', 'TEMPLATE', 'PROFILE'), 'ADMIN'),
)


def infer_category(action: str) -> str:
    upper = (action or '').upper()
    for needles, category in ACTION_CATEGORY_HINTS:
        if any(n in upper for n in needles):
            return category
    return 'OTHER'


def _department_for(actor) -> str:
    if actor is None:
        return ''
    return (getattr(actor, 'department', None) or '').strip()[:120]


def record_audit(
    *,
    actor=None,
    action,
    object_type='',
    object_id='',
    request=None,
    centre=None,
    department='',
    patient_ref='',
    visit_ref='',
    order_ref='',
    result_ref='',
    previous_values=None,
    new_values=None,
    reason='',
    approval_by=None,
    approval_note='',
    evidence_ref='',
    evidence_note='',
    detail=None,
    category=None,
    path='',
    method='',
):
    """Write one significant audit event with enterprise metadata."""
    ip = browser = ''
    if request is not None:
        path = path or getattr(request, 'path', '')[:500]
        method = method or getattr(request, 'method', '')[:10]
        ip = getattr(request, 'audit_ip', None) or ''
        browser = (getattr(request, 'audit_browser', '') or '')[:255]
        if actor is None and getattr(request, 'user', None) is not None and request.user.is_authenticated:
            actor = request.user
    if not department and actor is not None:
        department = _department_for(actor)
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, 'is_authenticated', True) else None,
        action=(action or '')[:80],
        category=category or infer_category(action),
        object_type=(object_type or '')[:100],
        object_id=str(object_id or '')[:100],
        path=(path or '')[:500],
        method=(method or '')[:10],
        ip_address=ip or None,
        browser=browser,
        centre=centre,
        department=(department or '')[:120],
        patient_ref=(patient_ref or '')[:80],
        visit_ref=(visit_ref or '')[:80],
        order_ref=(order_ref or '')[:80],
        result_ref=(result_ref or '')[:80],
        previous_values=previous_values or {},
        new_values=new_values or {},
        reason=reason or '',
        approval_by=approval_by,
        approval_note=approval_note or '',
        evidence_ref=(evidence_ref or '')[:255],
        evidence_note=evidence_note or '',
        detail=detail or {},
    )


def filter_audit_events(params):
    """Apply dashboard / export filters from a QueryDict-like mapping."""
    q = (params.get('q') or '').strip()
    username = (params.get('username') or '').strip()
    action = (params.get('action') or '').strip()
    object_type = (params.get('object_type') or '').strip()
    category = (params.get('category') or '').strip().upper()
    centre_id = (params.get('centre') or '').strip()
    department = (params.get('department') or '').strip()
    patient_ref = (params.get('patient') or '').strip()
    visit_ref = (params.get('visit') or '').strip()
    order_ref = (params.get('order') or '').strip()
    result_ref = (params.get('result') or '').strip()
    ip = (params.get('ip') or '').strip()
    date_from = parse_date((params.get('date_from') or '').strip() or '')
    date_to = parse_date((params.get('date_to') or '').strip() or '')
    # QueryDict.get returns the last value when checkbox + hidden are both posted.
    exclude_http = (params.get('exclude_http') or '1') != '0'
    significant = (params.get('significant') or '0') == '1'

    rows = AuditEvent.objects.select_related('actor', 'centre', 'approval_by').order_by('-created_at')
    if exclude_http:
        rows = rows.exclude(object_type='HTTP_REQUEST')
    if significant:
        rows = rows.exclude(object_type='HTTP_REQUEST').exclude(category='SYSTEM')
    if q:
        rows = rows.filter(
            Q(action__icontains=q)
            | Q(object_type__icontains=q)
            | Q(object_id__icontains=q)
            | Q(actor__username__icontains=q)
            | Q(actor__display_name__icontains=q)
            | Q(path__icontains=q)
            | Q(patient_ref__icontains=q)
            | Q(visit_ref__icontains=q)
            | Q(order_ref__icontains=q)
            | Q(result_ref__icontains=q)
            | Q(reason__icontains=q)
            | Q(department__icontains=q)
            | Q(ip_address__icontains=q)
        )
    if username:
        rows = rows.filter(
            Q(actor__username__icontains=username) | Q(actor__display_name__icontains=username)
        )
    if action:
        rows = rows.filter(action__icontains=action)
    if object_type:
        rows = rows.filter(object_type__icontains=object_type)
    if category and category in dict(AuditEvent.CATEGORIES):
        rows = rows.filter(category=category)
    if centre_id:
        rows = rows.filter(centre_id=centre_id)
    if department:
        rows = rows.filter(department__icontains=department)
    if patient_ref:
        rows = rows.filter(patient_ref__icontains=patient_ref)
    if visit_ref:
        rows = rows.filter(visit_ref__icontains=visit_ref)
    if order_ref:
        rows = rows.filter(order_ref__icontains=order_ref)
    if result_ref:
        rows = rows.filter(result_ref__icontains=result_ref)
    if ip:
        rows = rows.filter(ip_address__icontains=ip)
    if date_from:
        start = timezone.make_aware(datetime.combine(date_from, time.min), timezone.get_current_timezone())
        rows = rows.filter(created_at__gte=start)
    if date_to:
        end = timezone.make_aware(datetime.combine(date_to, time.max), timezone.get_current_timezone())
        rows = rows.filter(created_at__lte=end)
    return rows


def format_lagos(dt):
    if not dt:
        return '—'
    try:
        from zoneinfo import ZoneInfo
        local = timezone.localtime(dt, ZoneInfo(LAGOS))
    except Exception:
        local = timezone.localtime(dt)
    return local.strftime('%d %b %Y %H:%M:%S %Z')


def format_utc(dt):
    if not dt:
        return '—'
    from datetime import timezone as dt_timezone
    return dt.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _json_cell(value):
    if not value:
        return ''
    try:
        return json.dumps(value, ensure_ascii=True, default=str)
    except TypeError:
        return str(value)


def audit_row_dict(row: AuditEvent) -> dict:
    return {
        'id': str(row.pk),
        'when_lagos': format_lagos(row.created_at),
        'when_utc': format_utc(row.created_at),
        'actor': getattr(row.actor, 'username', '') or '',
        'actor_display': str(row.actor) if row.actor_id else '',
        'action': row.action,
        'category': row.get_category_display() if row.category else '',
        'object_type': row.object_type,
        'object_id': row.object_id,
        'centre': row.centre.code if row.centre_id else '',
        'department': row.department,
        'patient_ref': row.patient_ref,
        'visit_ref': row.visit_ref,
        'order_ref': row.order_ref,
        'result_ref': row.result_ref,
        'previous_values': _json_cell(row.previous_values),
        'new_values': _json_cell(row.new_values),
        'reason': row.reason,
        'approval_by': getattr(row.approval_by, 'username', '') or '',
        'approval_note': row.approval_note,
        'evidence_ref': row.evidence_ref,
        'evidence_note': row.evidence_note,
        'ip_address': row.ip_address or '',
        'browser': row.browser,
        'path': row.path,
        'method': row.method,
        'detail': _json_cell(row.detail),
    }


EXPORT_COLUMNS = [
    ('when_lagos', 'Timestamp (Africa/Lagos)'),
    ('when_utc', 'Timestamp (UTC)'),
    ('actor', 'Username'),
    ('actor_display', 'Actor display name'),
    ('action', 'Action'),
    ('category', 'Category'),
    ('object_type', 'Object type'),
    ('object_id', 'Object ID'),
    ('centre', 'Centre'),
    ('department', 'Department'),
    ('patient_ref', 'Patient'),
    ('visit_ref', 'Visit'),
    ('order_ref', 'Order'),
    ('result_ref', 'Result'),
    ('previous_values', 'Previous values'),
    ('new_values', 'New values'),
    ('reason', 'Reason'),
    ('approval_by', 'Approved by'),
    ('approval_note', 'Approval note'),
    ('evidence_ref', 'Evidence ref'),
    ('evidence_note', 'Evidence note'),
    ('ip_address', 'Source IP'),
    ('browser', 'Browser / device'),
    ('path', 'Path'),
    ('method', 'Method'),
    ('detail', 'Detail JSON'),
]


def export_audit_csv(queryset):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for row in queryset[:EXPORT_CAP]:
        data = audit_row_dict(row)
        writer.writerow([data[key] for key, _ in EXPORT_COLUMNS])
    response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="hfdn-audit-{_stamp()}.csv"'
    return response


def export_audit_excel(queryset):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = 'Audit trail'
    ws.append([label for _, label in EXPORT_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in queryset[:EXPORT_CAP]:
        data = audit_row_dict(row)
        ws.append([data[key] for key, _ in EXPORT_COLUMNS])
    ws.append([])
    ws.append(['Note: IP and browser metadata do not prove identity or physical location.'])
    ws.append(['Timestamps stored in UTC; Africa/Lagos column is for display.'])
    out = io.BytesIO()
    wb.save(out)
    response = HttpResponse(
        out.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="hfdn-audit-{_stamp()}.xlsx"'
    return response


def export_audit_pdf(queryset):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = [
        Paragraph('HFDN Enterprise Audit Trail', styles['Heading1']),
        Paragraph(
            f'Generated {_stamp()} · Times in Africa/Lagos (UTC stored) · '
            'IP/browser are metadata only and do not prove identity or location.',
            styles['Normal'],
        ),
        Spacer(1, 10),
    ]
    headers = ['When (Lagos)', 'User', 'Action', 'Object', 'Centre', 'Patient/Visit', 'IP']
    data = [headers]
    for row in queryset[:800]:
        refs = ' / '.join(p for p in [row.patient_ref, row.visit_ref] if p) or '—'
        data.append([
            format_lagos(row.created_at),
            getattr(row.actor, 'username', None) or '—',
            (row.action or '')[:28],
            f'{(row.object_type or "")[:16]} {(row.object_id or "")[:10]}'.strip(),
            row.centre.code if row.centre_id else '—',
            refs[:24],
            row.ip_address or '—',
        ])
    table = Table(data, repeatRows=1, colWidths=[110, 70, 95, 110, 50, 90, 70])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#102033')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#c5d0dc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f6fa')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(table)
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="hfdn-audit-{_stamp()}.pdf"'
    return response


def audit_dashboard_stats(queryset):
    capped = queryset
    return {
        'total_matching': capped.count(),
        'by_category': list(
            capped.values('category').annotate(count=Count('id')).order_by('-count')[:8]
        ),
        'by_action': list(
            capped.values('action').annotate(count=Count('id')).order_by('-count')[:8]
        ),
    }


def _stamp():
    return timezone.now().strftime('%Y%m%d-%H%M%S')


def centres_for_filter():
    return Centre.objects.filter(is_active=True).order_by('code')
