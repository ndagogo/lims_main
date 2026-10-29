from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import Client, TestCase

from billing.models import Invoice
from billing.registration_billing import process_registration
from core.models import Centre
from laboratory.models import LabOrder, ServiceGroup, StatType, Tariff, Test, TestPrice
from patients.models import Patient, Visit
from radiology.models import ImagingModality, ImagingOrder, ImagingPrice, ImagingProcedure


class StatTypeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username='stat.admin', email='stat.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.client = Client()
        self.client.force_login(self.admin)

    def test_platform_admin_manages_stats_and_registration_assigns_them(self):
        page = self.client.get('/catalog/tests/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Stat types')
        self.assertContains(page, 'Urgent')
        self.assertContains(page, '#000000')
        self.assertContains(page, 'Critical')
        self.assertContains(page, '#dc3545')
        self.assertContains(page, 'Normal')
        self.assertContains(page, '#198754')

        created = self.client.post('/catalog/tests/', {
            'action': 'create_stat', 'name': 'ASAP', 'color': '#111111', 'text_color': '#ffffff',
            'sort_order': '40', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created.status_code, 302)
        asap = StatType.objects.get(name='ASAP')
        renamed = self.client.post('/catalog/tests/', {
            'action': 'update_stat', 'stat_type': asap.pk, 'name': 'Immediate', 'color': '#222222',
            'text_color': '#ffffff', 'sort_order': '15', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(renamed.status_code, 302)
        asap.refresh_from_db()
        self.assertEqual(asap.name, 'Immediate')
        removed = self.client.post('/catalog/tests/', {'action': 'delete_stat', 'stat_type': asap.pk})
        self.assertEqual(removed.status_code, 302)
        self.assertFalse(StatType.objects.filter(pk=asap.pk).exists())

        register = self.client.get('/patients/register/')
        self.assertContains(register, 'Each test has its own stat')
        self.assertContains(register, '"name": "Urgent"')

        group = ServiceGroup.objects.create(name='Haematology', code='1')
        test = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=test, price=Decimal('1000.00'))
        procedure = ImagingProcedure.objects.create(code='CXR', name='Chest X-ray', modality_type='XRAY')
        ImagingPrice.objects.create(tariff=tariff, procedure=procedure, price=Decimal('5000.00'))
        ImagingModality.objects.create(centre=self.centre, code='XR1', name='X-ray Room 1', modality_type='XRAY')
        patient = Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08010000001')
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.admin)
        urgent = StatType.objects.get(name='Urgent')
        critical = StatType.objects.get(name='Critical')
        posted = QueryDict(mutable=True)
        posted['tariff'] = str(tariff.pk)
        posted.appendlist('lab_tests', f'{test.pk}|{urgent.pk}')
        posted.appendlist('imaging_tests', f'{procedure.pk}|{critical.pk}')
        process_registration(visit, self.admin, posted)
        Invoice.objects.filter(visit=visit).update(status='PAID')
        lab_order = LabOrder.objects.get(visit=visit, test=test)
        imaging_order = ImagingOrder.objects.get(visit=visit, procedure=procedure)
        self.assertEqual(lab_order.stat, urgent)
        self.assertEqual(imaging_order.stat, critical)

        blocked = self.client.post('/catalog/tests/', {'action': 'delete_stat', 'stat_type': urgent.pk})
        self.assertEqual(blocked.status_code, 302)
        self.assertTrue(StatType.objects.filter(pk=urgent.pk).exists())

        phlebotomy = self.client.get('/phlebotomy/')
        self.assertContains(phlebotomy, 'Complete Blood Count')
        self.assertContains(phlebotomy, 'Urgent')
        self.assertContains(phlebotomy, 'background:#000000')
        radiology = self.client.get('/radiology/')
        self.assertContains(radiology, 'Chest X-ray')
        self.assertContains(radiology, 'Critical')
        self.assertContains(radiology, 'background:#dc3545')

        plain = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.admin)
        fallback = QueryDict(mutable=True)
        fallback['tariff'] = str(tariff.pk)
        fallback.appendlist('lab_tests', str(test.pk))
        process_registration(plain, self.admin, fallback)
        self.assertEqual(LabOrder.objects.get(visit=plain).stat.name, 'Normal')
