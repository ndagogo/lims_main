"""Result dispatch desk: payment gate, Self/Third-party, multi-select audit."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone

from billing.eligibility import PREVIEW_BLOCKED_MESSAGE, UNPAID_DISPATCH_MESSAGE, check_visit_release
from billing.models import Invoice, InvoiceItem
from core.models import AuditEvent, Centre, ResultLedger, UserCentreAccess
from laboratory.models import LabOrder, LabResult, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import OrganisationClient, Patient, PatientDocument, Visit
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport

User = get_user_model()


class DispatchDeskPaymentAndComposeTests(TestCase):
    def setUp(self):
        self.dispatcher = User.objects.create_user(
            username='desk.dispatcher', email='desk.dispatcher@example.test',
            password='A-long-unique-password-2026!',
        )
        self.dispatcher.user_permissions.add(
            Permission.objects.get(codename='dispatch_result', content_type__app_label='radiology'),
        )
        self.dispatcher.groups.add(Group.objects.get_or_create(name='Lab Manager')[0])
        self.centre = Centre.objects.create(code='DSP', name='Dispatch Centre')
        UserCentreAccess.objects.create(
            user=self.dispatcher, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
        )
        self.patient = Patient.objects.create(first_name='Ngozi', last_name='Eze', phone_number='08031112233')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.dispatcher)
        modality = ImagingModality.objects.create(centre=self.centre, code='XR', name='X-ray', modality_type='XRAY')
        self.imaging_order = ImagingOrder.objects.create(
            visit=self.visit, modality=modality, requested_by=self.dispatcher,
        )
        exam = RadiologyExam.objects.create(order=self.imaging_order, status='ACQUIRED', assigned_radiologist=self.dispatcher)
        self.report = RadiologyReport.objects.create(
            exam=exam, author=self.dispatcher, status='APPROVED',
            findings='Clear', impression='Normal', approved_at=timezone.now(),
        )
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        self.lab_order = LabOrder.objects.create(
            visit=self.visit, test=self.test, price=Decimal('1500'), status='COMPLETED',
        )
        sample = Sample.objects.create(order=self.lab_order, centre=self.centre, status='RELEASED')
        self.lab_result = LabResult.objects.create(
            sample=sample, status='AUTHENTICATED', narrative='Hb 13',
            authenticated_at=timezone.now(),
        )
        self.web = Client()
        self.web.force_login(self.dispatcher)

    def _attach_form(self):
        return PatientDocument.objects.create(
            visit=self.visit, document_type='REGISTRATION',
            file=ContentFile(b'%PDF-1.4\n', name='form.pdf'), uploaded_by=self.dispatcher,
        )

    def _unpaid_invoice(self):
        invoice = Invoice.objects.create(
            visit=self.visit, payer_name='Ngozi', status='UNPAID', created_by=self.dispatcher,
        )
        InvoiceItem.objects.create(
            invoice=invoice, imaging_order=self.imaging_order, unit_price=Decimal('5000'),
            description='X-ray',
        )
        invoice.refresh_from_db()
        return invoice

    def test_unpaid_bill_blocks_dispatch_and_preview_alert(self):
        self._attach_form()
        self._unpaid_invoice()
        blockers = check_visit_release(self.visit, check_workflow=False)
        self.assertTrue(any(b.code == 'unpaid_invoice' for b in blockers))

        desk = self.web.get(f'/results/dispatch/?item=rad:{self.report.pk}')
        self.assertEqual(desk.status_code, 200)
        self.assertContains(desk, 'data-preview-blocked')
        self.assertContains(desk, PREVIEW_BLOCKED_MESSAGE)
        self.assertContains(desk, 'Self')
        self.assertContains(desk, 'Third-party')
        self.assertNotContains(desk, 'name="recipients"')

        blocked = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch',
            'items': [f'rad:{self.report.pk}'],
            'recipient_mode': 'self',
            'channels_self': ['HARDCOPY'],
            'link_expiry': '7',
        }, follow=True)
        self.assertContains(blocked, 'not fully paid')
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'APPROVED')

        preview = self.web.get(f'/radiology/reports/{self.report.pk}/preview/')
        self.assertEqual(preview.status_code, 403)
        self.assertIn(PREVIEW_BLOCKED_MESSAGE.encode(), preview.content)

    def test_self_and_third_party_modes_and_hmo_dropdown(self):
        self._attach_form()
        hmo = OrganisationClient.objects.create(
            centre=self.centre, kind='HMO', category='HMO', name='Hygeia HMO',
            report_email='reports@hygeia.test',
        )
        desk = self.web.get(f'/results/dispatch/?item=rad:{self.report.pk}')
        self.assertContains(desk, 'name="recipient_mode"')
        self.assertContains(desk, 'name="third_party_kind"')
        self.assertContains(desk, 'Family / Guardian')
        self.assertContains(desk, 'Hygeia HMO')
        self.assertContains(desk, 'Results for this visit')
        self.assertContains(desk, 'Complete Blood Count')
        self.assertContains(desk, 'Without header and footer')
        self.assertContains(desk, 'With centre letterhead')

        sent = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch',
            'items': [f'rad:{self.report.pk}'],
            'recipient_mode': 'third_party',
            'third_party_kind': 'hmo',
            'hmo_client': str(hmo.pk),
            'channels_hmo': ['HARDCOPY', 'EMAIL'],
            'link_expiry': '7',
        }, follow=True)
        self.assertEqual(sent.status_code, 200)
        self.assertContains(sent, 'Dispatched 1 result')
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'RELEASED')
        row = ResultLedger.objects.filter(kind='DISPATCH', object_id=str(self.report.pk)).latest('created_at')
        self.assertIn('HMO', row.summary)
        self.assertIn('Hygeia HMO', row.reason)
        batch = AuditEvent.objects.filter(action='RESULT_DISPATCH_BATCH', object_id=str(self.visit.pk)).latest('created_at')
        self.assertEqual(batch.detail.get('recipient'), 'Third-party · HMO · Hygeia HMO')

    def test_multi_select_dispatch_writes_detailed_audit(self):
        self._attach_form()
        payload = {
            'action': 'compose_dispatch',
            'items': [f'rad:{self.report.pk}', f'lab:{self.lab_result.pk}'],
            'recipient_mode': 'self',
            'channels_self': ['HARDCOPY'],
            'link_expiry': '7',
            'comment': 'Both films and CBC ready',
        }
        sent = self.web.post('/results/dispatch/', payload, follow=True)
        self.assertEqual(sent.status_code, 200)
        self.assertContains(sent, 'Dispatched 2 results')
        self.report.refresh_from_db()
        self.lab_result.refresh_from_db()
        self.assertEqual(self.report.status, 'RELEASED')
        self.assertEqual(self.lab_result.status, 'RELEASED')

        rad_row = ResultLedger.objects.filter(kind='DISPATCH', object_id=str(self.report.pk)).latest('created_at')
        lab_row = ResultLedger.objects.filter(kind='DISPATCH', object_id=str(self.lab_result.pk)).latest('created_at')
        self.assertIn('Dispatch selection (2)', rad_row.reason)
        self.assertIn('Complete Blood Count', rad_row.reason)
        self.assertIn('Both films and CBC ready', rad_row.reason)
        self.assertIn('Self', rad_row.summary)
        self.assertIn('Complete Blood Count', lab_row.summary)

        batch = AuditEvent.objects.filter(action='RESULT_DISPATCH_BATCH', object_id=str(self.visit.pk)).latest('created_at')
        self.assertEqual(batch.detail.get('selection_count'), 2)
        self.assertEqual(set(batch.new_values.get('items') or []), {f'rad:{self.report.pk}', f'lab:{self.lab_result.pk}'})

    def test_family_third_party_form_required(self):
        self._attach_form()
        missing = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch',
            'items': [f'rad:{self.report.pk}'],
            'recipient_mode': 'third_party',
            'third_party_kind': 'family',
            'channels_family': ['HARDCOPY'],
            'link_expiry': '7',
        }, follow=True)
        self.assertContains(missing, 'means of identification')
        id_copy = SimpleUploadedFile('nin.jpg', b'\xff\xd8\xff' + b'\x00' * 32, content_type='image/jpeg')
        sent = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch',
            'items': [f'rad:{self.report.pk}'],
            'recipient_mode': 'third_party',
            'third_party_kind': 'family',
            'channels_family': ['HARDCOPY'],
            'link_expiry': '7',
            'third_party_name': 'Ifeanyi Eze',
            'third_party_id_type': 'NIN',
            'third_party_id_number': '11223344556',
            'third_party_address': '12 Awolowo Road',
            'third_party_phone': '08039998877',
            'third_party_auth': 'Patient asked brother to collect',
            'third_party_id_file': id_copy,
        }, follow=True)
        self.assertContains(sent, 'View identification')
        row = ResultLedger.objects.filter(kind='DISPATCH', object_id=str(self.report.pk)).latest('created_at')
        self.assertIn('Ifeanyi Eze', row.reason)
        self.assertIn('Family/Guardian', row.reason)

    def test_unpaid_message_constant(self):
        self.assertIn('Contact the admin', UNPAID_DISPATCH_MESSAGE)
        self.assertEqual(PREVIEW_BLOCKED_MESSAGE, 'Contact the admin, there maybe pending issues. Thanks')
