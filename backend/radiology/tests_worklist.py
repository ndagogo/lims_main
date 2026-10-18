from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from billing.models import Invoice, InvoiceItem, Payment
from core.models import Centre
from patients.models import Patient, Visit
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam
from radiology.worklist import handle_find, publish_exam, scheduled_exams, worklist_record

User = get_user_model()


class ModalityWorklistTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='ris.admin', email='ris.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        self.centre = Centre.objects.create(code='RIS', name='RIS Centre')
        self.patient = Patient.objects.create(
            first_name='Ada', last_name='Okafor', other_names='N',
            phone_number='08000000991', date_of_birth=date(1990, 4, 2), sex='Female',
        )
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.user)
        self.modality = ImagingModality.objects.create(
            centre=self.centre, code='XR1', name='X-ray Room 1', modality_type='XRAY',
            ae_title='XRROOM', host='192.168.1.40', port=104,
        )
        self.order = ImagingOrder.objects.create(visit=self.visit, modality=self.modality, requested_by=self.user)
        self.exam = RadiologyExam.objects.create(order=self.order)

    def _pay(self):
        invoice = Invoice.objects.create(visit=self.visit, payer_name='Ada Okafor', created_by=self.user, status='UNPAID')
        InvoiceItem.objects.create(invoice=invoice, imaging_order=self.order, unit_price=Decimal('5000.00'), quantity=1)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.mwl_status, '')
        Payment.objects.create(invoice=invoice, amount=Decimal('5000.00'), method='CASH', received_by=self.user)
        self.exam.refresh_from_db()

    def test_paid_study_is_offered_to_the_modality_ae(self):
        invoice = Invoice.objects.create(visit=self.visit, payer_name='Ada Okafor', created_by=self.user, status='UNPAID')
        InvoiceItem.objects.create(invoice=invoice, imaging_order=self.order, unit_price=Decimal('5000.00'), quantity=1)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.mwl_status, '')
        self.assertFalse(publish_exam(self.exam))
        Payment.objects.create(invoice=invoice, amount=Decimal('5000.00'), method='CASH', received_by=self.user)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.mwl_status, 'SCHEDULED')
        rows = scheduled_exams('XRROOM')
        self.assertEqual([row.pk for row in rows], [self.exam.pk])
        record = worklist_record(self.exam)
        self.assertEqual(record['patient_id'], self.patient.uhid)
        self.assertEqual(record['patient_name'], 'Okafor^Ada^N')
        self.assertEqual(record['sex'], 'F')
        self.assertEqual(record['station_ae'], 'XRROOM')
        self.assertEqual(record['modality'], 'CR')
        self.assertEqual(scheduled_exams('OTHERROOM'), [])

    def test_worklist_query_returns_the_patient(self):
        self._pay()
        from pydicom.dataset import Dataset
        identifier = Dataset()
        step = Dataset()
        step.ScheduledStationAETitle = 'XRROOM'
        identifier.ScheduledProcedureStepSequence = [step]
        event = SimpleNamespace(
            identifier=identifier,
            assoc=SimpleNamespace(requestor=SimpleNamespace(ae_title=b'XRROOM')),
        )
        responses = list(handle_find(event))
        matches = [ds for status, ds in responses if status == 0xFF00]
        self.assertEqual(len(matches), 1)
        self.assertEqual(str(matches[0].PatientID), self.patient.uhid)
        self.assertEqual(matches[0].ScheduledProcedureStepSequence[0].ScheduledStationAETitle, 'XRROOM')
        self.assertEqual(responses[-1][0], 0x0000)

    def test_platform_saves_connection_and_records_echo(self):
        self.client.login(username='ris.admin', password='A-long-unique-password-2026!')
        page = self.client.get(f'/platform/modalities/?modality={self.modality.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'AE title')
        self.assertContains(page, 'IP address')
        self.assertContains(page, 'Test connection')
        saved = self.client.post('/platform/modalities/', {
            'action': 'update', 'modality': str(self.modality.pk),
            'name': 'X-ray Room 1', 'ae_title': 'xr_console', 'host': '10.0.0.8', 'port': '11112',
            'worklist_enabled': 'on', 'is_active': 'on',
        })
        self.assertEqual(saved.status_code, 302)
        self.modality.refresh_from_db()
        self.assertEqual(self.modality.ae_title, 'XR_CONSOLE')
        self.assertEqual(self.modality.host, '10.0.0.8')
        self.assertEqual(self.modality.port, 11112)
        with patch('radiology.dicom.echo_modality', return_value=(True, 'DICOM C-ECHO succeeded.')):
            tested = self.client.post('/platform/modalities/', {
                'action': 'test_connection', 'modality': str(self.modality.pk),
            })
        self.assertEqual(tested.status_code, 302)
        self.modality.refresh_from_db()
        self.assertTrue(self.modality.last_echo_ok)
        self.assertIn('C-ECHO', self.modality.last_echo_message)
