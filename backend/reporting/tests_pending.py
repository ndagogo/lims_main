from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, LabResult, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, Visit
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport
from reporting.pending import build_pending_results

User = get_user_model()


class PendingResultsReportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='pending.admin', email='pending.admin@example.test',
            password='A-long-unique-password-2026!',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        self.centre = Centre.objects.create(code='PEN', name='Pending Centre')
        UserCentreAccess.objects.create(
            user=self.admin, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
        )
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='7', is_active=True)
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(
            code='PNDFBC', name='Pending Blood Count', department='HAEMATOLOGY',
            department_code='1', service_group=group, specimen='Whole Blood',
        )
        self.patient = Patient.objects.create(first_name='Pending', last_name='Patient', phone_number='08011112222')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.admin)
        order = LabOrder.objects.create(visit=self.visit, test=self.test, price='1000')
        self.sample = Sample.objects.create(order=order, centre=self.centre, status='PROCESSING')
        self.result = LabResult.objects.create(sample=self.sample, status='DRAFT', entered_by=self.admin)

        modality = ImagingModality.objects.create(centre=self.centre, code='CT2', name='CT', modality_type='CT')
        self.imaging = ImagingOrder.objects.create(visit=self.visit, modality=modality, requested_by=self.admin)
        exam = RadiologyExam.objects.create(order=self.imaging, status='ACQUIRED')
        RadiologyReport.objects.create(exam=exam, author=self.admin, status='FINAL', findings='x')

    def test_build_includes_lab_and_radiology(self):
        payload = build_pending_results(self.admin)
        self.assertGreaterEqual(payload['summary']['laboratory'], 1)
        self.assertGreaterEqual(payload['summary']['radiology'], 1)
        self.assertTrue(any(r['department'] == 'Laboratory' for r in payload['rows']))
        self.assertTrue(any(r['department'] == 'Radiology' for r in payload['rows']))

    def test_department_filter(self):
        payload = build_pending_results(self.admin, department='RADIOLOGY')
        self.assertEqual(payload['summary']['laboratory'], 0)
        self.assertGreaterEqual(payload['summary']['radiology'], 1)

    def test_screen_and_csv_export(self):
        client = Client()
        client.force_login(self.admin)
        page = client.get('/reporting/pending/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Pending results')
        self.assertContains(page, 'Pending Blood Count')
        csv_resp = client.get('/reporting/pending/?format=csv')
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn('text/csv', csv_resp['Content-Type'])
        self.assertIn(b'Laboratory', csv_resp.content)


class IdleTimeoutSettingsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='idle.admin', email='idle.admin@example.test',
            password='A-long-unique-password-2026!',
        )

    def test_organization_idle_timeout_default_and_save(self):
        from core.organization import idle_timeout_minutes, save_organization_profile
        self.assertEqual(idle_timeout_minutes(), 10)
        save_organization_profile(idle_timeout_minutes=15)
        self.assertEqual(idle_timeout_minutes(), 15)
        client = Client()
        client.force_login(self.admin)
        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'idle-logout-form')
