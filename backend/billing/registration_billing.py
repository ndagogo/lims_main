"""Turn the tests chosen at reception into one unpaid invoice."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from billing.models import Invoice, InvoiceItem
from billing.pricing import (
    CREDIT_CHANNELS,
    ensure_credit_wallet_covers,
    normalize_channel,
    resolve_tariff,
    visit_shows_test_prices,
)
from laboratory.models import LabOrder, TestPrice
from laboratory.stat_catalog import stat_for_choice
from radiology.models import ImagingModality, ImagingOrder, ImagingPrice, RadiologyExam


def _order_choices(post, field):
    choices = []
    seen = set()
    for value in post.getlist(field):
        raw = (value or '').strip()
        if not raw:
            continue
        item_id, _sep, stat_id = raw.partition('|')
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        choices.append((item_id, stat_id))
    return choices


def process_registration(visit, user, post):
    """Create laboratory and imaging orders plus the outstanding invoice for this visit."""
    lab_choices = _order_choices(post, 'lab_tests')
    imaging_choices = _order_choices(post, 'imaging_tests')
    lab_ids = [item_id for item_id, _stat_id in lab_choices]
    imaging_ids = [item_id for item_id, _stat_id in imaging_choices]
    if not lab_ids and not imaging_ids:
        raise ValidationError('Add at least one laboratory or radiology test, then save.')

    posted_tariff = (post.get('tariff') or '').strip() or None
    tariff = resolve_tariff(
        centre=visit.centre,
        channel_code=visit.referral_source,
        client=visit.client,
        fallback_tariff=posted_tariff,
    )
    tariff_id = tariff.pk
    visit.resolved_tariff = tariff
    visit.show_test_prices = visit_shows_test_prices(visit)
    visit.save(update_fields=['resolved_tariff', 'show_test_prices', 'updated_at'])

    with transaction.atomic():
        lab_prices = {
            str(row.test_id): row
            for row in TestPrice.objects.select_related('test', 'tariff').filter(
                tariff_id=tariff_id, tariff__is_active=True, test_id__in=lab_ids, test__is_active=True,
            )
        }
        imaging_prices = {
            str(row.procedure_id): row
            for row in ImagingPrice.objects.select_related('procedure', 'tariff').filter(
                tariff_id=tariff_id, tariff__is_active=True, procedure_id__in=imaging_ids, procedure__is_active=True,
            )
        }
        missing = []
        for test_id, _stat in lab_choices:
            if test_id not in lab_prices:
                missing.append(test_id)
        for procedure_id, _stat in imaging_choices:
            if procedure_id not in imaging_prices:
                missing.append(procedure_id)
        if missing:
            raise ValidationError(
                'You can not proceed , contact admin to setup the price. Thanks'
            )

        subtotal = Decimal('0.00')
        for test_id, _stat in lab_choices:
            subtotal += lab_prices[test_id].price
        for procedure_id, _stat in imaging_choices:
            subtotal += imaging_prices[procedure_id].price

        channel = normalize_channel(visit.referral_source)
        use_credit_wallet = channel in CREDIT_CHANNELS and visit.client_id and visit.client.client_type_code == 'CREDIT'
        if use_credit_wallet:
            ensure_credit_wallet_covers(client=visit.client, amount=subtotal)

        patient = visit.patient
        if use_credit_wallet:
            payer_name = visit.client_name or (visit.client.name if visit.client else '') or 'Credit client'
        else:
            payer_name = f'{patient.first_name} {patient.last_name}'.strip()

        invoice = Invoice.objects.create(
            visit=visit,
            payer_name=payer_name,
            created_by=user,
            status='UNPAID',
        )
        for test_id, stat_id in lab_choices:
            price = lab_prices[test_id]
            order = LabOrder.objects.create(
                visit=visit, test=price.test, tariff=price.tariff, price=price.price,
                status='ORDERED', stat=stat_for_choice(stat_id),
            )
            try:
                from workflows.services import bind_workflow
                bind_workflow(order)
            except Exception:
                pass
            InvoiceItem.objects.create(
                invoice=invoice, test=price.test, tariff=price.tariff, lab_order=order,
                unit_price=price.price, outsource_price=price.outsource_price, quantity=1,
            )
        for procedure_id, stat_id in imaging_choices:
            price = imaging_prices[procedure_id]
            procedure = price.procedure
            modality = ImagingModality.objects.filter(
                centre=visit.centre, modality_type=procedure.modality_type, is_active=True,
            ).order_by('name').first()
            if modality is None:
                raise ValidationError(f'{procedure.name} needs an active {procedure.get_modality_type_display()} room at this centre.')
            order = ImagingOrder.objects.create(
                visit=visit, modality=modality, procedure=procedure,
                clinical_history=visit.clinical_history, requested_by=user, status='ORDERED',
                stat=stat_for_choice(stat_id),
            )
            RadiologyExam.objects.create(order=order)
            InvoiceItem.objects.create(
                invoice=invoice, imaging_order=order, tariff=price.tariff, unit_price=price.price, quantity=1,
            )
        visit.status = 'PROCESSED'
        visit.save(update_fields=['status', 'updated_at'])
        invoice.refresh_from_db()
        # Credit-client wallets stay untouched while the invoice is Unpaid.
        # Wallet debit runs only when Invoice status becomes Paid (see Invoice.recalculate).

        from communications.inbox import announce, record_corporate_invoice
        announce(lambda: record_corporate_invoice(invoice))
    return invoice
