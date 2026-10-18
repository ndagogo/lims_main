from datetime import date
from decimal import Decimal

from django.test import Client, TestCase

from core.models import Centre
from laboratory.models import LabOrder, LabResult, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, Visit
from billing.models import Invoice, Payment
from portals.models import PortalRequest


class PatientPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        self.adult = Patient.objects.create(
            first_name='Ada', last_name='Okeke', phone_number='8011111111',
            date_of_birth=date(1990, 4, 4), notify_sms=True, email='ada@example.test',
        )
        self.shared = Patient.objects.create(
            first_name='Chidi', last_name='Okeke', phone_number='8011111111',
            date_of_birth=date(1988, 2, 2), notify_sms=True,
        )
        self.minor = Patient.objects.create(
            first_name='Junior', last_name='Okeke', phone_number='8011111111',
            date_of_birth=date(2016, 6, 1),
            guardian_name='Ada Okeke', guardian_relationship='Mother', guardian_phone='8099999999',
        )
        self.visit = Visit.objects.create(patient=self.adult, centre=self.centre)
        invoice = Invoice.objects.create(visit=self.visit, total=Decimal('2000.00'), status='UNPAID')
        self.payment = Payment.objects.create(invoice=invoice, amount=Decimal('2000.00'), method='CASH')
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        test = Test.objects.create(code='CBC', name='Complete Blood Count', department='PATHOLOGY', department_code='1', service_group=group)
        order = LabOrder.objects.create(visit=self.visit, test=test, price='2000')
        sample = Sample.objects.create(order=order, centre=self.centre, status='RELEASED')
        self.result = LabResult.objects.create(sample=sample, status='RELEASED', narrative='Within range')
        other_visit = Visit.objects.create(patient=self.shared, centre=self.centre)
        other_order = LabOrder.objects.create(visit=other_visit, test=test, price='10')
        other_sample = Sample.objects.create(order=other_order, centre=self.centre, status='RELEASED')
        self.other_result = LabResult.objects.create(sample=other_sample, status='RELEASED', narrative='Secret other patient')

    def _login(self, patient, **extra):
        data = {'uhid': patient.uhid, 'date_of_birth': patient.date_of_birth.isoformat()}
        data.update(extra)
        return self.client.post('/portals/patient/login/', data)

    def test_phone_number_does_not_open_every_matching_patient(self):
        response = self.client.post('/portals/patient/login/', {'phone_number': '8011111111'})
        self.assertContains(response, 'phone number alone cannot open results')
        self.assertNotIn('portal_token', self.client.session)

    def test_adult_sees_only_their_released_result_receipt_and_can_request_help(self):
        self.assertEqual(self._login(self.adult).status_code, 302)
        home = self.client.get('/portals/patient/')
        self.assertContains(home, self.adult.uhid)
        self.assertContains(home, self.payment.receipt_number)
        self.assertContains(home, 'Complete Blood Count')
        self.assertNotContains(home, 'Secret other patient')
        self.assertNotContains(home, self.shared.uhid)
        blocked = self.client.get(f'/portals/patient/results/{self.other_result.pk}/')
        self.assertEqual(blocked.status_code, 404)
        own = self.client.get(f'/portals/patient/results/{self.result.pk}/')
        self.assertContains(own, 'Within range')
        saved = self.client.post('/portals/patient/preferences/', {'notify_email': '1', 'email': 'ada@example.test'})
        self.assertEqual(saved.status_code, 302)
        self.adult.refresh_from_db()
        self.assertTrue(self.adult.notify_email)
        self.assertFalse(self.adult.notify_sms)
        asked = self.client.post('/portals/patient/requests/', {'kind': 'CORRECTION', 'message': 'Please check the spelling of my name.', 'visit': str(self.visit.pk)})
        self.assertEqual(asked.status_code, 302)
        self.assertTrue(PortalRequest.objects.filter(patient=self.adult, kind='CORRECTION').exists())

    def test_minor_requires_the_registered_guardian_phone(self):
        rejected = self._login(self.minor)
        self.assertContains(rejected, 'registered guardian phone')
        accepted = self._login(self.minor, guardian_phone='8099999999')
        self.assertEqual(accepted.status_code, 302)
        home = self.client.get('/portals/patient/')
        self.assertContains(home, self.minor.uhid)
        self.assertNotContains(home, self.adult.first_name)
