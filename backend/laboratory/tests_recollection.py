"""Recollection supersession, rejection corrective action, and barcode print history."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Payment
from core.models import Centre, UserCentreAccess
from patients.models import Patient, Visit
from .models import (
    LabOrder, Sample, SampleActivity, SampleBarcodePrint, SampleCodeConfig,
    ServiceGroup, Tariff, Test,
)

User = get_user_model()


class RecollectionSupersessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='phleb1', email='phleb1@example.test', password='Strong-test-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Phlebotomist')[0])
        self.user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_edit=True, can_register=True,
        )
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        patient = Patient.objects.create(first_name='Recollect', last_name='Case', phone_number='08033334444')
        self.visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(
            code='CBC', name='Complete Blood Count', department='PATHOLOGY',
            department_code='1', service_group=group, specimen='EDTA whole blood',
        )
        tariff = Tariff.objects.create(name='Standard')
        self.order = LabOrder.objects.create(visit=self.visit, test=self.test, tariff=tariff, price='5500')
        invoice = Invoice.objects.create(visit=self.visit, created_by=self.user, status='UNPAID')
        InvoiceItem.objects.create(
            invoice=invoice, lab_order=self.order, test=self.test, tariff=tariff,
            unit_price=Decimal('5500.00'),
        )
        Payment.objects.create(
            invoice=invoice, amount=Decimal('5500.00'), method='CASH', received_by=self.user,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'PAID')
        self.sample = Sample.objects.create(
            order=self.order, centre=self.centre, status='COLLECTED',
            collected_by=self.user, specimen_type='EDTA whole blood',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_recollect_supersedes_original_and_blocks_workflow(self):
        response = self.client.post(
            f'/api/samples/{self.sample.pk}/recollect/',
            {
                'reason': 'Haemolysed specimen',
                'disposition': 'UNSUITABLE',
                'corrective_action': 'Retake with gentle draw',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        new_id = response.data['id']
        self.sample.refresh_from_db()
        replacement = Sample.objects.get(pk=new_id)

        self.assertEqual(self.sample.status, 'SUPERSEDED')
        self.assertEqual(self.sample.disposition, 'UNSUITABLE')
        self.assertEqual(self.sample.rejection_reason, 'Haemolysed specimen')
        self.assertEqual(self.sample.corrective_action, 'Retake with gentle draw')
        self.assertEqual(self.sample.rejected_by_id, self.user.pk)
        self.assertIsNotNone(self.sample.rejected_at)

        self.assertEqual(replacement.status, 'COLLECTED')
        self.assertEqual(replacement.recollection_of_id, self.sample.pk)
        self.assertNotEqual(replacement.sample_id, self.sample.sample_id)

        actions = list(SampleActivity.objects.filter(sample=self.sample).values_list('action', flat=True))
        self.assertIn('SUPERSEDED', actions)
        self.assertTrue(
            SampleActivity.objects.filter(sample=replacement, action='RECOLLECTED').exists()
        )

        # Superseded sample cannot be batched.
        batch = self.client.post(
            '/api/sample-batches/',
            {
                'centre': str(self.centre.pk),
                'department': 'PATHOLOGY',
                'sample_ids': [self.sample.sample_id],
            },
            format='json',
        )
        self.assertEqual(batch.status_code, 400)

        # Replacement can be batched.
        ok = self.client.post(
            '/api/sample-batches/',
            {
                'centre': str(self.centre.pk),
                'department': 'PATHOLOGY',
                'sample_ids': [replacement.sample_id],
            },
            format='json',
        )
        self.assertEqual(ok.status_code, 201, ok.data)

        # Cannot recollect an already superseded sample.
        again = self.client.post(
            f'/api/samples/{self.sample.pk}/recollect/',
            {'reason': 'Again'},
            format='json',
        )
        self.assertEqual(again.status_code, 400)

    def test_reject_records_corrective_action(self):
        response = self.client.post(
            f'/api/samples/{self.sample.pk}/reject/',
            {
                'reason': 'Clotted',
                'disposition': 'UNSUITABLE',
                'corrective_action': 'Educate phlebotomy on mix technique',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.status, 'REJECTED')
        self.assertEqual(self.sample.disposition, 'UNSUITABLE')
        self.assertEqual(self.sample.corrective_action, 'Educate phlebotomy on mix technique')
        self.assertEqual(self.sample.rejected_by_id, self.user.pk)

        blocked = self.client.post(
            f'/api/samples/{self.sample.pk}/start_processing/',
            {},
            format='json',
        )
        self.assertEqual(blocked.status_code, 400)

    def test_record_print_appends_history(self):
        first = self.client.post(
            f'/api/samples/{self.sample.pk}/record-print/',
            {'source': 'API'},
            format='json',
        )
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post(
            f'/api/samples/{self.sample.pk}/record-print/',
            {'source': 'LABEL_PAGE'},
            format='json',
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(SampleBarcodePrint.objects.filter(sample=self.sample).count(), 2)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.status, 'COLLECTED')

        barcode = self.client.get(f'/api/samples/{self.sample.pk}/barcode/')
        self.assertEqual(barcode.status_code, 200)
        self.assertEqual(len(barcode.data.get('print_history') or []), 2)
