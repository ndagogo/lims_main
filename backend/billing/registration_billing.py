"""Turn the tests chosen at reception into one unpaid invoice."""
from django.core.exceptions import ValidationError
from django.db import transaction

from billing.models import Invoice, InvoiceItem
from laboratory.models import LabOrder, TestPrice
from radiology.models import ImagingModality, ImagingOrder, ImagingPrice, RadiologyExam


def process_registration(visit, user, post):
    """Create laboratory and imaging orders plus the outstanding invoice for this visit."""
    lab_ids = []
    for value in post.getlist('lab_tests'):
        if value and value not in lab_ids:
            lab_ids.append(value)
    imaging_ids = []
    for value in post.getlist('imaging_tests'):
        if value and value not in imaging_ids:
            imaging_ids.append(value)
    if not lab_ids and not imaging_ids:
        raise ValidationError('Add at least one laboratory or radiology test, then save.')
    tariff_id = (post.get('tariff') or '').strip()
    if not tariff_id:
        raise ValidationError('Select the tariff that prices these tests.')

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
        if len(lab_prices) != len(lab_ids) or len(imaging_prices) != len(imaging_ids):
            raise ValidationError('Every selected test must have a price on the chosen tariff.')
        patient = visit.patient
        invoice = Invoice.objects.create(
            visit=visit,
            payer_name=f'{patient.first_name} {patient.last_name}'.strip(),
            created_by=user,
            status='UNPAID',
        )
        for test_id in lab_ids:
            price = lab_prices[test_id]
            order = LabOrder.objects.create(visit=visit, test=price.test, tariff=price.tariff, price=price.price, status='ORDERED')
            try:
                from workflows.services import bind_workflow
                bind_workflow(order)
            except Exception:
                pass
            InvoiceItem.objects.create(
                invoice=invoice, test=price.test, tariff=price.tariff, lab_order=order,
                unit_price=price.price, outsource_price=price.outsource_price, quantity=1,
            )
        for procedure_id in imaging_ids:
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
            )
            RadiologyExam.objects.create(order=order)
            InvoiceItem.objects.create(
                invoice=invoice, imaging_order=order, tariff=price.tariff, unit_price=price.price, quantity=1,
            )
        visit.status = 'PROCESSED'
        visit.save(update_fields=['status', 'updated_at'])
        invoice.refresh_from_db()
    return invoice
