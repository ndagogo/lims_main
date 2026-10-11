"""Patient-scoped timeline built from visits, billing, documents, and audit events."""
from django.db.models import Q

from billing.models import DiscountRequest, Invoice, Payment
from core.models import AuditEvent, UserCentreAccess
from patients.models import PatientDocument, Visit


def _actor_name(user):
    if not user:
        return 'System'
    return user.display_name or user.get_full_name() or user.username


def _visible_visits(user, patient):
    visits = Visit.objects.filter(patient=patient).select_related('centre', 'registered_by', 'client').order_by('-created_at')
    if user.is_superuser:
        return visits
    return visits.filter(
        centre__user_access__user=user,
        centre__user_access__can_view=True,
        centre__is_active=True,
    ).distinct()


def build_patient_timeline(user, patient):
    visits = list(_visible_visits(user, patient))
    visit_ids = [visit.pk for visit in visits]
    events = []

    for visit in visits:
        events.append({
            'when': visit.created_at,
            'action': 'VISIT_OPENED',
            'title': f'Visit {visit.visit_id} opened',
            'detail': f'{visit.centre.code} · {visit.get_referral_source_display()} · {visit.status}',
            'actor': _actor_name(visit.registered_by),
            'centre': visit.centre.code,
        })

    invoices = Invoice.objects.filter(visit_id__in=visit_ids).select_related('visit__centre').order_by('-created_at')
    for invoice in invoices:
        events.append({
            'when': invoice.created_at,
            'action': 'INVOICE',
            'title': f'Invoice {invoice.invoice_number}',
            'detail': f'Status {invoice.get_status_display()} · Total ₦{invoice.total:.2f}',
            'actor': '',
            'centre': invoice.visit.centre.code,
        })

    payments = Payment.objects.filter(invoice__visit_id__in=visit_ids).select_related(
        'invoice__visit__centre', 'received_by', 'reversed_by',
    ).order_by('-created_at')
    for payment in payments:
        if payment.status == 'REVERSED':
            events.append({
                'when': payment.reversed_at or payment.updated_at,
                'action': 'PAYMENT_REVERSED',
                'title': f'Payment {payment.receipt_number} reversed',
                'detail': payment.reversal_reason or f'₦{payment.amount:.2f}',
                'actor': _actor_name(payment.reversed_by),
                'centre': payment.invoice.visit.centre.code,
            })
        events.append({
            'when': payment.created_at,
            'action': 'PAYMENT',
            'title': f'Payment {payment.receipt_number}',
            'detail': f'₦{payment.amount:.2f} · {payment.get_method_display()} · {payment.status}',
            'actor': _actor_name(payment.received_by),
            'centre': payment.invoice.visit.centre.code,
        })

    discounts = DiscountRequest.objects.filter(invoice__visit_id__in=visit_ids).select_related(
        'invoice__visit__centre', 'requested_by', 'reviewed_by',
    ).order_by('-created_at')
    for discount in discounts:
        events.append({
            'when': discount.created_at,
            'action': f'DISCOUNT_{discount.status}',
            'title': f'Discount {discount.status.lower()} on {discount.invoice.invoice_number}',
            'detail': f'{discount.discount_type} {discount.requested_value} · {discount.reason}',
            'actor': _actor_name(discount.reviewed_by or discount.requested_by),
            'centre': discount.invoice.visit.centre.code,
        })

    documents = PatientDocument.objects.filter(visit_id__in=visit_ids).select_related(
        'visit__centre', 'uploaded_by',
    ).order_by('-created_at')
    for document in documents:
        events.append({
            'when': document.created_at,
            'action': 'DOCUMENT_FILED',
            'title': f'{document.get_document_type_display()} filed',
            'detail': f'On visit {document.visit.visit_id}',
            'actor': _actor_name(document.uploaded_by),
            'centre': document.visit.centre.code,
        })

    object_ids = {str(patient.pk), *(str(pk) for pk in visit_ids)}
    object_ids.update(str(pk) for pk in invoices.values_list('pk', flat=True))
    object_ids.update(str(pk) for pk in payments.values_list('pk', flat=True))
    object_ids.update(str(pk) for pk in discounts.values_list('pk', flat=True))

    audit_qs = AuditEvent.objects.filter(
        Q(object_id__in=object_ids) | Q(detail__uhid=patient.uhid),
    ).select_related('actor', 'centre').order_by('-created_at')
    if not user.is_superuser:
        centre_ids = UserCentreAccess.objects.filter(
            user=user, centre__is_active=True, can_view=True,
        ).values_list('centre_id', flat=True)
        audit_qs = audit_qs.filter(Q(centre_id__in=centre_ids) | Q(centre__isnull=True))
    audit_qs = audit_qs[:300]

    seen = {(row['action'], row['when'], row['title']) for row in events}
    for row in audit_qs:
        title = row.action.replace('_', ' ').title()
        key = (row.action, row.created_at, title)
        if key in seen:
            continue
        detail_bits = []
        if isinstance(row.detail, dict):
            for key_name in ('visit_id', 'invoice', 'receipt', 'uhid', 'reason', 'status'):
                if row.detail.get(key_name):
                    detail_bits.append(f'{key_name.replace("_", " ")}: {row.detail[key_name]}')
        events.append({
            'when': row.created_at,
            'action': row.action,
            'title': title,
            'detail': ' · '.join(detail_bits) or row.object_type,
            'actor': _actor_name(row.actor),
            'centre': row.centre.code if row.centre_id else '',
        })

    events.sort(key=lambda item: item['when'], reverse=True)
    return events
