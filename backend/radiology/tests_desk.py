from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Centre, UserCentreAccess
from patients.models import Patient, Visit
from radiology.desk import release_expired_assignments
from radiology.models import ExamSupplyUse, ImagingModality, ImagingOrder, RadiologyDeskSetting, RadiologyExam

User = get_user_model()


class RadiologyDeskTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(username='tech.only', email='tech.only@example.test', password='A-long-unique-password-2026!')
        self.rad = User.objects.create_user(username='rad.only', email='rad.only@example.test', password='A-long-unique-password-2026!')
        self.other = User.objects.create_user(username='desk.other', email='desk.other@example.test', password='A-long-unique-password-2026!')
        self.tech.groups.add(Group.objects.get_or_create(name='Radiographer')[0])
        self.rad.groups.add(Group.objects.get_or_create(name='Radiologist')[0])
        self.other.groups.add(Group.objects.get_or_create(name='Front Desk')[0])
        self.centre = Centre.objects.create(code='DSK', name='Desk Centre')
        for person in (self.tech, self.rad, self.other):
            UserCentreAccess.objects.create(
                user=person, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
            )
        patient = Patient.objects.create(first_name='Ada', last_name='Bello', phone_number='08000000222')
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.tech)
        modality = ImagingModality.objects.create(centre=self.centre, code='CT1', name='CT', modality_type='CT')
        self.order = ImagingOrder.objects.create(visit=visit, modality=modality, requested_by=self.tech)
        self.exam = RadiologyExam.objects.create(order=self.order, status='ASSIGNED')
        self.client = APIClient()

    def test_only_radiographers_can_be_assigned_and_start(self):
        self.client.force_authenticate(self.tech)
        denied = self.client.post(f'/api/imaging-orders/{self.order.pk}/assign/', {'radiographer': self.rad.pk}, format='json')
        self.assertEqual(denied.status_code, 400)
        started = self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.assertEqual(started.status_code, 200)
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'supplies': [{'name': 'Syringe', 'quantity': 1}, {'name': 'Film', 'quantity': 2}],
        }, format='json')
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ExamSupplyUse.objects.filter(exam=self.exam).count(), 2)
        self.exam.refresh_from_db()
        self.assertIsNotNone(self.exam.ended_at)
        self.assertGreaterEqual(int((self.exam.ended_at - self.exam.started_at).total_seconds()), 0)

    def test_radiologist_queue_waits_for_pacs_and_accept_window(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {}, format='json')
        screen = Client()
        screen.force_login(self.rad)
        hidden = screen.get('/radiology/')
        self.assertNotContains(hidden, 'Ada')
        self.client.force_authenticate(self.tech)
        uploaded = self.client.post(f'/api/radiology-exams/{self.exam.pk}/upload-pacs/', {}, format='json')
        self.assertEqual(uploaded.status_code, 200)
        visible = screen.get('/radiology/')
        self.assertContains(visible, 'Ada')
        self.assertContains(visible, 'Templates')
        self.client.force_authenticate(self.rad)
        assigned = self.client.post(f'/api/imaging-orders/{self.order.pk}/assign-radiologist/', {'radiologist': self.rad.pk}, format='json')
        self.assertEqual(assigned.status_code, 200)
        RadiologyDeskSetting.objects.update_or_create(pk=1, defaults={'accept_minutes': 15})
        self.exam.refresh_from_db()
        self.exam.radiologist_assigned_at = timezone.now() - timedelta(minutes=20)
        self.exam.save(update_fields=['radiologist_assigned_at'])
        self.assertEqual(release_expired_assignments(), 1)
        self.exam.refresh_from_db()
        self.assertIsNone(self.exam.assigned_radiologist_id)

    def test_reassignment_stays_closed_until_the_accept_window_elapses(self):
        other = User.objects.create_user(username='rad.two', email='rad.two@example.test', password='A-long-unique-password-2026!')
        other.groups.add(Group.objects.get_or_create(name='Radiologist')[0])
        UserCentreAccess.objects.create(user=other, centre=self.centre, can_view=True, can_edit=True, can_approve=True)
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {'radiologist': str(self.rad.pk)}, format='json')
        self.assertEqual(ended.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.assigned_radiologist_id, self.rad.pk)
        self.assertIsNone(self.exam.radiologist_assigned_at)
        blocked = self.client.post(
            f'/api/imaging-orders/{self.order.pk}/assign-radiologist/',
            {'radiologist': str(other.pk)}, format='json',
        )
        self.assertEqual(blocked.status_code, 400)
        uploaded = self.client.post(f'/api/radiology-exams/{self.exam.pk}/upload-pacs/', {}, format='json')
        self.assertEqual(uploaded.status_code, 200)
        self.exam.refresh_from_db()
        self.assertIsNotNone(self.exam.radiologist_assigned_at)
        still = self.client.post(
            f'/api/imaging-orders/{self.order.pk}/assign-radiologist/',
            {'radiologist': str(other.pk)}, format='json',
        )
        self.assertEqual(still.status_code, 400)
        RadiologyDeskSetting.objects.update_or_create(pk=1, defaults={'accept_minutes': 15})
        self.exam.radiologist_assigned_at = timezone.now() - timedelta(minutes=20)
        self.exam.save(update_fields=['radiologist_assigned_at'])
        opened = self.client.post(
            f'/api/imaging-orders/{self.order.pk}/assign-radiologist/',
            {'radiologist': str(other.pk)}, format='json',
        )
        self.assertEqual(opened.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.assigned_radiologist_id, other.pk)

    def test_radiographer_cannot_start_a_second_exam(self):
        from radiology.desk import EXAM_IN_PROCESS

        self.client.force_authenticate(self.tech)
        started = self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.assertEqual(started.status_code, 200)
        patient = Patient.objects.create(first_name='Bola', last_name='Ade', phone_number='08000000223')
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.tech)
        order = ImagingOrder.objects.create(visit=visit, modality=self.order.modality, requested_by=self.tech)
        other = RadiologyExam.objects.create(order=order, status='ASSIGNED')
        blocked = self.client.post(f'/api/radiology-exams/{other.pk}/start/', {}, format='json')
        self.assertEqual(blocked.status_code, 400)
        self.assertIn(EXAM_IN_PROCESS, str(blocked.data))
        other.refresh_from_db()
        self.assertEqual(other.status, 'ASSIGNED')
        screen = Client()
        screen.force_login(self.tech)
        page = screen.get('/radiology/')
        self.assertContains(page, 'data-exam-busy="1"')
        self.assertContains(page, EXAM_IN_PROCESS)
        posted = screen.post('/radiology/', {'action': 'start', 'exam': str(other.pk)}, follow=True)
        self.assertContains(posted, EXAM_IN_PROCESS)

    def test_selecting_a_published_template_loads_into_the_report(self):
        from radiology.models import RadiologyReportTemplate
        from radiology.templates_svc import publish_template

        template = RadiologyReportTemplate.objects.create(
            modality_type='CT', name='Desk Ultrasound', body_part='Abdomen',
            scope='PLATFORM', status='DRAFT', visibility='PUBLIC',
            body_html='<p><strong>EXAM:</strong> desk-template-marker ultrasound</p><p><strong>FINDINGS:</strong> Findings:</p>',
            findings_boilerplate='Findings:',
        )
        publish_template(template, self.rad)
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {'radiologist': str(self.rad.pk)}, format='json')
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/upload-pacs/', {}, format='json')
        screen = Client()
        screen.force_login(self.rad)
        waiting = screen.get('/radiology/')
        self.assertContains(waiting, 'Awaiting acceptance')
        self.assertContains(waiting, 'rad-status-await')
        self.assertContains(waiting, 'btn-rad-accept')
        self.client.force_authenticate(self.rad)
        accepted = self.client.post(f'/api/radiology-exams/{self.exam.pk}/accept/', {}, format='json')
        self.assertEqual(accepted.status_code, 200)
        page = screen.get(f'/radiology/?study={self.order.pk}')
        self.assertContains(page, 'Desk Ultrasound')
        self.assertContains(page, 'desk-template-marker')
        self.assertContains(page, 'id="report-template"')
        self.assertContains(page, 'Ready to report')
        self.assertContains(page, 'rad-status-ready')
        self.assertContains(page, 'btn-rad-open')
        saved = screen.post('/radiology/', {
            'action': 'draft',
            'exam': str(self.exam.pk),
            'template': str(template.pk),
            'findings': template.body_html,
            'indication': 'Pain',
        })
        self.assertEqual(saved.status_code, 302)
        again = screen.get(f'/radiology/?study={self.order.pk}')
        self.assertContains(again, 'desk-template-marker')
        self.assertContains(again, 'Ready to report')
