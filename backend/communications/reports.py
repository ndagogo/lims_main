from datetime import timedelta
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from billing.models import DiscountRequest, Invoice, Payment
from billing.visibility import settled
from laboratory.models import LabOrder, Sample
from patients.models import Visit
from radiology.models import ImagingOrder


def visible_centre_ids(user):
    """None means every centre. A list means only centres this user may view."""
    if user.is_superuser:
        return None
    return list(user.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True))


def _pipeline(rows, choices):
    counts = {row['status']: row['count'] for row in rows}
    return [{'status': code, 'label': label, 'count': counts.get(code, 0)} for code, label in choices]


def build_operations_summary(user):
    start = timezone.now() - timedelta(days=30)
    centre_ids = visible_centre_ids(user)
    visits = Visit.objects.filter(created_at__gte=start)
    orders = LabOrder.objects.filter(created_at__gte=start)
    samples = settled(Sample.objects.filter(created_at__gte=start), 'lab_order_id', 'order_id')
    invoices = Invoice.objects.filter(created_at__gte=start)
    payments = Payment.objects.filter(created_at__gte=start, status='CONFIRMED')
    imaging = settled(ImagingOrder.objects.filter(created_at__gte=start), 'imaging_order_id')
    discounts = DiscountRequest.objects.filter(status='PENDING')
    if centre_ids is not None:
        visits = visits.filter(centre_id__in=centre_ids)
        orders = orders.filter(visit__centre_id__in=centre_ids)
        samples = samples.filter(centre_id__in=centre_ids)
        invoices = invoices.filter(visit__centre_id__in=centre_ids)
        payments = payments.filter(invoice__visit__centre_id__in=centre_ids)
        imaging = imaging.filter(visit__centre_id__in=centre_ids)
        discounts = discounts.filter(invoice__visit__centre_id__in=centre_ids)
    sample_rows = list(samples.values('status').annotate(count=Count('id')).order_by('status'))
    order_rows = list(orders.values('status').annotate(count=Count('id')).order_by('status'))
    imaging_rows = list(imaging.values('status').annotate(count=Count('id')).order_by('status'))
    open_samples = samples.exclude(status__in=['RELEASED', 'CANCELLED', 'REJECTED']).count()
    return {
        'period_days': 30,
        'visits': visits.count(),
        'lab_orders_by_status': order_rows,
        'samples_by_status': sample_rows,
        'sample_pipeline': _pipeline(sample_rows, Sample.STATUS_CHOICES),
        'order_pipeline': _pipeline(order_rows, LabOrder.STATUS_CHOICES),
        'imaging_pipeline': _pipeline(imaging_rows, ImagingOrder.STATUSES),
        'invoice_count': invoices.count(),
        'unpaid_invoices': invoices.filter(status__in=['UNPAID', 'PART_PAID']).count(),
        'pending_discounts': discounts.count(),
        'open_samples': open_samples,
        'released_samples': samples.filter(status='RELEASED').count(),
        'open_imaging': imaging.exclude(status__in=['COMPLETED', 'CANCELLED']).count(),
        'confirmed_payments_ngn': str(payments.aggregate(total=Sum('amount'))['total'] or 0),
        'generated_at': timezone.now().isoformat(),
    }


def build_work_queues(user):
    """Short lists for the operations dashboard. They follow the same centre scope as the summary."""
    centre_ids = visible_centre_ids(user)
    invoices = Invoice.objects.select_related('visit__patient', 'visit__centre').filter(status__in=['UNPAID', 'PART_PAID'])
    samples = settled(Sample.objects.select_related('order__test', 'order__visit__patient', 'centre').filter(
        status__in=['COLLECTED', 'RECEIVED', 'PROCESSING', 'RESULT_ENTERED', 'UNDER_REVIEW', 'AUTHENTICATED']
    ), 'lab_order_id', 'order_id')
    imaging = settled(
        ImagingOrder.objects.select_related('visit__patient', 'visit__centre', 'modality').exclude(status__in=['COMPLETED', 'CANCELLED']),
        'imaging_order_id',
    )
    if centre_ids is not None:
        invoices = invoices.filter(visit__centre_id__in=centre_ids)
        samples = samples.filter(centre_id__in=centre_ids)
        imaging = imaging.filter(visit__centre_id__in=centre_ids)
    from core.models import AuditEvent
    events = AuditEvent.objects.select_related('actor', 'centre')
    if centre_ids is not None:
        events = events.filter(centre_id__in=centre_ids)
    return {
        'unpaid_invoices': invoices.order_by('-created_at'),
        'active_samples': samples.order_by('-updated_at'),
        'open_imaging': imaging.order_by('-created_at'),
        'recent_events': events.order_by('-created_at'),
    }


class OperationsSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_operations_summary(request.user))
