from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.base import ContentFile
from django.test import Client, TestCase

from core.models import Centre, ResultLedger, UserCentreAccess
from laboratory.models import LabOrder, LabResult, Sample, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, PatientDocument, Visit
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport

User = get_user_model()


class ResultApprovalTests(TestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username='reporter', email='reporter@example.test', password='A-long-unique-password-2026!')
        self.head = User.objects.create_user(username='radhead', email='radhead@example.test', password='A-long-unique-password-2026!')
        self.dispatcher = User.objects.create_user(username='dispatcher', email='dispatcher@example.test', password='A-long-unique-password-2026!')
        self.manager = User.objects.create_user(username='labmgr', email='labmgr@example.test', password='A-long-unique-password-2026!')
        radiologist = Group.objects.get_or_create(name='Radiologist')[0]
        manager = Group.objects.get_or_create(name='Lab Manager')[0]
        self.reporter.groups.add(radiologist)
        self.head.groups.add(radiologist)
        self.manager.groups.add(manager)
        self.head.user_permissions.add(Permission.objects.get(codename='approve_result', content_type__app_label='radiology'))
        self.dispatcher.user_permissions.add(Permission.objects.get(codename='dispatch_result', content_type__app_label='radiology'))
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        for person in (self.reporter, self.head, self.dispatcher, self.manager):
            UserCentreAccess.objects.create(
                user=person, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
            )
        patient = Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08000000021')
        self.visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.reporter)
        modality = ImagingModality.objects.create(centre=self.centre, code='XR2', name='X-ray', modality_type='XRAY')
        order = ImagingOrder.objects.create(visit=self.visit, modality=modality, requested_by=self.reporter)
        exam = RadiologyExam.objects.create(order=order, status='ACQUIRED', assigned_radiologist=self.reporter)
        self.report = RadiologyReport.objects.create(
            exam=exam, author=self.reporter, status='FINAL', findings='Finding', impression='Impression',
        )
        self.web = Client()

    def test_platform_admin_sees_approve_on_the_radiology_page(self):
        admin = User.objects.create_superuser(
            username='platform.admin.test', email='platform.admin.test@example.test',
            password='A-long-unique-password-2026!',
        )
        self.web.force_login(admin)
        page = self.web.get('/radiology/')
        self.assertContains(page, 'Approve reported results')
        self.assertContains(page, 'Approve Result')
        self.assertContains(page, self.visit.visit_id)

    def test_preview_is_finished_pdf_with_watermark_and_radiologist_cannot_dispatch(self):
        from core.report_format import get_report_format
        get_report_format(self.centre)  # ensure UNVERIFIED default exists
        self.web.force_login(self.reporter)
        preview = self.web.get(f'/radiology/reports/{self.report.pk}/preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['Content-Type'], 'application/pdf')
        self.assertTrue(preview.content.startswith(b'%PDF'))
        self.assertIn(b'UNVERIFIED', preview.content)
        self.assertIn('inline', preview.get('Content-Disposition', ''))
        self.assertNotIn('attachment', preview.get('Content-Disposition', ''))
        denied = self.web.post('/results/dispatch/', {'action': 'dispatch_radiology', 'report': self.report.pk})
        self.assertEqual(denied.status_code, 403)

    def test_approve_puts_result_on_dispatch_dashboard(self):
        self.web.force_login(self.head)
        page = self.web.get('/radiology/')
        self.assertContains(page, 'Preview finished report')
        approved = self.web.post('/radiology/', {'action': 'approve', 'report': self.report.pk})
        self.assertEqual(approved.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'APPROVED')
        self.web.force_login(self.dispatcher)
        desk = self.web.get('/results/dispatch/')
        self.assertContains(desk, self.visit.visit_id)
        self.assertContains(desk, 'Ready to dispatch')
        self.assertContains(desk, 'Dispatch —')
        self.assertContains(desk, 'Dispatch to recipients')
        self.assertContains(desk, 'Dispatch rules')
        self.assertContains(desk, 'Dispatch history')
        self.assertContains(desk, 'Download CSV')

    def test_approve_then_repeat_dispatch_keeps_every_click(self):
        self.web.force_login(self.head)
        approved = self.web.post('/radiology/', {'action': 'approve', 'report': self.report.pk})
        self.assertEqual(approved.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'APPROVED')
        self.web.force_login(self.dispatcher)
        PatientDocument.objects.create(
            visit=self.visit, document_type='REGISTRATION',
            file=ContentFile(b'%PDF-1.4\n', name='form.pdf'), uploaded_by=self.dispatcher,
        )
        first = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch', 'item': f'rad:{self.report.pk}',
            'recipients': ['self'], 'channels_self': ['HARDCOPY'], 'link_expiry': '7',
        })
        self.assertEqual(first.status_code, 302)
        second_user = User.objects.create_user(username='nextday', email='nextday@example.test', password='A-long-unique-password-2026!')
        second_user.user_permissions.add(Permission.objects.get(codename='dispatch_result', content_type__app_label='radiology'))
        UserCentreAccess.objects.create(user=second_user, centre=self.centre, can_view=True, can_edit=True)
        self.web.force_login(second_user)
        again = self.web.post('/results/dispatch/', {
            'action': 'compose_dispatch', 'item': f'rad:{self.report.pk}',
            'recipients': ['self'], 'channels_self': ['HARDCOPY'], 'link_expiry': '7',
        })
        self.assertEqual(again.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'RELEASED')
        rows = ResultLedger.objects.filter(kind='DISPATCH', object_id=str(self.report.pk))
        self.assertEqual(rows.count(), 2)
        self.assertEqual(set(rows.values_list('actor_id', flat=True)), {self.dispatcher.pk, second_user.pk})

    def test_modified_approval_requires_a_reason_and_returns_the_report(self):
        self.report.status = 'RELEASED'
        self.report.save(update_fields=['status'])
        self.web.force_login(self.reporter)
        desk = self.web.get('/results/modify/')
        self.assertContains(desk, 'Modification history')
        self.assertContains(desk, 'Returned today')
        self.assertContains(desk, 'Download CSV')
        missing = self.web.post('/results/modify/', {
            'action': 'modify_radiology', 'visit_id': self.visit.visit_id, 'report': self.report.pk, 'reason': '',
        })
        self.assertEqual(missing.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'RELEASED')
        returned = self.web.post('/results/modify/', {
            'action': 'modify_radiology', 'visit_id': self.visit.visit_id,
            'report': self.report.pk, 'reason': 'Clinical detail was left out',
        })
        self.assertEqual(returned.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'DRAFT')
        self.assertTrue(ResultLedger.objects.filter(kind='MODIFY', reason='Clinical detail was left out').exists())
        history = self.web.get('/results/modify/')
        self.assertContains(history, 'Clinical detail was left out')
        self.assertContains(history, self.visit.visit_id)
        export = self.web.get('/results/modify/history/export/?fmt=csv')
        self.assertEqual(export.status_code, 200)
        self.assertIn(b'Clinical detail was left out', export.content)

    def test_laboratory_result_can_be_returned_by_visit_id(self):
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        test = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        order = LabOrder.objects.create(visit=self.visit, test=test, price='1000', status='COMPLETED')
        sample = Sample.objects.create(order=order, centre=self.centre, status='RELEASED')
        result = LabResult.objects.create(sample=sample, status='RELEASED', narrative='Hb 12')
        self.web.force_login(self.manager)
        page = self.web.get(f'/results/modify/?visit_id={self.visit.visit_id}')
        self.assertContains(page, 'Complete Blood Count')
        self.assertContains(page, 'Reason For Modification')
        saved = self.web.post('/results/modify/', {
            'action': 'modify_laboratory', 'visit_id': self.visit.visit_id,
            'result': result.pk, 'reason': 'Value needs a rerun',
        })
        self.assertEqual(saved.status_code, 302)
        result.refresh_from_db()
        self.assertEqual(result.status, 'DRAFT')

    def test_worklist_filters_request_form_and_stats_export(self):
        PatientDocument.objects.create(
            visit=self.visit, document_type='REGISTRATION',
            file=ContentFile(b'%PDF-1.4\n', name='request.pdf'), uploaded_by=self.reporter,
        )
        exam = self.report.exam
        exam.pacs_uploaded_at = exam.updated_at
        exam.save(update_fields=['pacs_uploaded_at'])
        self.web.force_login(self.reporter)
        page = self.web.get(f'/radiology/?visit={self.visit.visit_id}&status=REPORTED&modality=ALL&when=all&desk=report')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.visit.visit_id)
        self.assertContains(page, 'View Request Form')
        self.assertContains(page, 'Reported')
        export = self.web.get(f'/radiology/stats/export/?fmt=csv&visit={self.visit.visit_id}&status=REPORTED')
        self.assertEqual(export.status_code, 200)
        self.assertIn(b'REPORTED', export.content)
        self.assertIn(self.visit.visit_id.encode(), export.content)
