"""Result dispatch desk: unified queue, compose form, delivery log, and notifications."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from billing.eligibility import PREVIEW_BLOCKED_MESSAGE, check_visit_release
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


RECIPIENT_MODES = [
    ('self', 'Self'),
    ('third_party', 'Third-party'),
]

THIRD_PARTY_KINDS = [
    ('family', 'Family / Guardian'),
    ('hmo', 'HMO'),
    ('corporate', 'Corporate'),
]

# Kept for older posts / tests that still send recipients[] chips.
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

ID_MEANS = [
    ('NIN', 'National Identification Number (NIN)'),
    ('DRIVERS_LICENCE', "Driver's licence"),
    ('PASSPORT', 'International passport'),
    ('VOTERS_CARD', "Voter's card"),
    ('OTHER', 'Other'),
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
    ).filter(status__in=('APPROVED', 'RELEASED', 'AMENDED')).order_by(
        Coalesce('released_at', 'approved_at').desc(),
    )
    rows = _scope_reports(rows, centre_ids)
    items = []
    for report in rows:
        visit = report.exam.order.visit
        blockers = check_visit_release(visit, check_workflow=False)
        procedure = report.exam.order.procedure.name if report.exam.order.procedure_id else report.exam.order.modality.name
        sent_before = report.status in ('RELEASED', 'AMENDED')
        ready = not blockers
        if blockers:
            label = blockers[0].message[:48]
        elif sent_before:
            label = 'Ready · sent before'
        else:
            label = 'Ready'
        items.append({
            'key': f'rad:{report.pk}',
            'service': 'RADIOLOGY',
            'pk': str(report.pk),
            'visit': visit,
            'visit_id': visit.visit_id,
            'patient_name': _patient_label(visit.patient),
            'test_label': f'{report.exam.order.modality.get_modality_type_display()} · {procedure}',
            'status_label': label,
            'ready': ready,
            'already_sent': sent_before,
            'blockers': blockers,
            'when': report.released_at or report.approved_at,
            # Unpaid / incomplete bills must not offer a report preview — same as missing forms.
            'preview_url': reverse('radiology-report-preview', args=[report.pk]) if ready else '',
            'preview_blocked': not ready,
            'preview_blocked_message': PREVIEW_BLOCKED_MESSAGE if not ready else '',
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
    ).filter(status__in=('AUTHENTICATED', 'RELEASED', 'AMENDED')).order_by(
        Coalesce('released_at', 'authenticated_at').desc(),
    )
    rows = _scope_lab(rows, centre_ids)
    items = []
    for result in rows:
        visit = result.sample.order.visit
        blockers = check_visit_release(visit)
        sent_before = result.status in ('RELEASED', 'AMENDED')
        ready = not blockers
        if blockers:
            label = blockers[0].message[:48]
        elif sent_before:
            label = 'Ready · sent before'
        else:
            label = 'Ready'
        items.append({
            'key': f'lab:{result.pk}',
            'service': 'LABORATORY',
            'pk': str(result.pk),
            'visit': visit,
            'visit_id': visit.visit_id,
            'patient_name': _patient_label(visit.patient),
            'test_label': result.sample.order.test.name,
            'status_label': label,
            'ready': ready,
            'already_sent': sent_before,
            'blockers': blockers,
            'when': result.released_at or result.authenticated_at,
            'preview_url': (
                reverse('lab-result-pdf', kwargs={'pk': result.pk}) + '?letterhead=1'
                if ready else ''
            ),
            'preview_blocked': not ready,
            'preview_blocked_message': PREVIEW_BLOCKED_MESSAGE if not ready else '',
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


def _resolve_recipient_mode(post):
    """Map Self / Third-party (+ kind) into the internal chip list used by delivery targeting."""
    from django.core.exceptions import ValidationError

    mode = (post.get('recipient_mode') or '').strip().lower()
    kind = (post.get('third_party_kind') or '').strip().lower()
    legacy = [c for c in post.getlist('recipients') if c in dict(RECIPIENT_CHIPS)]
    if mode == 'self' or (not mode and ('self' in legacy or not legacy)):
        return 'self', '', ['self']
    if mode == 'third_party' or any(c in legacy for c in ('third_party', 'family', 'hmo', 'corporate')):
        if not kind:
            if 'family' in legacy or 'third_party' in legacy:
                kind = 'family'
            elif 'hmo' in legacy:
                kind = 'hmo'
            elif 'corporate' in legacy:
                kind = 'corporate'
            else:
                kind = 'family'
        if kind not in dict(THIRD_PARTY_KINDS):
            raise ValidationError('Choose Family/Guardian, HMO, or Corporate for third-party dispatch.')
        chip = 'family' if kind == 'family' else kind
        return 'third_party', kind, [chip]
    raise ValidationError('Choose Self or Third-party.')


def _client_email(client):
    return (getattr(client, 'report_email', '') or getattr(client, 'comm_email', '') or '').strip()


def _client_phone(client):
    return (
        getattr(client, 'contact_phone', '')
        or getattr(client, 'contact_mobile_1', '')
        or getattr(client, 'phone', '')
        or ''
    ).strip()


def _organisation_clients(kind):
    from patients.models import OrganisationClient
    kind = (kind or '').upper()
    rows = []
    for client in (
        OrganisationClient.objects.filter(is_active=True)
        .filter(Q(kind=kind) | Q(category=kind))
        .order_by('name')
    ):
        rows.append({
            'id': str(client.pk),
            'name': client.name,
            'email': _client_email(client),
            'phone': _client_phone(client),
            'kind': client.kind,
            'client_type_code': client.client_type_code,
        })
    return rows


def _recipient_targets(visit, selected_chips, post, *, third_party_kind='', organisation_client=None):
    """Build (chip, label, channel, recipient) rows from the compose form."""
    patient = visit.patient
    doctor = _doctor(visit)
    targets = []
    for chip in selected_chips:
        channels = [c.strip().upper() for c in post.getlist(f'channels_{chip}') if c.strip()]
        # Legacy compose forms posted channels_third_party for Family/Guardian collection.
        if chip == 'family' and not channels:
            channels = [c.strip().upper() for c in post.getlist('channels_third_party') if c.strip()]
        if chip == 'self':
            if not channels:
                channels = ['SMS', 'PORTAL', 'HARDCOPY']
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
            # Family / Guardian third-party collection — desk form supplies the person collecting.
            phone = (post.get('third_party_phone') or patient.guardian_phone or '').strip()
            name = (post.get('third_party_name') or patient.guardian_name or 'Family/Guardian').strip()
            email = (post.get('third_party_email') or '').strip()
            if not channels:
                channels = ['HARDCOPY']
            for channel in channels:
                if channel == 'EMAIL' and email:
                    targets.append((chip, name, channel, email))
                elif channel in ('SMS', 'WHATSAPP') and phone:
                    targets.append((chip, name, channel, phone))
                elif channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
        elif chip == 'third_party':
            phone = (post.get('third_party_phone') or '').strip()
            email = (post.get('third_party_email') or '').strip()
            name = (post.get('third_party_name') or 'Third party').strip()
            if not channels:
                channels = ['HARDCOPY']
            for channel in channels:
                if channel == 'EMAIL' and email:
                    targets.append((chip, name, channel, email))
                elif channel in ('SMS', 'WHATSAPP') and phone:
                    targets.append((chip, name, channel, phone))
                elif channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
        elif chip in ('corporate', 'hmo'):
            if organisation_client:
                name = organisation_client.get('name') or chip.title()
                email = (organisation_client.get('email') or '').strip()
                phone = (organisation_client.get('phone') or '').strip()
            else:
                name = visit.client.name if visit.client_id else (visit.client_name or chip.title())
                email = (post.get(f'{chip}_email') or '').strip()
                phone = (post.get(f'{chip}_phone') or '').strip()
            if not channels:
                channels = ['HARDCOPY']
                if email:
                    channels.append('EMAIL')
            for channel in channels:
                if channel == 'HARDCOPY':
                    targets.append((chip, name, channel, 'HARDCOPY'))
                elif channel == 'EMAIL' and email:
                    targets.append((chip, name, channel, email))
                elif channel in ('SMS', 'WHATSAPP') and phone:
                    targets.append((chip, name, channel, phone))
    physician_channels = [c.strip().upper() for c in post.getlist('channels_physician') if c.strip()]
    if doctor and physician_channels:
        for channel in physician_channels:
            if channel == 'EMAIL' and (doctor.email or '').strip():
                targets.append(('physician', f'Referring physician — {doctor}', channel, doctor.email.strip()))
            elif channel in ('SMS', 'WHATSAPP') and (doctor.phone or '').strip():
                targets.append(('physician', f'Referring physician — {doctor}', channel, doctor.phone.strip()))
            elif channel == 'HARDCOPY':
                targets.append(('physician', f'Referring physician — {doctor}', channel, 'HARDCOPY'))
    seen = set()
    unique = []
    for row in targets:
        key = (row[2], row[3])
        if not row[3] or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _third_party_collection(post, *, require=False, upload=None):
    """Identity of the person collecting a result on the patient's behalf (Family/Guardian)."""
    if not require:
        return '', None
    from django.core.exceptions import ValidationError
    name = (post.get('third_party_name') or '').strip()
    id_type = (post.get('third_party_id_type') or '').strip()
    id_number = (post.get('third_party_id_number') or '').strip()
    address = ' '.join((post.get('third_party_address') or '').split())
    phone = (post.get('third_party_phone') or '').strip()
    auth = (post.get('third_party_auth') or '').strip()
    means = dict(ID_MEANS)
    missing = []
    if not name:
        missing.append('name')
    if id_type not in means or not id_number:
        missing.append('means of identification')
    if not upload:
        missing.append('a copy of the identification')
    if not address:
        missing.append('address')
    if not phone:
        missing.append('personal phone number')
    if not auth:
        missing.append('authorisation note')
    if missing:
        raise ValidationError('Third-party collection needs ' + ', '.join(missing) + '.')
    from core.uploads import validate_upload
    validate_upload(upload)
    note = (
        f'Third party (Family/Guardian): {name}. ID: {means[id_type]} {id_number}. '
        f'Address: {address}. Phone: {phone}. Authorisation: {auth}. '
        'Identification copy filed.'
    )
    return note, upload


def _resolve_organisation_client(post, kind):
    from django.core.exceptions import ValidationError
    from patients.models import OrganisationClient

    client_id = (post.get('organisation_client') or post.get(f'{kind}_client') or '').strip()
    if not client_id:
        raise ValidationError(f'Select an {kind.upper()} from the list before dispatching.')
    client = OrganisationClient.objects.filter(pk=client_id, is_active=True).first()
    if client is None:
        raise ValidationError(f'That {kind.upper()} account was not found.')
    return {
        'id': str(client.pk),
        'name': client.name,
        'email': _client_email(client),
        'phone': _client_phone(client),
        'kind': client.kind,
        'client_type_code': client.client_type_code,
    }


def _stamp_collector(service, object_id, note, upload=None, extra_reason='', summary='', print_style=''):
    row = ResultLedger.objects.filter(
        kind='DISPATCH', service=service, object_id=str(object_id),
    ).order_by('-created_at').first()
    if row is None:
        return
    fields = []
    if summary:
        row.summary = summary[:200]
        fields.append('summary')
    combined = ' | '.join(part for part in (note, extra_reason) if part)
    if combined:
        row.reason = combined[:4000]
        fields.append('reason')
    if upload:
        row.collector_id = upload
        fields.append('collector_id')
    if print_style in ('letterhead', 'plain'):
        row.print_style = print_style
        fields.append('print_style')
    if fields:
        row.save(update_fields=fields)


def _load_dispatch_item(item_key):
    from django.core.exceptions import ValidationError
    from laboratory.models import LabResult

    key = (item_key or '').strip()
    if key.startswith('rad:'):
        report = get_object_or_404(
            RadiologyReport.objects.select_related(
                'exam__order__visit__patient', 'exam__order__visit__centre', 'exam__order__visit__client',
                'exam__order__modality', 'exam__order__procedure',
            ),
            pk=key.split(':', 1)[1],
        )
        visit = report.exam.order.visit
        procedure = report.exam.order.procedure.name if report.exam.order.procedure_id else report.exam.order.modality.name
        label = f'{report.exam.order.modality.get_modality_type_display()} · {procedure}'
        return {
            'key': key, 'service': 'RADIOLOGY', 'visit': visit, 'object': report,
            'object_type': 'RadiologyReport', 'object_id': str(report.pk), 'label': label,
        }
    if key.startswith('lab:'):
        result = get_object_or_404(
            LabResult.objects.select_related(
                'sample__order__visit__patient', 'sample__order__visit__centre', 'sample__order__visit__client',
                'sample__order__test',
            ),
            pk=key.split(':', 1)[1],
        )
        visit = result.sample.order.visit
        return {
            'key': key, 'service': 'LABORATORY', 'visit': visit, 'object': result,
            'object_type': 'LabResult', 'object_id': str(result.pk),
            'label': result.sample.order.test.name,
        }
    raise ValidationError('Select at least one result to dispatch.')


def compose_dispatch(user, post, request=None, files=None):
    """Clinical release + queued notifications for one or more selected results on a visit."""
    from django.core.exceptions import ValidationError
    from django.db import transaction

    item_keys = [k.strip() for k in post.getlist('items') if k.strip()]
    if not item_keys:
        single = (post.get('item') or '').strip()
        if single:
            item_keys = [single]
    if not item_keys:
        raise ValidationError('Select at least one result to dispatch.')

    loaded = [_load_dispatch_item(key) for key in item_keys]
    visit_ids = {row['visit'].pk for row in loaded}
    if len(visit_ids) != 1:
        raise ValidationError('All selected results must belong to the same visit.')
    visit = loaded[0]['visit']

    # Payment / document / workflow gate — same bar as missing registration forms.
    blockers = check_visit_release(visit, check_workflow=any(row['service'] == 'LABORATORY' for row in loaded))
    if blockers:
        raise ValidationError(blockers[0].message)

    recipient_mode, third_party_kind, selected_chips = _resolve_recipient_mode(post)
    organisation_client = None
    if recipient_mode == 'third_party' and third_party_kind in ('hmo', 'corporate'):
        organisation_client = _resolve_organisation_client(post, third_party_kind)

    uploaded = files.get('third_party_id_file') if files is not None else None
    collector_note, collector_file = _third_party_collection(
        post, require=(recipient_mode == 'third_party' and third_party_kind == 'family'), upload=uploaded,
    )
    comment = (post.get('comment') or '').strip()
    print_style = (post.get('report_format') or 'letterhead').strip().lower()
    if print_style in ('plain', 'without', 'pdf-plain'):
        print_style = 'plain'
    else:
        print_style = 'letterhead'
    hours = int(post.get('link_expiry') or '7')
    selection_summary = ', '.join(row['label'] for row in loaded)
    recipient_desc = 'Self'
    if recipient_mode == 'third_party':
        if third_party_kind == 'family':
            recipient_desc = 'Third-party · Family/Guardian'
        elif organisation_client:
            recipient_desc = f'Third-party · {third_party_kind.upper()} · {organisation_client["name"]}'
        else:
            recipient_desc = f'Third-party · {third_party_kind}'

    dispatched = []
    with transaction.atomic():
        for row in loaded:
            if row['service'] == 'RADIOLOGY':
                dispatch_radiology_report(user, row['object'], request=request)
            else:
                dispatch_laboratory_result(user, row['object'], request=request)
            audit_note = (
                f'Dispatch selection ({len(loaded)}): {selection_summary}. '
                f'Recipient: {recipient_desc}.'
            )
            if comment:
                audit_note = f'{audit_note} Comment: {comment}'
            ledger_summary = f"{visit.visit_id} · {row['label']} → {recipient_desc}"
            _stamp_collector(
                row['service'], row['object_id'], collector_note, collector_file,
                extra_reason=audit_note, summary=ledger_summary, print_style=print_style,
            )
            dispatched.append(row)

    context = build_patient_context(visit, {
        'result_link': _issue_result_link(visit.patient, hours=hours),
        'comment': comment,
        'tests': selection_summary,
    })
    if comment:
        context['reason'] = comment
    targets = _recipient_targets(
        visit, selected_chips, post,
        third_party_kind=third_party_kind, organisation_client=organisation_client,
    )
    stamp = timezone.now().strftime('%Y%m%d%H%M%S')
    idem = f'dispatch:visit:{visit.pk}:{stamp}'
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
    if not queued and not local_only and recipient_mode == 'self':
        from communications.messaging import notify_patient
        queued = notify_patient(
            event_key=EVENT_RESULT_READY, visit=visit, user=user,
            idempotency_key=idem, extra={'result_link': context['result_link']},
        )

    from core.audit import record_audit
    record_audit(
        actor=user, action='RESULT_DISPATCH_BATCH', object_type='Visit', object_id=str(visit.pk),
        request=request, centre=visit.centre, visit_ref=visit.visit_id,
        reason=comment or recipient_desc,
        new_values={
            'recipient_mode': recipient_mode,
            'third_party_kind': third_party_kind,
            'organisation_client': organisation_client,
            'print_style': print_style,
            'items': [row['key'] for row in dispatched],
            'tests': [row['label'] for row in dispatched],
            'channels': [{'chip': t[0], 'label': t[1], 'channel': t[2], 'recipient': t[3]} for t in targets],
        },
        detail={
            'selection_count': len(dispatched),
            'selection_summary': selection_summary,
            'recipient': recipient_desc,
            'queued_messages': len(queued),
        },
        category='CLINICAL',
    )

    recipient_labels = {(t[0], t[1]) for t in targets} if targets else {('self', 'Patient — Self')}
    return {
        'visit': visit,
        'queued': queued,
        'recipient_count': max(len(recipient_labels), local_only, 1 if queued else 0),
        'item_key': dispatched[0]['key'],
        'item_keys': [row['key'] for row in dispatched],
        'selection_summary': selection_summary,
        'dispatched_count': len(dispatched),
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
            | Q(reason__icontains=history_q)
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
    visit_results = []
    delivery_log = paginate(request, [], 'dpage')
    message_preview = ''
    preview_url = ''
    preview_blocked = False
    preview_blocked_message = ''
    rules = [
        'A finished laboratory or radiology result stays here so it can be dispatched again. Each send is kept in history.',
        'Results can only be dispatched after the scanned referral/registration form is attached and the bill is fully paid.',
        'Choose Self or Third-party. Family/Guardian third-party collection needs identification, an uploaded ID copy, address, phone, and an authorisation note. HMO and Corporate need a selected account.',
        'Select one or more ready results for the same visit before dispatching. Every selection is audited.',
        'Sharing with external health providers requires consent. Access and retrieval links are logged.',
    ]
    default_recipient_mode = 'self'
    default_third_party_kind = 'family'
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
        visit_results = [
            row for row in ready_items
            if row.get('visit') and row['visit'].pk == visit.pk
        ]
        ready_count = sum(1 for row in visit_results if row.get('ready'))
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
            'approved_label': (
                f'{ready_count}/{len(visit_results)} ready'
                if visit_results else ('ready' if selected.get('ready') else 'blocked')
            ),
        }
        preview_url = selected.get('preview_url') or ''
        preview_blocked = bool(selected.get('preview_blocked'))
        preview_blocked_message = selected.get('preview_blocked_message') or PREVIEW_BLOCKED_MESSAGE
        delivery_log = paginate(
            request,
            NotificationOutbox.objects.filter(visit=visit).order_by('-created_at'),
            'dpage',
        )
        context = build_patient_context(visit, {
            'result_link': '{{result_link}}',
        })
        _subject, message_preview = render_event_message(EVENT_RESULT_READY, 'SMS', context)
        source = (visit.referral_source or '').upper()
        client_kind = (visit.client.kind if visit.client_id else '').upper()
        if source == 'HMO' or client_kind == 'HMO':
            default_recipient_mode = 'third_party'
            default_third_party_kind = 'hmo'
        elif source == 'CORPORATE' or client_kind == 'CORPORATE':
            default_recipient_mode = 'third_party'
            default_third_party_kind = 'corporate'

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
        'visit_results': visit_results,
        'visit_has_ready': any(row.get('ready') for row in visit_results),
        'patient_panel': patient_panel,
        'delivery_log': delivery_log,
        'message_preview': message_preview,
        'preview_url': preview_url,
        'preview_blocked': preview_blocked,
        'preview_blocked_message': preview_blocked_message,
        'rules': rules,
        'recipient_modes': RECIPIENT_MODES,
        'third_party_kinds': THIRD_PARTY_KINDS,
        'recipient_chips': RECIPIENT_CHIPS,
        'id_means': ID_MEANS,
        'channel_options': CHANNEL_OPTIONS,
        'link_expiry_choices': LINK_EXPIRY_CHOICES,
        'default_recipient_mode': default_recipient_mode,
        'default_third_party_kind': default_third_party_kind,
        'default_recipients': ['self'] if default_recipient_mode == 'self' else [default_third_party_kind],
        'hmo_clients': _organisation_clients('HMO'),
        'corporate_clients': _organisation_clients('CORPORATE'),
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
            row.reason or '',
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
