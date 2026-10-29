"""Per-visit enterprise audit trail: every significant action on one registration."""
from __future__ import annotations

from django.db.models import Q

from core.audit import record_audit
from core.models import AuditEvent, ResultLedger, UserCentreAccess


ACTION_TITLES = {
    'PATIENT_REGISTERED': 'Patient registered',
    'VISIT_OPENED': 'Visit opened',
    'DOCUMENT_FILED': 'Request form / document filed',
    'PAYMENT_RECORDED': 'Payment recorded',
    'PAYMENT_REVERSED': 'Payment reversed',
    'DISCOUNT_REQUESTED': 'Discount requested',
    'DISCOUNT_APPROVED': 'Discount approved',
    'DISCOUNT_REJECTED': 'Discount rejected',
    'INVOICE_CANCELLED': 'Invoice cancelled',
    'BILL_AMENDMENT': 'Bill amended',
    'REFUND_ISSUED': 'Refund issued',
    'PATIENT_DEMOGRAPHICS_UPDATED': 'Patient demographics modified',
    'RADIOLOGY_RADIOLOGIST_ASSIGNED': 'Radiologist assigned',
    'RADIOLOGY_EXAM_ASSIGNED': 'Radiographer assigned',
    'RADIOLOGY_STUDY_ACCEPTED': 'Radiologist accepted study',
    'RADIOLOGY_REPORT_FINALIZED': 'Radiology report signed (reported)',
    'RADIOLOGY_REPORT_AMENDED': 'Radiology report amended',
    'RESULT_APPROVE': 'Result approved',
    'RESULT_DISPATCH': 'Result dispatched',
    'RESULT_MODIFY': 'Result returned for modification',
    'LAB_RESULT_REJECTED': 'Laboratory result rejected',
}


def user_may_view_visit(user, visit):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    return UserCentreAccess.objects.filter(
        user=user, centre=visit.centre, centre__is_active=True, can_view=True,
    ).exists()


def record_visit_audit(
    request,
    visit,
    *,
    action,
    object_type='Visit',
    object_id=None,
    detail=None,
    previous_values=None,
    new_values=None,
    reason='',
    category=None,
    order_ref='',
    result_ref='',
):
    """Write one visit-scoped audit row with IP, browser, and network location."""
    patient = getattr(visit, 'patient', None)
    return record_audit(
        request=request,
        actor=getattr(request, 'user', None),
        action=action,
        object_type=object_type,
        object_id=object_id if object_id is not None else str(visit.pk),
        centre=visit.centre,
        patient_ref=getattr(patient, 'uhid', '') or '',
        visit_ref=visit.visit_id,
        order_ref=order_ref,
        result_ref=result_ref,
        previous_values=previous_values,
        new_values=new_values,
        reason=reason,
        detail=detail or {},
        category=category,
    )


def _actor_name(user):
    if not user:
        return 'System'
    return user.display_name or user.get_full_name() or user.username


def _format_map(values):
    if not values:
        return []
    rows = []
    for key, value in values.items():
        if isinstance(value, dict) and 'from' in value and 'to' in value:
            rows.append(f"{key}: {value.get('from')!s} → {value.get('to')!s}")
        else:
            rows.append(f'{key}: {value}')
    return rows


def _event(
    *,
    when,
    action,
    title,
    summary='',
    actor='',
    department='',
    ip='',
    browser='',
    location='',
    path='',
    method='',
    reason='',
    previous=None,
    new=None,
    detail=None,
    source='audit',
    object_type='',
    object_id='',
):
    return {
        'when': when,
        'action': action,
        'title': title,
        'summary': summary,
        'actor': actor,
        'department': department,
        'ip_address': ip or '',
        'browser': browser or '',
        'location_hint': location or '',
        'path': path or '',
        'method': method or '',
        'reason': reason or '',
        'previous_values': previous or {},
        'new_values': new or {},
        'previous_lines': _format_map(previous or {}),
        'new_lines': _format_map(new or {}),
        'detail': detail or {},
        'detail_lines': _format_map(detail or {}) if isinstance(detail, dict) else [],
        'source': source,
        'object_type': object_type,
        'object_id': str(object_id or ''),
    }


def build_visit_audit_trail(user, visit):
    """Full chronological trail for one visit registration."""
    if not user_may_view_visit(user, visit):
        return []

    events = []
    visit_id = visit.visit_id
    uhid = visit.patient.uhid

    # Seed registration moment when an early audit row is missing.
    events.append(_event(
        when=visit.created_at,
        action='VISIT_CREATED',
        title='Registration record created',
        summary=f'Visit {visit_id} opened at {visit.centre.code}',
        actor=_actor_name(visit.registered_by),
        department=getattr(visit.registered_by, 'department', '') or '',
        detail={
            'visit_id': visit_id,
            'uhid': uhid,
            'status': visit.status,
            'referral_source': visit.get_referral_source_display(),
        },
        source='visit',
        object_type='Visit',
        object_id=visit.pk,
    ))

    audit_qs = AuditEvent.objects.filter(
        Q(visit_ref__iexact=visit_id)
        | Q(object_type='Visit', object_id=str(visit.pk))
        | Q(detail__visit_id=visit_id)
    ).exclude(object_type='HTTP_REQUEST').select_related('actor', 'centre', 'approval_by').order_by('created_at')

    # Also include patient demographic changes tied to this UHID after the visit opened.
    demo_qs = AuditEvent.objects.filter(
        action='PATIENT_DEMOGRAPHICS_UPDATED',
        patient_ref__iexact=uhid,
        created_at__gte=visit.created_at,
    ).select_related('actor', 'centre').order_by('created_at')

    seen_ids = set()
    for row in list(audit_qs) + list(demo_qs):
        if row.pk in seen_ids:
            continue
        seen_ids.add(row.pk)
        title = ACTION_TITLES.get(row.action, row.action.replace('_', ' ').title())
        summary_bits = []
        if row.reason:
            summary_bits.append(row.reason)
        if isinstance(row.detail, dict):
            for key in ('invoice', 'receipt', 'radiologist', 'radiographer', 'status', 'note', 'reason'):
                if row.detail.get(key):
                    summary_bits.append(f'{key}: {row.detail[key]}')
        events.append(_event(
            when=row.created_at,
            action=row.action,
            title=title,
            summary=' · '.join(str(bit) for bit in summary_bits),
            actor=_actor_name(row.actor),
            department=row.department or getattr(row.actor, 'department', '') or '',
            ip=row.ip_address or '',
            browser=row.browser,
            location=row.location_hint or (row.detail or {}).get('location_hint', ''),
            path=row.path,
            method=row.method,
            reason=row.reason,
            previous=row.previous_values,
            new=row.new_values,
            detail=row.detail,
            source='audit',
            object_type=row.object_type,
            object_id=row.object_id,
        ))

    # Radiology exam activity (assignment, accept miss / return, acquire, etc.)
    try:
        from radiology.models import RadiologyExamActivity
        activities = RadiologyExamActivity.objects.filter(
            exam__order__visit=visit,
        ).select_related('performed_by', 'exam__order').order_by('created_at')
        for row in activities:
            events.append(_event(
                when=row.created_at,
                action=row.action,
                title=f'Radiology · {row.action.replace("_", " ").title()}',
                summary=row.note or f'{row.from_status} → {row.to_status}'.strip(' →'),
                actor=_actor_name(row.performed_by),
                ip=row.ip_address or '',
                browser=row.browser,
                detail={'from_status': row.from_status, 'to_status': row.to_status, 'note': row.note},
                previous={'status': row.from_status} if row.from_status else {},
                new={'status': row.to_status} if row.to_status else {},
                source='radiology',
                object_type='RadiologyExamActivity',
                object_id=row.pk,
            ))
    except Exception:
        pass

    # Laboratory sample activity and result reviews (including rejections)
    try:
        from laboratory.models import ResultReview, SampleActivity
        samples = SampleActivity.objects.filter(
            sample__order__visit=visit,
        ).select_related('performed_by', 'sample').order_by('created_at')
        for row in samples:
            events.append(_event(
                when=row.created_at,
                action=row.action,
                title=f'Laboratory · {row.action.replace("_", " ").title()}',
                summary=row.note or f'{row.from_status} → {row.to_status}'.strip(' →'),
                actor=_actor_name(row.performed_by),
                ip=getattr(row, 'ip_address', '') or '',
                browser=getattr(row, 'browser', '') or '',
                detail={'sample_id': row.sample.sample_id, 'from_status': row.from_status, 'to_status': row.to_status, 'note': row.note},
                previous={'status': row.from_status} if row.from_status else {},
                new={'status': row.to_status} if row.to_status else {},
                source='laboratory',
                object_type='SampleActivity',
                object_id=row.pk,
            ))
        reviews = ResultReview.objects.filter(
            result__sample__order__visit=visit,
        ).select_related('reviewer', 'result').order_by('created_at')
        for row in reviews:
            title = f'Laboratory result · {row.action.title()}'
            if row.action == 'REJECTED':
                title = 'Laboratory result rejected'
            events.append(_event(
                when=row.created_at,
                action=f'LAB_RESULT_{row.action}',
                title=title,
                summary=row.note or '',
                actor=_actor_name(row.reviewer),
                reason=row.note if row.action == 'REJECTED' else '',
                detail={'note': row.note, 'snapshot': row.snapshot} if row.snapshot else {'note': row.note},
                source='laboratory',
                object_type='ResultReview',
                object_id=row.pk,
            ))
    except Exception:
        pass

    # Result ledger (approve / modify / dispatch, including repeat dispatches)
    for row in ResultLedger.objects.filter(visit=visit).select_related('actor').order_by('created_at'):
        events.append(_event(
            when=row.created_at,
            action=f'RESULT_{row.kind}',
            title=ACTION_TITLES.get(f'RESULT_{row.kind}', f'Result {row.kind.title()}'),
            summary=row.summary or row.reason,
            actor=_actor_name(row.actor),
            reason=row.reason,
            detail={'service': row.service, 'summary': row.summary, 'object_type': row.object_type, 'object_id': row.object_id},
            source='ledger',
            object_type=row.object_type,
            object_id=row.object_id,
        ))

    # Billing domain rows that may predate richer audit fields
    from billing.models import DiscountRequest, Invoice, Payment
    for invoice in Invoice.objects.filter(visit=visit).order_by('created_at'):
        events.append(_event(
            when=invoice.created_at,
            action='INVOICE',
            title=f'Invoice {invoice.invoice_number}',
            summary=f'{invoice.get_status_display()} · Total ₦{invoice.total:.2f}',
            detail={'invoice': invoice.invoice_number, 'status': invoice.status, 'total': str(invoice.total)},
            source='billing',
            object_type='Invoice',
            object_id=invoice.pk,
        ))
    for payment in Payment.objects.filter(invoice__visit=visit).select_related('received_by', 'reversed_by').order_by('created_at'):
        events.append(_event(
            when=payment.created_at,
            action='PAYMENT',
            title=f'Payment {payment.receipt_number}',
            summary=f'₦{payment.amount:.2f} · {payment.get_method_display()} · {payment.status}',
            actor=_actor_name(payment.received_by),
            detail={'receipt': payment.receipt_number, 'amount': str(payment.amount), 'method': payment.method, 'status': payment.status},
            source='billing',
            object_type='Payment',
            object_id=payment.pk,
        ))
        if payment.status == 'REVERSED':
            events.append(_event(
                when=payment.reversed_at or payment.updated_at,
                action='PAYMENT_REVERSED',
                title=f'Payment {payment.receipt_number} reversed',
                summary=payment.reversal_reason or '',
                actor=_actor_name(payment.reversed_by),
                reason=payment.reversal_reason or '',
                detail={'receipt': payment.receipt_number, 'amount': str(payment.amount)},
                source='billing',
                object_type='Payment',
                object_id=payment.pk,
            ))
    for discount in DiscountRequest.objects.filter(invoice__visit=visit).select_related('requested_by', 'reviewed_by').order_by('created_at'):
        events.append(_event(
            when=discount.created_at,
            action=f'DISCOUNT_{discount.status}',
            title=f'Discount {discount.status.lower()}',
            summary=f'{discount.discount_type} {discount.requested_value} · {discount.reason}',
            actor=_actor_name(discount.reviewed_by or discount.requested_by),
            reason=discount.reason or discount.review_note or '',
            detail={
                'invoice': discount.invoice.invoice_number,
                'status': discount.status,
                'requested_value': str(discount.requested_value),
                'review_note': discount.review_note,
            },
            source='billing',
            object_type='DiscountRequest',
            object_id=discount.pk,
        ))

    for document in visit.documents.select_related('uploaded_by').order_by('created_at'):
        events.append(_event(
            when=document.created_at,
            action='DOCUMENT_FILED',
            title=f'{document.get_document_type_display()} filed',
            actor=_actor_name(document.uploaded_by),
            detail={'document_type': document.document_type, 'file': document.file.name if document.file else ''},
            source='document',
            object_type='PatientDocument',
            object_id=document.pk,
        ))

    # De-duplicate near-identical seeded rows (same action + second)
    events.sort(key=lambda item: (item['when'], item['action'], item['title']))
    deduped = []
    seen_keys = set()
    for item in events:
        key = (
            item['action'],
            item['when'].replace(microsecond=0) if item['when'] else None,
            item['title'],
            item.get('summary', '')[:80],
            item.get('object_id', ''),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: item['when'], reverse=True)
    return deduped
