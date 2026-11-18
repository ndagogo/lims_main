"""Processing centre chosen at collection routes the sample for batch receiving."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Payment
from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, Visit

User = get_user_model()


class ProcessingCentreRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='phleb.route', email='phleb.route@example.test', password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Phlebotomist')[0])
        self.user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        self.nowas = Centre.objects.create(code='NWS', name='NOWAS')
        self.ind = Centre.objects.create(code='IND', name='Independence Layout')
        SampleCodeConfig.objects.create(centre=self.nowas, numeric_code='2')
        SampleCodeConfig.objects.create(centre=self.ind, numeric_code='3')
        for centre in (self.nowas, self.ind):
            UserCentreAccess.objects.create(
                user=self.user, centre=centre, can_view=True, can_register=True, can_edit=True, can_approve=True,
            )
        patient = Patient.objects.create(first_name='Route', last_name='Patient', phone_number='08033334444')
        self.visit = Visit.objects.create(patient=patient, centre=self.ind, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(
            code='CBC', name='Complete Blood Count', department='HAEMATOLOGY',
            department_code='1', service_group=group,
        )
        self.order = LabOrder.objects.create(visit=self.visit, test=self.test, price='5500', status='ORDERED')
        invoice = Invoice.objects.create(
            visit=self.visit, payer_name='Route', subtotal=Decimal('5500.00'), total=Decimal('5500.00'),
            status='UNPAID', created_by=self.user,
        )
        InvoiceItem.objects.create(
            invoice=invoice, lab_order=self.order, test=self.test, description=self.test.name,
            unit_price=Decimal('5500.00'), quantity=1,
        )
        Payment.objects.create(
            invoice=invoice, amount=Decimal('5500.00'), method='CASH', received_by=self.user,
        )
        invoice.recalculate()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_collect_routes_sample_to_selected_processing_centre(self):
        self.client.force_login(self.user)
        page = self.client.get('/phlebotomy/')
        self.assertContains(page, 'NOWAS')
        self.assertContains(page, 'Independence Layout')
        self.assertContains(page, 'Default processing centre')

        response = self.api.post(
            f'/api/lab-orders/{self.order.pk}/collect/',
            {'processing_centre': str(self.nowas.pk), 'note': 'Courier to Nowas'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        sample = Sample.objects.get(pk=response.data['id'])
        self.assertEqual(sample.centre_id, self.nowas.pk)
        self.assertEqual(sample.order.visit.centre_id, self.ind.pk)
        self.assertTrue(sample.sample_id.startswith('2'))

        # Batch create is scoped to Sample.centre (the processing centre), not registration.
        wrong = self.api.post(
            '/api/sample-batches/',
            {
                'centre': str(self.ind.pk),
                'department': 'HAEMATOLOGY',
                'sample_ids': [sample.sample_id],
            },
            format='json',
        )
        self.assertEqual(wrong.status_code, 400)
        batch = self.api.post(
            '/api/sample-batches/',
            {
                'centre': str(self.nowas.pk),
                'department': 'HAEMATOLOGY',
                'sample_ids': [sample.sample_id],
            },
            format='json',
        )
        self.assertEqual(batch.status_code, 201, batch.data)
