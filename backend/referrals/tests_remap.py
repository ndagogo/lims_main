"""DEFAULT DOCTOR assignment at registration and referring-doctor remapping."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.utils import timezone

from core.models import AuditEvent, Centre, UserCentreAccess
from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
from patients.models import Patient, PatientDocument, Visit
from referrals.defaults import ensure_default_doctor
from referrals.models import ReferralAttribution, ReferralDoctor, ReferralInstitution
from referrals.remap import REQUEST_FORM_MESSAGE, SAME_DOCTOR_MESSAGE

User = get_user_model()


class ReferringDoctorRemapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='desk1', email='desk1@example.test', password='Strong-test-password-2026!',
        )
        group = Group.objects.get_or_create(name='Front Desk')[0]
        perm = Permission.objects.get(codename='change_referring_doctor')
        group.permissions.add(perm)
        self.user.groups.add(group)
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_edit=True, can_register=True,
        )
        self.default = ensure_default_doctor(self.centre)
        self.doctor = ReferralDoctor.objects.create(
            centre=self.centre, doctor_code='NWS-DR0001', first_name='Ife', last_name='Balogun',
            commission_type='PERCENT', commission_value=Decimal('5'),
        )
        self.clinic = ReferralInstitution.objects.create(doctor=self.doctor, name='UNTH')
        self.other = ReferralDoctor.objects.create(
            centre=self.centre, doctor_code='NWS-DR0002', first_name='MNO', last_name='Ade',
            commission_type='PERCENT', commission_value=Decimal('8'),
        )
        self.other_clinic = ReferralInstitution.objects.create(doctor=self.other, name='Park Lane')
        group_lab = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group_lab)
        self.tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=self.tariff, test=self.test, price=Decimal('1000.00'))
        self.client = Client()
        self.client.force_login(self.user)

    def _attach_request_form(self, visit):
        return PatientDocument.objects.create(
            visit=visit,
            document_type='REGISTRATION',
            file=ContentFile(b'%PDF-1.4\n', name='request-form.pdf'),
            uploaded_by=self.user,
        )

    def test_unknown_referral_assigns_default_doctor(self):
        response = self.client.post('/patients/register/', {
            'action': 'register',
            'centre': str(self.centre.pk),
            'title': 'Mr',
            'first_name': 'Chinedu',
            'last_name': 'Okafor',
            'phone_number': '8011112222',
            'date_of_birth': '1991-01-01',
            'sex': 'Male',
            'referral_source': 'DOCTOR',
            'referring_doctor_unknown': '1',
            'tariff': str(self.tariff.pk),
            'lab_tests': str(self.test.pk),
        })
        self.assertEqual(response.status_code, 302)
        visit = Visit.objects.get(patient__phone_number='8011112222')
        self.assertEqual(visit.referral_source, 'DOCTOR')
        self.assertTrue(visit.referral_attribution.doctor.is_default_placeholder)
        self.assertEqual(visit.referral_attribution.doctor_id, self.default.pk)

    def test_remap_blocked_without_request_form(self):
        patient = Patient.objects.create(first_name='No', last_name='Form', phone_number='8099990000')
        visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.user, referral_source='DOCTOR',
        )
        ReferralAttribution.objects.create(visit=visit, doctor=self.default, recorded_by=self.user)
        blocked = self.client.post('/patients/referrals/remap/', {
            'visit': str(visit.pk),
            'referral_doctor': str(self.doctor.pk),
            'referral_institutions': [str(self.clinic.pk)],
            'referral_note': 'Should fail',
        }, follow=True)
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, REQUEST_FORM_MESSAGE)
        visit.refresh_from_db()
        self.assertEqual(visit.referral_attribution.doctor_id, self.default.pk)

    def test_remap_from_default_updates_attribution_and_audits(self):
        patient = Patient.objects.create(first_name='Ada', last_name='Okeke', phone_number='8022223333')
        visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.user, referral_source='DOCTOR',
        )
        ReferralAttribution.objects.create(visit=visit, doctor=self.default, recorded_by=self.user)
        self._attach_request_form(visit)

        page = self.client.get(f'/patients/referrals/remap/?q={patient.uhid}&patient={patient.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, visit.visit_id)
        self.assertContains(page, 'DEFAULT DOCTOR')

        saved = self.client.post('/patients/referrals/remap/', {
            'visit': str(visit.pk),
            'referral_doctor': str(self.doctor.pk),
            'referral_institutions': [str(self.clinic.pk)],
            'referral_note': 'Doctor details confirmed',
        })
        self.assertEqual(saved.status_code, 302)
        visit.refresh_from_db()
        attribution = visit.referral_attribution
        self.assertEqual(attribution.doctor_id, self.doctor.pk)
        self.assertFalse(attribution.doctor.is_default_placeholder)
        self.assertEqual(list(attribution.institutions.values_list('name', flat=True)), ['UNTH'])
        audit = AuditEvent.objects.get(action='REFERRING_DOCTOR_REMAPPED', object_id=str(visit.pk))
        self.assertTrue(audit.detail['from']['is_default_placeholder'])
        self.assertEqual(audit.detail['to']['doctor_code'], 'NWS-DR0001')
        self.assertEqual(audit.detail['remapped_by'], 'desk1')

    def test_remap_between_real_doctors_is_allowed(self):
        patient = Patient.objects.create(first_name='Mark', last_name='Okoro', phone_number='8033334444')
        visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.user, referral_source='DOCTOR',
        )
        attribution = ReferralAttribution.objects.create(
            visit=visit, doctor=self.doctor, recorded_by=self.user, note='Original mapping',
        )
        attribution.institutions.set([self.clinic])
        self._attach_request_form(visit)

        page = self.client.get(f'/patients/referrals/remap/?q={patient.uhid}&patient={patient.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Ife Balogun')
        self.assertContains(page, SAME_DOCTOR_MESSAGE)

        saved = self.client.post('/patients/referrals/remap/', {
            'visit': str(visit.pk),
            'referral_doctor': str(self.other.pk),
            'referral_institutions': [str(self.other_clinic.pk)],
            'referral_note': 'Corrected referring physician',
        })
        self.assertEqual(saved.status_code, 302)
        attribution.refresh_from_db()
        self.assertEqual(attribution.doctor_id, self.other.pk)
        self.assertEqual(list(attribution.institutions.values_list('name', flat=True)), ['Park Lane'])
        audit = AuditEvent.objects.get(action='REFERRING_DOCTOR_REMAPPED', object_id=str(visit.pk))
        self.assertEqual(audit.detail['from']['doctor_code'], 'NWS-DR0001')
        self.assertEqual(audit.detail['to']['doctor_code'], 'NWS-DR0002')
        self.assertEqual(audit.detail['from']['institutions'], ['UNTH'])
        self.assertEqual(audit.detail['to']['institutions'], ['Park Lane'])
        self.assertEqual(audit.detail['to']['note'], 'Corrected referring physician')

    def test_cannot_remap_to_same_doctor(self):
        patient = Patient.objects.create(first_name='Ngozi', last_name='Same', phone_number='8055556666')
        visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.user, referral_source='DOCTOR',
        )
        attribution = ReferralAttribution.objects.create(
            visit=visit, doctor=self.doctor, recorded_by=self.user, note='Already mapped',
        )
        attribution.institutions.set([self.clinic])
        self._attach_request_form(visit)

        blocked = self.client.post('/patients/referrals/remap/', {
            'visit': str(visit.pk),
            'referral_doctor': str(self.doctor.pk),
            'referral_institutions': [str(self.clinic.pk)],
            'referral_note': 'Trying same doctor',
        }, follow=True)
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, SAME_DOCTOR_MESSAGE)
        attribution.refresh_from_db()
        self.assertEqual(attribution.doctor_id, self.doctor.pk)
        self.assertFalse(
            AuditEvent.objects.filter(action='REFERRING_DOCTOR_REMAPPED', object_id=str(visit.pk)).exists()
        )

    def test_cannot_remap_without_permission(self):
        outsider = User.objects.create_user(
            username='lab1', email='lab1@example.test', password='Strong-test-password-2026!',
        )
        outsider.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        UserCentreAccess.objects.create(user=outsider, centre=self.centre, can_view=True, can_edit=True)
        blocked = Client()
        blocked.force_login(outsider)
        response = blocked.get('/patients/referrals/remap/')
        self.assertEqual(response.status_code, 302)

    def test_remap_page_shows_history_search_and_date_filters(self):
        patient = Patient.objects.create(first_name='History', last_name='Case', phone_number='8044445555')
        visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.user, referral_source='DOCTOR',
        )
        ReferralAttribution.objects.create(visit=visit, doctor=self.default, recorded_by=self.user)
        self._attach_request_form(visit)
        self.client.post('/patients/referrals/remap/', {
            'visit': str(visit.pk),
            'referral_doctor': str(self.doctor.pk),
            'referral_institutions': [str(self.clinic.pk)],
            'referral_note': 'History note alpha',
        })
        page = self.client.get('/patients/referrals/remap/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Remap history')
        self.assertContains(page, 'Search history')
        self.assertContains(page, 'From date')
        self.assertContains(page, patient.uhid)
        self.assertContains(page, 'Ife Balogun')
        filtered = self.client.get('/patients/referrals/remap/', {'hq': 'History note alpha'})
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, patient.uhid)
        today = timezone.localdate().isoformat()
        by_date = self.client.get('/patients/referrals/remap/', {'hfrom': today, 'hto': today})
        self.assertEqual(by_date.status_code, 200)
        self.assertContains(by_date, patient.uhid)
        past = (timezone.localdate() - timedelta(days=30)).isoformat()
        older = self.client.get('/patients/referrals/remap/', {'hfrom': past, 'hto': past})
        self.assertEqual(older.status_code, 200)
        self.assertContains(older, 'No remap history matched')
        miss = self.client.get('/patients/referrals/remap/', {'hq': 'zzzz-no-match'})
        self.assertEqual(miss.status_code, 200)
        self.assertContains(miss, 'No remap history matched')
