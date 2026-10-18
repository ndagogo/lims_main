from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Centre, UserCentreAccess
from patients.models import Patient, PatientDocument, Visit
from radiology.modality_templates import ensure_modality_report_templates
from radiology.models import (
    ImagingModality, ImagingOrder, RadiologyExam, RadiologyExamActivity,
    RadiologyReport, RadiologyReportTemplate,
)
from radiology.templates_svc import clone_template_version, publish_template

User = get_user_model()


class RadiologyWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='radiologist1', email='rad1@example.test', password='A-long-unique-password-2026!')
        self.radiographer = User.objects.create_user(username='radiographer1', email='tech1@example.test', password='A-long-unique-password-2026!')
        self.other = User.objects.create_user(username='radiologist2', email='rad2@example.test', password='A-long-unique-password-2026!')
        radiologist = Group.objects.get_or_create(name='Radiologist')[0]
        radiographer = Group.objects.get_or_create(name='Radiographer')[0]
        self.user.groups.add(radiologist)
        self.other.groups.add(radiologist)
        self.radiographer.groups.add(radiographer)
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True)
        UserCentreAccess.objects.create(user=self.radiographer, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=False)
        UserCentreAccess.objects.create(user=self.other, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True)
        patient = Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08000000001')
        self.visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.user)
        self.modality = ImagingModality.objects.create(centre=self.centre, code='XR1', name='X-ray Room 1', modality_type='XRAY')
        self.order = ImagingOrder.objects.create(visit=self.visit, modality=self.modality, requested_by=self.user)
        self.exam = RadiologyExam.objects.create(
            order=self.order, assigned_radiographer=self.radiographer,
            assigned_radiologist=self.user, status='ASSIGNED',
        )
        self.client = APIClient()

    def test_radiographer_must_start_and_end_exam_before_report(self):
        self.client.force_authenticate(self.radiographer)
        start = self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.assertEqual(start.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'IN_PROGRESS')
        self.assertTrue(RadiologyExamActivity.objects.filter(exam=self.exam, action='START_EXAM').exists())
        end = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'acquisition_notes': 'Images acquired',
            'supplies': [{'name': 'Film', 'quantity': 2}],
        }, format='json')
        self.assertEqual(end.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'ACQUIRED')
        self.assertTrue(RadiologyExamActivity.objects.filter(exam=self.exam, action='END_EXAM').exists())
        uploaded = self.client.post(f'/api/radiology-exams/{self.exam.pk}/upload-pacs/', {}, format='json')
        self.assertEqual(uploaded.status_code, 200)
        self.client.force_authenticate(self.user)
        accepted = self.client.post(f'/api/radiology-exams/{self.exam.pk}/accept/', {}, format='json')
        self.assertEqual(accepted.status_code, 200)
        create = self.client.post('/api/radiology-reports/', {
            'exam': str(self.exam.pk), 'findings': 'Clear', 'impression': 'Normal',
        }, format='json')
        self.assertEqual(create.status_code, 201)

    def test_report_blocked_until_exam_acquired(self):
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/radiology-reports/', {
            'exam': str(self.exam.pk), 'findings': 'x', 'impression': 'y',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_only_assigned_radiologist_or_substitute_may_edit_unsigned(self):
        self.exam.status = 'ACQUIRED'
        self.exam.save(update_fields=['status'])
        report = RadiologyReport.objects.create(
            exam=self.exam, author=self.user, findings='Initial findings', impression='Initial impression',
        )
        self.client.force_authenticate(self.other)
        denied = self.client.patch(
            f'/api/radiology-reports/{report.pk}/', {'findings': 'Changed by another author'}, format='json',
        )
        self.assertEqual(denied.status_code, 403)
        # Authorise substitute under controlled policy, then edit succeeds.
        self.client.force_authenticate(self.user)
        auth = self.client.post(f'/api/imaging-orders/{self.order.pk}/authorize-substitute/', {
            'substitute': self.other.pk, 'reason': 'On-call cover for assigned reporter',
        }, format='json')
        self.assertEqual(auth.status_code, 200, getattr(auth, 'data', auth.content))
        self.client.force_authenticate(self.other)
        ok = self.client.patch(
            f'/api/radiology-reports/{report.pk}/', {'findings': 'Edited by substitute'}, format='json',
        )
        self.assertEqual(ok.status_code, 200, getattr(ok, 'data', ok.content))
        report.refresh_from_db()
        self.assertEqual(report.findings, 'Edited by substitute')

    def test_sign_then_release_amendment_and_addendum(self):
        PatientDocument.objects.create(
            visit=self.visit, document_type='REFERRAL',
            file=ContentFile(b'%PDF-1.4\n', name='referral.pdf'), uploaded_by=self.user,
        )
        self.exam.status = 'ACQUIRED'
        self.exam.save(update_fields=['status'])
        report = RadiologyReport.objects.create(
            exam=self.exam, author=self.user, findings='Finding', impression='Impression',
            indication='Cough', technique='PA chest',
        )
        self.client.force_authenticate(self.user)
        signed = self.client.post(f'/api/radiology-reports/{report.pk}/finalize/', {}, format='json')
        self.assertEqual(signed.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, 'FINAL')
        self.assertTrue(report.configuration_snapshot)
        blocked = self.client.post(f'/api/radiology-reports/{report.pk}/release/', {}, format='json')
        self.assertEqual(blocked.status_code, 200, getattr(blocked, 'data', blocked.content))
        report.refresh_from_db()
        self.assertEqual(report.status, 'RELEASED')
        # Direct edit of signed report blocked.
        edit = self.client.patch(f'/api/radiology-reports/{report.pk}/', {'findings': 'sneak'}, format='json')
        self.assertEqual(edit.status_code, 400)
        self.client.force_authenticate(self.other)
        amendment = self.client.post(f'/api/radiology-reports/{report.pk}/override/', {
            'findings': 'Amended finding', 'reason': 'Correction after image re-review',
        }, format='json')
        self.assertEqual(amendment.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, 'AMENDED')
        self.assertEqual(report.version, 2)
        self.assertEqual(report.revisions.filter(change_type='AMENDMENT').count(), 1)
        addendum = self.client.post(f'/api/radiology-reports/{report.pk}/addendum/', {
            'addendum_text': 'Additional clinical correlation noted.',
            'reason': 'Addendum after MDT discussion',
        }, format='json')
        self.assertEqual(addendum.status_code, 200, getattr(addendum, 'data', addendum.content))
        report.refresh_from_db()
        self.assertEqual(report.version, 3)
        self.assertIn('MDT', report.addendum)
        self.assertEqual(report.revisions.filter(change_type='ADDENDUM').count(), 1)

    def test_open_laboratory_workflow_does_not_block_imaging_dispatch(self):
        from unittest.mock import patch
        from billing.eligibility import assert_radiology_report_releasable
        PatientDocument.objects.create(
            visit=self.visit, document_type='REGISTRATION',
            file=ContentFile(b'%PDF-1.4\n', name='form.pdf'), uploaded_by=self.user,
        )
        self.exam.status = 'ACQUIRED'
        self.exam.pacs_uploaded_at = self.exam.updated_at
        self.exam.accepted_at = self.exam.updated_at
        self.exam.save(update_fields=['status', 'pacs_uploaded_at', 'accepted_at'])
        report = RadiologyReport.objects.create(
            exam=self.exam, author=self.user, findings='Finding', impression='Impression',
        )
        with patch('workflows.services.workflow_blocks_release', return_value='Order CBC is still at workflow step Collected.'):
            self.assertTrue(assert_radiology_report_releasable(report))
        self.client.force_authenticate(self.user)
        signed = self.client.post(f'/api/radiology-reports/{report.pk}/finalize/', {}, format='json')
        self.assertEqual(signed.status_code, 200)
        with patch('workflows.services.workflow_blocks_release', return_value='Order CBC is still at workflow step Collected.'):
            released = self.client.post(f'/api/radiology-reports/{report.pk}/release/', {}, format='json')
        self.assertEqual(released.status_code, 200, getattr(released, 'data', released.content))

    def test_imaging_order_cannot_use_another_centre_modality(self):
        other_centre = Centre.objects.create(code='IND', name='Independence')
        other_modality = ImagingModality.objects.create(
            centre=other_centre, code='XR1', name='Other X-ray', modality_type='XRAY',
        )
        self.client.force_authenticate(self.user)
        response = self.client.post(
            '/api/imaging-orders/',
            {'visit': str(self.visit.pk), 'modality': str(other_modality.pk)},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_modality_specific_platform_templates(self):
        rows = ensure_modality_report_templates(publish=True)
        types = {r.modality_type for r in rows}
        self.assertTrue({'XRAY', 'CT', 'MRI', 'US', 'MAMMO'}.issubset(types))
        mammo = RadiologyReportTemplate.objects.get(modality_type='MAMMO', name='Standard Mammography', version=1)
        self.assertTrue(any(f.get('code') == 'BI_RADS' for f in mammo.extra_fields))
        self.assertTrue(mammo.indication_boilerplate)
        self.assertTrue(mammo.technique_boilerplate)
        self.assertTrue(mammo.body_html)
        self.assertEqual(mammo.body_part, 'Breast')
        personal = clone_template_version(mammo, user=self.user, personal=True, name='My Mammo')
        self.assertEqual(personal.extra_fields, mammo.extra_fields)
        self.assertEqual(personal.body_html, mammo.body_html)
        publish_template(personal, self.user)
        self.exam.status = 'ACQUIRED'
        self.exam.pacs_uploaded_at = self.exam.updated_at
        self.exam.accepted_at = self.exam.updated_at
        self.exam.order.modality.modality_type = 'MAMMO'
        self.exam.order.modality.save(update_fields=['modality_type', 'updated_at'])
        self.exam.save(update_fields=['status', 'pacs_uploaded_at', 'accepted_at'])
        self.client.force_authenticate(self.user)
        create = self.client.post('/api/radiology-reports/', {
            'exam': str(self.exam.pk), 'template': personal.pk,
        }, format='json')
        self.assertEqual(create.status_code, 201, getattr(create, 'data', create.content))
        report = RadiologyReport.objects.get(pk=create.data['id'])
        self.assertTrue(report.indication)
        self.assertIn('BI_RADS', report.extra_values)
        self.assertIn('<strong>EXAM:</strong>', report.findings)

    def test_finalize_freezes_format_and_signature_meta(self):
        from django.core.files.base import ContentFile
        from core.report_format import get_report_format

        fmt = get_report_format(self.centre)
        fmt.layout_style = 'MODERN'
        fmt.title_radiology = 'NOWAS Imaging Report'
        fmt.save()
        self.user.job_title = 'Consultant Radiologist'
        self.user.signature_title = 'Consultant Radiologist'
        self.user.qualifications = 'MBBS, FWACS'
        self.user.signature.save('sig.png', ContentFile(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
            b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        ), save=True)
        self.exam.status = 'ACQUIRED'
        self.exam.save(update_fields=['status'])
        report = RadiologyReport.objects.create(
            exam=self.exam, author=self.user, findings='Clear lungs', impression='Normal study',
        )
        self.client.force_authenticate(self.user)
        signed = self.client.post(f'/api/radiology-reports/{report.pk}/finalize/', {}, format='json')
        self.assertEqual(signed.status_code, 200, getattr(signed, 'data', signed.content))
        report.refresh_from_db()
        snap = report.configuration_snapshot
        self.assertEqual(snap['format']['layout_style'], 'MODERN')
        self.assertEqual(snap['format']['title_radiology'], 'NOWAS Imaging Report')
        self.assertEqual(snap['authenticator']['title'], 'Consultant Radiologist')
        self.assertEqual(snap['authenticator']['qualifications'], 'MBBS, FWACS')
        self.assertTrue(snap['authenticator']['has_signature'])
        pdf = self.client.get(f'/api/radiology-reports/{report.pk}/pdf/')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_printed_report_aligns_identity_and_keeps_markup(self):
        from datetime import date
        from referrals.models import ReferralAttribution, ReferralDoctor

        patient = self.visit.patient
        patient.date_of_birth = date(1990, 4, 12)
        patient.sex = 'Female'
        patient.title = 'Mrs'
        patient.save(update_fields=['date_of_birth', 'sex', 'title', 'updated_at'])
        doctor = ReferralDoctor.objects.create(
            centre=self.centre, doctor_code='DR-ADA', title='Dr', first_name='Ada', last_name='Nwosu',
            facility_name='City Clinic', commission_value=0,
        )
        ReferralAttribution.objects.create(visit=self.visit, doctor=doctor, recorded_by=self.user)
        self.exam.status = 'ACQUIRED'
        self.exam.pacs_uploaded_at = self.exam.updated_at
        self.exam.accepted_at = self.exam.updated_at
        self.exam.save(update_fields=['status', 'pacs_uploaded_at', 'accepted_at'])
        report = RadiologyReport.objects.create(
            exam=self.exam, author=self.user, status='FINAL',
            findings='<p><strong>Lungs:</strong> Clear.</p><p>Heart size is normal.</p>',
            impression='Normal chest radiograph',
        )
        self.client.force_authenticate(self.user)
        pdf = self.client.get(f'/api/radiology-reports/{report.pk}/pdf/')
        self.assertEqual(pdf.status_code, 200, pdf.content[:300])
        text = pdf.content.decode('latin-1', errors='ignore')
        self.assertIn('OKAFOR', text)
        self.assertIn('Female', text)
        self.assertIn('Dr Ada Nwosu', text)
        self.assertIn('Lungs:', text)
        self.assertIn(self.visit.visit_id, text)
        self.assertIn('X-ray', text)

    def test_rich_template_builder_update_and_variant(self):
        from django.test import Client
        tpl = RadiologyReportTemplate.objects.create(
            modality_type='CT', name='ABUAD RADIOLOGY', variant_name='', body_part='Abdomen',
            visibility='PUBLIC', version=1, status='DRAFT', scope='PERSONAL', owner=self.user,
            body_html='<p><strong>EXAM:</strong> CT Abdomen</p>', is_active=True,
        )
        web = Client()
        web.force_login(self.user)
        update = web.post(f'/radiology/templates/{tpl.pk}/', {
            'action': 'update',
            'name': 'ABUAD RADIOLOGY',
            'variant_name': 'Adult',
            'modality_type': 'CT',
            'body_part': 'Abdomen',
            'visibility': 'PUBLIC',
            'is_active': 'on',
            'body_html': '<p><strong>EXAM:</strong> CT Aortogram of the abdomen.</p><p><em>Italic note</em></p>',
        })
        self.assertEqual(update.status_code, 302)
        tpl.refresh_from_db()
        self.assertEqual(tpl.variant_name, 'Adult')
        self.assertIn('Aortogram', tpl.body_html)
        self.assertIn('<em>Italic note</em>', tpl.body_html)
        variant = web.post(f'/radiology/templates/{tpl.pk}/', {
            'action': 'save_variant',
            'name': 'ABUAD RADIOLOGY',
            'variant_name': 'Paediatric',
            'modality_type': 'CT',
            'body_part': 'Abdomen',
            'visibility': 'PRIVATE',
            'is_active': 'on',
            'body_html': '<p><u>Underlined paediatric variant</u></p>',
        })
        self.assertEqual(variant.status_code, 302)
        clone = RadiologyReportTemplate.objects.filter(
            owner=self.user, name='ABUAD RADIOLOGY', variant_name='Paediatric',
        ).exclude(pk=tpl.pk).first()
        self.assertIsNotNone(clone)
        self.assertEqual(clone.visibility, 'PRIVATE')
        self.assertIn('Underlined', clone.body_html)
        tpl.refresh_from_db()
        self.assertEqual(tpl.variant_name, 'Adult')  # original unchanged by save_variant
