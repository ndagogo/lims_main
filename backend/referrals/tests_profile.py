"""Referral doctor centres, bank details, introducing staff, and report-name opt-out."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from core.models import Centre, UserCentreAccess
from patients.models import Patient, Visit
from referrals.models import ReferralAttribution, ReferralDoctor
from referrals.services import doctors_for_centre, report_referring_name
from reporting.dashboard import build_management_dashboard

User = get_user_model()


class ReferralDoctorProfileTests(TestCase):
    def setUp(self):
        self.home = Centre.objects.create(code='NWS', name='NOWAS')
        self.other = Centre.objects.create(code='IND', name='Independence')
        self.marketer = User.objects.create_user(
            username='marketer1', first_name='Ada', last_name='Marketing',
            email='marketer1@example.test', password='A-long-unique-password-2026!',
        )
        self.admin = User.objects.create_user(
            username='catalog.docs', email='catalog.docs@example.test', password='A-long-unique-password-2026!',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        UserCentreAccess.objects.create(user=self.admin, centre=self.home, can_view=True, can_edit=True, is_default=True)
        self.patient = Patient.objects.create(first_name='Zilly', last_name='Ufuman', phone_number='8030001111')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.home, registered_by=self.admin)

    def test_catalog_saves_bank_centres_marketer_and_report_opt_out(self):
        screen = Client()
        screen.force_login(self.admin)
        created = screen.post('/catalog/doctors/', {
            'action': 'create',
            'centre': str(self.home.pk),
            'centres': [str(self.home.pk), str(self.other.pk)],
            'doctor_code': 'NWS-DR77',
            'title': 'Dr',
            'first_name': 'Ngozi',
            'last_name': 'Eze',
            'commission_type': 'PERCENT',
            'commission_value': '5',
            'is_active': 'on',
            'is_active_submitted': '1',
            'bank_name': 'Access Bank',
            'account_name': 'Ngozi Eze',
            'account_number': '0123456789',
            'onboarded_by': str(self.marketer.pk),
            'appear_on_reports_submitted': '1',
        })
        self.assertEqual(created.status_code, 302)
        doctor = ReferralDoctor.objects.get(doctor_code='NWS-DR77')
        self.assertEqual(doctor.bank_name, 'Access Bank')
        self.assertEqual(doctor.account_number, '0123456789')
        self.assertEqual(doctor.onboarded_by_id, self.marketer.pk)
        self.assertFalse(doctor.appear_on_reports)
        self.assertTrue(doctor.serves_centre(self.other))
        self.assertIn(self.other.pk, set(doctor.centres.values_list('pk', flat=True)))
        self.assertTrue(doctors_for_centre(self.other).filter(pk=doctor.pk).exists())

        ReferralAttribution.objects.create(visit=self.visit, doctor=doctor, recorded_by=self.admin)
        self.assertEqual(report_referring_name(self.visit), '')
        doctor.appear_on_reports = True
        doctor.save(update_fields=['appear_on_reports'])
        self.visit.refresh_from_db()
        self.assertIn('Ngozi Eze', report_referring_name(self.visit))

        page = screen.get(f'/catalog/doctors/?doctor={doctor.pk}')
        self.assertContains(page, 'Assigned centres')
        self.assertContains(page, 'Search staff by name, username, or staff ID')
        self.assertContains(page, 'Access Bank')
        self.assertContains(page, 'Ada Marketing')

        dashboard = build_management_dashboard(self.admin, period='annual')
        marketers = dashboard['referral']['marketers']
        self.assertEqual(marketers[0]['username'], 'marketer1')
        self.assertEqual(marketers[0]['doctors'], 1)
        self.assertEqual(marketers[0]['doctor_names'], ['Dr Ngozi Eze'])
        self.assertEqual(marketers[0]['visits'], 1)
