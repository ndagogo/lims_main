"""Bulk upload: template download, validate, duplicate skip, Validate and Save."""
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from core.models import Centre
from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
from patients.channel_seed import ensure_default_client_types
from patients.models import OrganisationClient
from radiology.models import ImagingProcedure
from referrals.models import ReferralDoctor


class EnterpriseBulkUploadTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.admin = User.objects.create_superuser(
            username='bulk.admin', email='bulk.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.outsider = User.objects.create_user(
            username='bulk.out', email='bulk.out@example.test', password='A-long-unique-password-2026!',
        )
        self.tariff = Tariff.objects.create(name='Walk-in Bulk', status='APPROVAL', is_active=True)
        ensure_default_client_types()
        self.client = Client()
        self.client.force_login(self.admin)

    def test_hub_requires_platform_admin(self):
        guest = Client()
        guest.force_login(self.outsider)
        response = guest.get('/enterprise/bulk-upload/')
        self.assertEqual(response.status_code, 403)

    def test_hub_and_template_download(self):
        hub = self.client.get('/enterprise/bulk-upload/?dataset=lab_tests')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Bulk upload')
        self.assertContains(hub, 'Laboratory tests')

        xlsx = self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'lab_tests', 'action': 'download_template', 'format': 'xlsx',
        })
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn('attachment', xlsx['Content-Disposition'])
        self.assertTrue(xlsx.content[:2] == b'PK')

        csv_resp = self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'lab_tests', 'action': 'download_template', 'format': 'csv',
        })
        self.assertEqual(csv_resp.status_code, 200)
        body = csv_resp.content.decode('utf-8-sig')
        self.assertIn('TestName', body.splitlines()[0])

    def test_lab_tests_validate_save_auto_code_and_duplicates(self):
        csv_bytes = (
            'TestName,ServiceGroupName,Price,TariffName\n'
            'Bulk Glucose,,4500,Walk-in Bulk\n'
            'Bulk Glucose,,4500,Walk-in Bulk\n'
            'Bulk Lipids,,,\n'
        ).encode('utf-8')
        uploaded = SimpleUploadedFile('tests.csv', csv_bytes, content_type='text/csv')
        validate = self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'lab_tests', 'action': 'validate', 'upload_file': uploaded,
        })
        self.assertEqual(validate.status_code, 302)
        preview = self.client.get('/enterprise/bulk-upload/?dataset=lab_tests')
        self.assertContains(preview, 'ready')
        self.assertContains(preview, 'duplicate')
        self.assertContains(preview, 'Validate and Save')

        save = self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'lab_tests', 'action': 'save',
        })
        self.assertEqual(save.status_code, 302)
        tests = list(Test.objects.filter(name__icontains='Bulk').order_by('name'))
        self.assertEqual(len(tests), 2)
        self.assertTrue(all(t.code for t in tests))
        priced = TestPrice.objects.filter(tariff=self.tariff, test__name='Bulk Glucose').first()
        self.assertIsNotNone(priced)
        self.assertEqual(priced.price, Decimal('4500.00'))
        self.assertTrue(ServiceGroup.objects.filter(name__iexact='General').exists())

        # Second upload of same names must mark duplicates and create nothing new.
        again = SimpleUploadedFile(
            'tests2.csv',
            b'TestName\nBulk Glucose\nBulk Lipids\n',
            content_type='text/csv',
        )
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'lab_tests', 'action': 'validate', 'upload_file': again,
        })
        preview2 = self.client.get('/enterprise/bulk-upload/?dataset=lab_tests')
        self.assertContains(preview2, 'duplicate')
        self.assertContains(preview2, 'Ready 0')
        self.assertContains(preview2, 'Duplicates 2')
        self.assertNotContains(preview2, 'action" value="save"')
        self.assertEqual(Test.objects.filter(name__icontains='Bulk').count(), 2)

    def test_imaging_doctors_clients_tariffs_roundtrip(self):
        # Imaging
        img = SimpleUploadedFile(
            'img.csv',
            b'ProcedureName,ModalityType\nBulk Chest PA,XRAY\n',
            content_type='text/csv',
        )
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'imaging_procedures', 'action': 'validate', 'upload_file': img,
        })
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'imaging_procedures', 'action': 'save',
        })
        proc = ImagingProcedure.objects.get(name='Bulk Chest PA')
        self.assertTrue(proc.code)

        # Doctors
        doc = SimpleUploadedFile(
            'docs.csv',
            b'FirstName,LastName,CentreCode,CommissionType,CommissionValue\nAda,BulkDoc,NWS,PERCENT,10\n',
            content_type='text/csv',
        )
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'referral_doctors', 'action': 'validate', 'upload_file': doc,
        })
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'referral_doctors', 'action': 'save',
        })
        doctor = ReferralDoctor.objects.get(first_name='Ada', last_name='BulkDoc')
        self.assertTrue(doctor.doctor_code)
        self.assertEqual(doctor.commission_value, Decimal('10'))

        # Clients
        client_csv = SimpleUploadedFile(
            'clients.csv',
            b'ClientName,CentreCode,ClientTypeCode,CreditLimit\nBulk Credit Co,NWS,CREDIT,100000\n',
            content_type='text/csv',
        )
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'clients', 'action': 'validate', 'upload_file': client_csv,
        })
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'clients', 'action': 'save',
        })
        org = OrganisationClient.objects.get(name='Bulk Credit Co')
        self.assertEqual(org.client_type_code, 'CREDIT')
        self.assertEqual(org.credit_limit, Decimal('100000'))

        # Tariffs
        tariff_csv = SimpleUploadedFile(
            'tariffs.csv',
            b'TariffName,CentreCode,Status\nBulk Tariff 2026,NWS,APPROVAL\n',
            content_type='text/csv',
        )
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'tariffs', 'action': 'validate', 'upload_file': tariff_csv,
        })
        self.client.post('/enterprise/bulk-upload/', {
            'dataset': 'tariffs', 'action': 'save',
        })
        tariff = Tariff.objects.get(name='Bulk Tariff 2026')
        self.assertTrue(tariff.code)
        self.assertEqual(tariff.status, 'APPROVAL')
