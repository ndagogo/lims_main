"""Batch report PDF on dispatch."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from rest_framework.test import APIClient

from core.models import Centre, UserCentreAccess
from laboratory.batch_report import build_batch_report_pdf
from laboratory.models import LabOrder, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, Visit

User = get_user_model()


class BatchReportPdfTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='batch.report', email='batch.report@example.test', password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        self.reg = Centre.objects.create(code='IND', name='Independence Layout')
        self.proc = Centre.objects.create(code='NWS', name='NOWAS')
        SampleCodeConfig.objects.create(centre=self.proc, numeric_code='2')
        for centre in (self.reg, self.proc):
            UserCentreAccess.objects.create(user=self.user, centre=centre, can_view=True, can_edit=True)
        patient = Patient.objects.create(
            title='Mrs', first_name='Ada', last_name='Okoye', phone_number='08055556666',
            sex='Female', date_of_birth=date(1984, 1, 15),
        )
        visit = Visit.objects.create(patient=patient, centre=self.reg, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        test = Test.objects.create(
            code='CBC', name='Complete Blood Count', department='HAEMATOLOGY',
            department_code='1', service_group=group, specimen='Whole Blood',
        )
        order = LabOrder.objects.create(visit=visit, test=test, price='5500')
        self.sample = Sample.objects.create(
            order=order, centre=self.proc, status='COLLECTED', specimen_type='Whole Blood',
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        created = self.api.post(
            '/api/sample-batches/',
            {
                'centre': str(self.proc.pk),
                'department': 'HAEMATOLOGY',
                'sample_ids': [self.sample.sample_id],
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.batch_id = created.data['id']

    def test_dispatch_opens_batch_report_pdf(self):
        sent = self.api.post(f'/api/sample-batches/{self.batch_id}/dispatch/', {}, format='json')
        self.assertEqual(sent.status_code, 200)

        api_pdf = self.api.get(f'/api/sample-batches/{self.batch_id}/report/')
        self.assertEqual(api_pdf.status_code, 200)
        self.assertEqual(api_pdf['Content-Type'], 'application/pdf')
        self.assertTrue(api_pdf.content.startswith(b'%PDF'))

        screen = Client()
        screen.force_login(self.user)
        page_pdf = screen.get(f'/laboratory/batches/{self.batch_id}/report/')
        self.assertEqual(page_pdf.status_code, 200)
        self.assertTrue(page_pdf.content.startswith(b'%PDF'))

        from laboratory.batch_report import batch_report_header, batch_report_rows
        from laboratory.models import SampleBatch
        batch = SampleBatch.objects.get(pk=self.batch_id)
        built = build_batch_report_pdf(batch)
        self.assertTrue(built.startswith(b'%PDF'))
        self.assertIn(f'Batch Report {batch.batch_code}'.encode(), built)
        header = batch_report_header(batch)
        self.assertEqual(header['proc_centre'], 'NOWAS')
        self.assertEqual(header['reg_centre'], 'Independence Layout')
        rows = batch_report_rows(batch)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sample_id'], self.sample.sample_id)
        self.assertEqual(rows[0]['service_name'], 'Complete Blood Count')
        self.assertIn('ADA OKOYE', rows[0]['customer_name'])

    def test_workspace_dispatch_redirects_to_report(self):
        screen = Client()
        screen.force_login(self.user)
        response = screen.post('/laboratory/', {
            'action': 'dispatch',
            'stage': 'batch',
            'batch': self.batch_id,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/laboratory/batches/{self.batch_id}/report/', response['Location'])
