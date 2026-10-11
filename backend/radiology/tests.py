from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import Centre, UserCentreAccess
from patients.models import Patient, Visit
from .models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport

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
        self.exam = RadiologyExam.objects.create(order=self.order, assigned_radiographer=self.radiographer, status='ASSIGNED')
        self.client = APIClient()

    def test_radiographer_must_start_and_end_exam_before_report(self):
        self.client.force_authenticate(self.radiographer)
        start = self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.assertEqual(start.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'IN_PROGRESS')
        end = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {'acquisition_notes': 'Images acquired'}, format='json')
        self.assertEqual(end.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'ACQUIRED')

    def test_report_edit_is_restricted_to_author(self):
        self.exam.status = 'ACQUIRED'; self.exam.save(update_fields=['status'])
        report = RadiologyReport.objects.create(exam=self.exam, author=self.user, findings='Initial findings', impression='Initial impression')
        self.client.force_authenticate(self.other)
        response = self.client.patch(f'/api/radiology-reports/{report.pk}/', {'findings': 'Changed by another author'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_final_report_requires_controlled_override_and_reason(self):
        self.exam.status = 'ACQUIRED'; self.exam.save(update_fields=['status'])
        report = RadiologyReport.objects.create(exam=self.exam, author=self.user, status='FINAL', findings='Finding', impression='Impression')
        self.client.force_authenticate(self.user)
        ordinary = self.client.patch(f'/api/radiology-reports/{report.pk}/', {'findings': 'Edited final'}, format='json')
        self.assertEqual(ordinary.status_code, 400)
        override = self.client.post(f'/api/radiology-reports/{report.pk}/override/', {'findings': 'Amended finding', 'reason': 'Correction after image re-review'}, format='json')
        self.assertEqual(override.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, 'AMENDED')
        self.assertEqual(report.version, 2)
        self.assertEqual(report.revisions.count(), 1)

    def test_imaging_order_cannot_use_another_centre_modality(self):
        other_centre = Centre.objects.create(code='IND', name='Independence')
        other_modality = ImagingModality.objects.create(centre=other_centre, code='XR1', name='Other X-ray', modality_type='XRAY')
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/imaging-orders/', {'visit': str(self.visit.pk), 'modality': str(other_modality.pk)}, format='json')
        self.assertEqual(response.status_code, 400)
