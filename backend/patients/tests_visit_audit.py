from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, RequestFactory, TestCase

from core.models import AuditEvent, Centre, UserCentreAccess
from patients.models import Patient, Visit
from patients.visit_audit import build_visit_audit_trail, record_visit_audit

User = get_user_model()


class VisitAuditTrailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='audit.desk', email='audit.desk@example.test',
            password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Front Desk')[0])
        self.user.department = 'Front Desk'
        self.user.save(update_fields=['department'])
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True,
        )
        self.patient = Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08012345678')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.user)
        self.client = Client()
        self.factory = RequestFactory()

    def test_record_visit_audit_stores_ip_browser_and_location(self):
        request = self.factory.post('/patients/register/')
        request.user = self.user
        request.audit_ip = '203.0.113.40'
        request.audit_browser = 'Mozilla/5.0 HFDNTest'
        request.audit_location = 'NG'
        record_visit_audit(
            request, self.visit, action='VISIT_OPENED',
            detail={'visit_id': self.visit.visit_id, 'invoice': 'INV-1'},
            new_values={'status': 'PROCESSED'},
        )
        row = AuditEvent.objects.get(action='VISIT_OPENED', visit_ref=self.visit.visit_id)
        self.assertEqual(row.ip_address, '203.0.113.40')
        self.assertEqual(row.browser, 'Mozilla/5.0 HFDNTest')
        self.assertEqual(row.location_hint, 'NG')
        self.assertEqual(row.patient_ref, self.patient.uhid)
        self.assertEqual(row.new_values.get('status'), 'PROCESSED')

    def test_visit_audit_page_shows_trace_fields(self):
        AuditEvent.objects.create(
            actor=self.user, action='PAYMENT_RECORDED', object_type='Payment', object_id='1',
            centre=self.centre, visit_ref=self.visit.visit_id, patient_ref=self.patient.uhid,
            ip_address='198.51.100.9', browser='AccountsChrome/120', location_hint='NG',
            previous_values={}, new_values={'amount': '5000.00'},
            detail={'invoice': 'INV-9', 'visit_id': self.visit.visit_id},
            reason='',
        )
        AuditEvent.objects.create(
            actor=self.user, action='DISCOUNT_REJECTED', object_type='DiscountRequest', object_id='2',
            centre=self.centre, visit_ref=self.visit.visit_id, patient_ref=self.patient.uhid,
            ip_address='198.51.100.10', browser='AccountsChrome/120', location_hint='NG',
            reason='Outside policy',
            detail={'invoice': 'INV-9', 'visit_id': self.visit.visit_id},
        )
        trail = build_visit_audit_trail(self.user, self.visit)
        actions = {row['action'] for row in trail}
        self.assertIn('PAYMENT_RECORDED', actions)
        self.assertIn('DISCOUNT_REJECTED', actions)
        payment = next(row for row in trail if row['action'] == 'PAYMENT_RECORDED')
        self.assertEqual(payment['ip_address'], '198.51.100.9')
        self.assertEqual(payment['browser'], 'AccountsChrome/120')
        self.assertEqual(payment['location_hint'], 'NG')

        self.client.force_login(self.user)
        page = self.client.get(f'/visits/{self.visit.visit_id}/audit/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'REGISTRATION AUDIT TRAIL')
        self.assertContains(page, '198.51.100.9')
        self.assertContains(page, 'AccountsChrome/120')
        self.assertContains(page, 'Outside policy')
        self.assertContains(page, 'Payment recorded')

    def test_registration_list_links_to_visit_audit(self):
        self.client.force_login(self.user)
        page = self.client.get('/patients/register/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'/visits/{self.visit.visit_id}/audit/')
        self.assertContains(page, 'Audit trail')
