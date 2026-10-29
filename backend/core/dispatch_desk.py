"""Result dispatch desk: unified queue, compose form, delivery log, and notifications."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from billing.eligibility import check_visit_release
from communications.messaging import (
    EVENT_RESULT_READY,
    build_patient_context,
    notify_address,
    render_event_message,
)
from communications.models import NotificationOutbox
from communications.services import dispatch_one
from core.models import ResultLedger, UserCentreAccess
from core.result_control import (
    dispatch_laboratory_result,
    dispatch_radiology_report,
    user_can_dispatch_laboratory,
    user_can_dispatch_radiology,
)
from core.paging import PAGE_SIZES, paginate
from radiology.models import RadiologyReport


RECIPIENT_CHIPS = [
    ('self', 'Self'),
    ('family', 'Family/Guardian'),
    ('third_party', 'Third party'),
    ('corporate', 'Corporate Client'),
    ('hmo', 'HMO'),
]

CHANNEL_OPTIONS = [
    ('EMAIL', 'Email'),
    ('WHATSAPP', 'WhatsApp'),
    ('SMS', 'Phone (SMS)'),
    ('PORTAL', 'Patient portal'),
    ('HARDCOPY', 'Hardcopy pickup'),
]

LINK_EXPIRY_CHOICES = [
    ('1', '1 day'),
    ('2', '2 days'),
    ('7', '7 days'),
    ('14', '14 days'),
    ('30', '30 days'),
]


def _centre_ids(user):
    if user.is_superuser:
        return None
    return list(
        UserCentreAccess.objects.filter(
            user=user, centre__is_active=True, can_view=True,
        ).values_list('centre_id', flat=True)
    )


def _scope_visits(qs, centre_ids, visit_field='visit'):
    if centre_ids is None:
        return qs
    return qs.filter(**{f'{visit_field}__centre_id__in': centre_ids})


def _scope_reports(qs, centre_ids):
    if centre_ids is None:
        return qs
    return qs.filter(exam__order__visit__centre_id__in=centre_ids)


def _scope_lab(qs, centre_ids):
    if centre_ids is None:
        return qs
    return qs.filter(sample__order__visit__centre_id__in=centre_ids)


def _patient_label(patient):
    parts = [patient.title, patient.first_name, patient.last_name]
    return ' '.join(part for part in parts if part).strip() or patient.uhid


def _invoice_summary(visit):
    from billing.models import InvoiceItem, Payment
    items = InvoiceItem.objects.filter(
        Q(lab_order__visit=visit) | Q(imaging_order__visit=visit),
    ).select_related('invoice')
    if not items.exists():
        return 'No invoice linked', Decimal('0.00'), True
    invoices = {item.invoice_id: item.invoice for item in items}
    total = sum((inv.total or Decimal('0')) for inv in invoices.values())
    paid = all(inv.status == 'PAID' for inv in invoices.values())
    confirmed = Payment.objects.filter(
        invoice_id__in=invoices.keys(), status='CONFIRMED',
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    source = (visit.get_referral_source_display() if hasattr(visit, 'get_referral_source_display') else visit.referral_source) or 'Self'
    if visit.client_id:
        source = visit.client.name
    elif visit.client_name:
        source = visit.client_name
    status = 'settled' if paid else 'outstanding'
    return f'{source} — {status} ₦{confirmed:,.0f}', confirmed, paid


def _document(visit):
    return visit.documents.filter(document_type__in=['REFERRAL', 'REGISTRATION']).order_by('-created_at').first()


def _doctor(visit):
    from django.core.exceptions import ObjectDoesNotExist
    try:
        return visit.referral_attribution.doctor
    except ObjectDoesNotExist:
        return None


def _ready_radiology(user, centre_ids):
    if not user_can_dispatch_radiology(user):
        return []
    rows = RadiologyReport.objects.select_related(
        'exam__order__visit__patient', 'exam__order__visit__centre', 'exam__order__visit__client',
        'exam__order__modality', 'exam__order__procedure', 'approved_by',
    ).filter(status='APPROVED').order_by('-approved_at')
    rows = _scope_reports(rows, centre_ids)[:200]
    items = []
    for report in rows:
        visit = report.exam.order.visit
        blockers = check_visit_release(visit, check_workflow=False)
        procedure = report.exam.order.procedure.name if report.exam.order.procedure_id else report.exam.order.modality.name
        items.append({
            'key': f'rad:{report.pk}',
            'service': 'RADIOLOGY',
            'pk': str(report.pk),
            'visit': visit,
            'visit_id': visit.visit_id,
            'patient_name': _patient_label(visit.patient),
            'test_label': f'{report.exam.order.modality.get_modality_type_display()} · {procedure}',
            'status_label': 'Ready' if not blockers else (blockers[0].message[:42]),
            'ready': not blockers,
            'blockers': blockers,
            'when': report.approved_at,
            'preview_url': reverse('radiology-report-preview', args=[report.pk]),
            'object': report,
        })
    return items


def _ready_laboratory(user, centre_ids):
    if not user_can_dispatch_laboratory(user):
        return []
    from laboratory.models import LabResult
    rows = LabResult.objects.select_related(
        'sample__order__visit__patient', 'sample__order__visit__centre', 'sample__order__visit__client',
        'sample__order__test', 'authenticated_by',
    ).filter(status='AUTHENTICATED').order_by('-authenticated_at')
    rows = _scope_lab(rows, centre_ids)[:200]
    items = []
    for result in rows:
        visit = result.sample.order.visit
        blockers = check_visit_release(visit)
        items.append({
            'key': f'lab:{result.pk}',
            'service': 'LABORATORY',
            'pk': str(result.pk),
            'visit': visit,
            'visit_id': visit.visit_id,
            'patient_name': _patient_label(visit.patient),
            'test_label': result.sample.order.test.name,
            'status_label': 'Ready' if not blockers else (blockers[0].message[:42]),
            'ready': not blockers,
            'blockers': blockers,
            'when': result.authenticated_at,
            'preview_url': '',
            'object': result,
        })
    return items


def _issue_result_link(patient, hours=7):
    from portals.models import PortalAccessToken
    from django.conf import settings
    token = PortalAccessToken.issue('PATIENT', patient=patient, hours=max(1, int(hours)))
    base = (getattr(settings, 'PUBLIC_APP_URL', '') or '').strip().rstrip('/')
    return f'{base}/portals/patient/access/{token.token}/'


def _queue_recipient(*, centre, visit, channel, recipient, label, user, context, idempotency_key):
    if channel in ('HARDCOPY', 'PORTAL'):
        # Hardcopy is fulfilled at the desk. Portal access is the result_link in other channels.
        return {'channel': channel, 'recipient': recipient or label, 'label': label}
    return notify_address(
        centre=centre, channel=channel, recipient=recipient, event_key=EVENT_RESULT_READY,
        context=context, user=user, visit=visit, idempotency_key=f'{idempotency_key}:{channel}',
    )


def _recipient_targets(visit, selected_chips, post):
    """Build (chip, label, channel, recipient) rows from the compose form."""
    patient = visit.patient
    doctor = _doctor(visit)
    targets = []
    for chip in selected_chips:
        channels = [c.strip().upper() for c in post.getlist(f'channels_{chip}') if c.strip()]
        if chip == 'self':
            for channel in channels:
                if channel == 'EMAIL':
                    targets.append((chip, 'Patient — Self', channel, (patient.email or '').strip()))
                elif channel in ('SMS', 'WHATSAPP'):
                    code = (patient.phone_country_code or '').strip() or '+234'
                    local = (patient.phone_number or '').strip()
                    recipient = local if local.startswith('+') else (f'{code}{local}' if local else '')
                    targets.append((chip, 'Patient — Self', channel, recipient))
                elif channel == 'PORTAL':
                    targets.append((chip, 'Patient — Self', channel, patient.uhid))
                elif channel == 'HARDCOPY':
                    targets.append((chip, 'Patient — Self', channel, 'HARDCOPY'))
            alt = (patient.alt_phone or '').strip()
            if alt and any(c in ('SMS', 'WHATSAPP') for c in channels):
                for channel in channels:
                    if channel in ('SMS', 'WHATSAPP'):
                        targets.append((chip, 'Alternative phone', channel, alt if alt.startswith('+') else alt))
        elif chip == 'family':
            phone = (patient.guardian_phone or '').strip()
            name = (patient.guardian_name or 'Family/Guardian').strip()
            for channel in channels:
                if channel in ('SMS', 'WHATSAPP') and phone:
                    targets.append((chip, name, channel, phone))
                elif channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
        elif chip == 'third_party':
            phone = (post.get('third_party_phone') or '').strip()
            email = (post.get('third_party_email') or '').strip()
            name = (post.get('third_party_name') or 'Third party').strip()
            for channel in channels:
                if channel == 'EMAIL' and email:
                    targets.append((chip, name, channel, email))
                elif channel in ('SMS', 'WHATSAPP') and phone:
                    targets.append((chip, name, channel, phone))
                elif channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
        elif chip in ('corporate', 'hmo'):
            name = visit.client.name if visit.client_id else (visit.client_name or chip.title())
            for channel in channels:
                if channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
                elif channel == 'EMAIL' and (post.get(f'{chip}_email') or '').strip():
                    targets.append((chip, name, channel, post.get(f'{chip}_email').strip()))
                elif channel in ('SMS', 'WHATSAPP') and (post.get(f'{chip}_phone') or '').strip():
                    targets.append((chip, name, channel, post.get(f'{chip}_phone').strip()))
        # Referring physician always available alongside self when checked via channels_physician
    physician_channels = [c.strip().upper() for c in post.getlist('channels_physician') if c.strip()]
    if doctor and physician_channels:
        for channel in physician_channels:
            if channel == 'EMAIL' and (doctor.email or '').strip():
                targets.append(('physician', f'Referring physician — {doctor}', channel, doctor.email.strip()))
            elif channel in ('SMS', 'WHATSAPP') and (doctor.phone or '').strip():
                targets.append(('physician', f'Referring physician — {doctor}', channel, doctor.phone.strip()))
            elif channel == 'HARDCOPY':
                targets.append(('physician', f'Referring physician — {doctor}', channel, 'HARDCOPY'))
    # Dedupe by channel+recipient
    seen = set()
    unique = []
    for row in targets:
        key = (row[2], row[3])
        if not row[3] or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def compose_dispatch(user, post, request=None):
    """Clinical release + queued notifications for the compose form."""
    item_key = (post.get('item') or '').strip()
    selected_chips = [c for c in post.getlist('recipients') if c in dict(RECIPIENT_CHIPS)]
    if 'third_party' in selected_chips and not (post.get('third_party_auth') or '').strip():
        from django.core.exceptions import ValidationError
        raise ValidationError('Third-party recipients require an authorisation note.')
    comment = (post.get('comment') or '').strip()
    hours = int(post.get('link_expiry') or '7')
    if item_key.startswith('rad:'):
        report = get_object_or_404(
            RadiologyReport.objects.select_related(
                'exam__order__visit__patient', 'exam__order__visit__centre', 'exam__order__visit__client',
            ),
            pk=item_key.split(':', 1)[1],
        )
        visit = report.exam.order.visit
        dispatch_radiology_report(user, report, request=request)
        object_id = str(report.pk)
        service = 'RADIOLOGY'
    elif item_key.startswith('lab:'):
        from laboratory.models import LabResult
        result = get_object_or_404(
            LabResult.objects.select_related(
                'sample__order__visit__patient', 'sample__order__visit__centre', 'sample__order__visit__client',
                'sample__order__test',
            ),
            pk=item_key.split(':', 1)[1],
        )
        visit = result.sample.order.visit
        dispatch_laboratory_result(user, result, request=request)
        object_id = str(result.pk)
        service = 'LABORATORY'
    else:
        from django.core.exceptions import ValidationError
        raise ValidationError('Select a result to dispatch.')

    context = build_patient_context(visit, {
        'result_link': _issue_result_link(visit.patient, hours=hours),
        'comment': comment,
    })
    if comment:
        context['reason'] = comment
    targets = _recipient_targets(visit, selected_chips or ['self'], post)
    idem = f'dispatch:{service}:{object_id}:{timezone.now().strftime("%Y%m%d%H%M%S")}'
    queued = []
    local_only = 0
    for _chip, label, channel, recipient in targets:
        note = _queue_recipient(
            centre=visit.centre, visit=visit, channel=channel, recipient=recipient,
            label=label, user=user, context=context, idempotency_key=f'{idem}:{label}',
        )
        if isinstance(note, dict):
            local_only += 1
        elif note:
            queued.append(note)
    # Ensure at least patient consented channels fire when no explicit electronic channel was chosen
    if not queued and not local_only:
        from communications.messaging import notify_patient
        queued = notify_patient(
            event_key=EVENT_RESULT_READY, visit=visit, user=user,
            idempotency_key=idem, extra={'result_link': context['result_link']},
        )
    recipient_labels = {(t[0], t[1]) for t in targets} if targets else {('self', 'Patient — Self')}
    return {
        'visit': visit,
        'queued': queued,
        'recipient_count': max(len(recipient_labels), local_only, 1 if queued else 0),
        'item_key': item_key,
    }


def retry_delivery(user, outbox_id, request=None):
    note = get_object_or_404(NotificationOutbox, pk=outbox_id)
    centre_ids = _centre_ids(user)
    if centre_ids is not None and note.centre_id not in centre_ids:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('You cannot retry a delivery outside your centre.')
    if note.status != 'FAILED':
        from django.core.exceptions import ValidationError
        raise ValidationError('Only a failed delivery can be retried.')
    note.status = 'QUEUED'
    note.last_error = ''
    note.save(update_fields=['status', 'last_error', 'updated_at'])
    return dispatch_one(note)


def _history_filters(request):
    from django.utils.dateparse import parse_date
    history_q = (request.GET.get('history_q') or '').strip()
    history_service = (request.GET.get('history_service') or '').upper()
    if history_service not in ('RADIOLOGY', 'LABORATORY'):
        history_service = ''
    history_from = parse_date(request.GET.get('history_from') or '')
    history_to = parse_date(request.GET.get('history_to') or '')
    return history_q, history_service, history_from, history_to


def _history_queryset(user, request):
    centre_ids = _centre_ids(user)
    history_q, history_service, history_from, history_to = _history_filters(request)
    ledger = ResultLedger.objects.select_related(
        'actor', 'visit', 'visit__patient', 'visit__centre',
    ).filter(kind='DISPATCH').order_by('-created_at')
    if centre_ids is not None:
        ledger = ledger.filter(visit__centre_id__in=centre_ids)
    if history_q:
        ledger = ledger.filter(
            Q(visit__visit_id__icontains=history_q)
            | Q(summary__icontains=history_q)
            | Q(actor__username__icontains=history_q)
            | Q(actor__first_name__icontains=history_q)
            | Q(actor__last_name__icontains=history_q)
            | Q(visit__patient__first_name__icontains=history_q)
            | Q(visit__patient__last_name__icontains=history_q)
        )
    if history_service:
        ledger = ledger.filter(service=history_service)
    if history_from:
        ledger = ledger.filter(created_at__date__gte=history_from)
    if history_to:
        ledger = ledger.filter(created_at__date__lte=history_to)
    return ledger, history_q, history_service, history_from, history_to


def _outbox_queue_rows(qs, *, status_label=None, when_attr='updated_at'):
    rows = []
    for row in qs[:500]:
        when = getattr(row, when_attr, None)
        label = status_label
        if label is None and when_attr == 'scheduled_for' and row.scheduled_for:
            label = f'Scheduled {timezone.localtime(row.scheduled_for):%d %b %H:%M}'
        elif label is None:
            label = (row.last_error or 'Failed')[:48]
        rows.append({
            'key': f'out:{row.pk}',
            'service': 'DELIVERY',
            'pk': str(row.pk),
            'visit_id': row.visit.visit_id if row.visit_id else '—',
            'patient_name': row.recipient,
            'test_label': row.get_channel_display(),
            'status_label': label,
            'ready': False,
            'when': when,
            'preview_url': '',
            'outbox': row,
        })
    return rows


def build_dispatch_context(request):
    user = request.user
    centre_ids = _centre_ids(user)
    query = (request.GET.get('q') or '').strip()
    tab = (request.GET.get('tab') or 'ready').lower()
    if tab not in ('ready', 'failed', 'scheduled', 'sent'):
        tab = 'ready'

    ready_items = _ready_radiology(user, centre_ids) + _ready_laboratory(user, centre_ids)
    ready_items.sort(key=lambda row: row['when'] or timezone.now(), reverse=True)
    if query:
        needle = query.lower()
        ready_items = [
            row for row in ready_items
            if needle in row['visit_id'].lower()
            or needle in row['patient_name'].lower()
            or needle in row['test_label'].lower()
        ]

    backlog = [row for row in ready_items if not row['ready']]
    ready_ok = [row for row in ready_items if row['ready']]

    today = timezone.localdate()
    history_qs, history_q, history_service, history_from, history_to = _history_queryset(user, request)
    dispatched_today = ResultLedger.objects.filter(kind='DISPATCH', created_at__date=today)
    if centre_ids is not None:
        dispatched_today = dispatched_today.filter(visit__centre_id__in=centre_ids)
    dispatched_today = dispatched_today.count()

    outbox = NotificationOutbox.objects.select_related('visit', 'created_by').order_by('-created_at')
    if centre_ids is not None:
        outbox = outbox.filter(centre_id__in=centre_ids)
    failed_qs = outbox.filter(status='FAILED')
    scheduled_qs = outbox.filter(status='QUEUED', scheduled_for__isnull=False, scheduled_for__gt=timezone.now())
    sent_qs = outbox.filter(status='SENT', sent_at__date=today)

    kpis = {
        'ready': len(ready_ok),
        'backlog': len(backlog),
        'dispatched_today': dispatched_today,
        'failed': failed_qs.count(),
    }
    tab_counts = {
        'ready': len(ready_items),
        'failed': failed_qs.count(),
        'scheduled': scheduled_qs.count(),
        'sent': sent_qs.count(),
    }

    if tab == 'failed':
        queue_rows = _outbox_queue_rows(failed_qs.order_by('-updated_at'))
    elif tab == 'scheduled':
        queue_rows = _outbox_queue_rows(scheduled_qs.order_by('scheduled_for'), when_attr='scheduled_for')
    elif tab == 'sent':
        queue_rows = _outbox_queue_rows(sent_qs.order_by('-sent_at'), status_label='Sent', when_attr='sent_at')
    else:
        queue_rows = ready_items

    queue = paginate(request, queue_rows, 'qpage')

    selected_key = (request.GET.get('item') or '').strip()
    selected = None
    if selected_key:
        selected = next((row for row in ready_items if row['key'] == selected_key), None)
    if selected is None and tab == 'ready' and ready_items:
        selected = ready_items[0]
        selected_key = selected['key']

    patient_panel = None
    delivery_log = paginate(request, [], 'dpage')
    message_preview = ''
    preview_url = ''
    rules = [
        'Results can only be dispatched after the scanned document is attached.',
        'Third-party recipients require a valid authorisation note.',
        'Sharing with external health providers requires consent.',
        'Access and retrieval links are logged.',
    ]
    if selected and selected.get('visit'):
        visit = selected['visit']
        patient = visit.patient
        invoice_label, _amount, paid = _invoice_summary(visit)
        doc = _document(visit)
        doctor = _doctor(visit)
        age = ''
        if patient.date_of_birth:
            today_d = timezone.localdate()
            years = today_d.year - patient.date_of_birth.year - (
                (today_d.month, today_d.day) < (patient.date_of_birth.month, patient.date_of_birth.day)
            )
            age = f'{years} yrs'
        patient_panel = {
            'name': _patient_label(patient),
            'age_sex': ' · '.join(part for part in (age, patient.sex) if part),
            'phone': patient.phone_number,
            'alt_phone': patient.alt_phone,
            'email': patient.email,
            'address': getattr(patient, 'address', '') or getattr(patient, 'city', '') or '',
            'visit_id': visit.visit_id,
            'registered_at': visit.created_at,
            'payer': invoice_label,
            'paid': paid,
            'physician': str(doctor) if doctor else '',
            'physician_phone': (doctor.phone if doctor else '') or '',
            'physician_email': (doctor.email if doctor else '') or '',
            'document': doc,
            'document_url': reverse('patient-document-download', args=[doc.pk]) if doc else '',
            'guardian_name': patient.guardian_name,
            'guardian_phone': patient.guardian_phone,
            'client_name': visit.client.name if visit.client_id else visit.client_name,
            'client_kind': visit.client.kind if visit.client_id else '',
            'blockers': selected.get('blockers') or [],
            'comment': visit.comments,
            'approved_label': 'approved 1/1 tests' if selected.get('ready') else 'blocked',
        }
        preview_url = selected.get('preview_url') or ''
        delivery_log = paginate(
            request,
            NotificationOutbox.objects.filter(visit=visit).order_by('-created_at'),
            'dpage',
        )
        context = build_patient_context(visit, {
            'result_link': '{{result_link}}',
        })
        _subject, message_preview = render_event_message(EVENT_RESULT_READY, 'SMS', context)

    default_recipients = ['self']
    if selected and selected.get('visit'):
        source = selected['visit'].referral_source
        if source == 'CORPORATE':
            default_recipients.append('corporate')
        elif source == 'HMO':
            default_recipients.append('hmo')

    history = paginate(request, history_qs, 'hpage')

    from urllib.parse import urlencode
    history_query = urlencode({
        key: value for key, value in {
            'history_q': history_q,
            'history_service': history_service,
            'history_from': history_from.isoformat() if history_from else '',
            'history_to': history_to.isoformat() if history_to else '',
            'tab': tab if tab != 'ready' else '',
            'item': selected_key or '',
            'q': query,
            'per': request.GET.get('per') or '',
        }.items() if value
    })

    return {
        'kpis': kpis,
        'tab': tab,
        'tab_counts': tab_counts,
        'queue': queue,
        'query': query,
        'selected': selected,
        'selected_key': selected_key,
        'patient_panel': patient_panel,
        'delivery_log': delivery_log,
        'message_preview': message_preview,
        'preview_url': preview_url,
        'rules': rules,
        'recipient_chips': RECIPIENT_CHIPS,
        'channel_options': CHANNEL_OPTIONS,
        'link_expiry_choices': LINK_EXPIRY_CHOICES,
        'default_recipients': default_recipients,
        'history': history,
        'history_q': history_q,
        'history_service': history_service,
        'history_from': history_from.isoformat() if history_from else '',
        'history_to': history_to.isoformat() if history_to else '',
        'history_query': history_query,
        'history_count': history.paginator.count,
        'page_sizes': PAGE_SIZES,
        'can_dispatch_radiology': user_can_dispatch_radiology(user),
        'can_dispatch_laboratory': user_can_dispatch_laboratory(user),
    }


def dispatch_history_export_rows(request):
    """Flat rows for CSV/PDF/Excel export of the filtered dispatch history (all matching rows)."""
    history_qs, history_q, history_service, history_from, history_to = _history_queryset(request.user, request)
    rows = []
    for row in history_qs[:5000]:
        actor = row.actor.get_full_name() if row.actor_id else ''
        if not actor and row.actor_id:
            actor = row.actor.username
        patient = ''
        if row.visit_id and row.visit.patient_id:
            patient = _patient_label(row.visit.patient)
        rows.append([
            timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
            actor,
            row.visit.visit_id if row.visit_id else '',
            patient,
            row.get_service_display(),
            row.summary or '',
        ])
    return {
        'rows': rows,
        'filters': {
            'q': history_q,
            'service': history_service,
            'from': history_from.isoformat() if history_from else '',
            'to': history_to.isoformat() if history_to else '',
        },
        'count': len(rows),
    }
