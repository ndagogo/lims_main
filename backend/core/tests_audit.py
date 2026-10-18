from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from core.audit import filter_audit_events, record_audit
from core.models import AuditEvent, Centre, UserCentreAccess

User = get_user_model()


class AuditTrailDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='audit.admin', email='audit.admin@example.test',
            password='A-long-unique-password-2026!',
        )
        self.admin.department = 'Quality'
        self.admin.save(update_fields=['department'])
        Group.objects.get_or_create(name='Platform Administrator')
        self.admin.groups.add(Group.objects.get(name='Platform Administrator'))
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(
            user=self.admin, centre=self.centre, can_view=True, can_edit=True, can_approve=True, is_default=True,
        )
        record_audit(
            actor=self.admin, action='RESULT_RELEASED', object_type='LabResult', object_id='r1',
            centre=self.centre, department='Laboratory', patient_ref='UHID001', visit_ref='NWS0000099',
            previous_values={'status': 'AUTHENTICATED'}, new_values={'status': 'RELEASED'},
            reason='Clinical release after dual control', approval_by=self.admin,
            approval_note='Second checker', evidence_ref='DOC-44',
        )
        # Simulate IP/browser via direct create for filter coverage
        AuditEvent.objects.filter(action='RESULT_RELEASED').update(ip_address='10.0.0.8', browser='PytestBrowser/1.0')
        AuditEvent.objects.create(
            actor=self.admin, action='HTTP_POST', category='SYSTEM', object_type='HTTP_REQUEST',
            path='/accounts/', method='POST', ip_address='10.0.0.8',
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_dashboard_filters_and_detail(self):
        page = self.client.get('/platform/audit/', {
            'username': 'audit.admin', 'action': 'RESULT_RELEASED', 'patient': 'UHID001', 'exclude_http': '1',
        })
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'RESULT_RELEASED')
        self.assertContains(page, 'UHID001')
        self.assertNotContains(page, 'HTTP_POST')
        event = AuditEvent.objects.get(action='RESULT_RELEASED')
        detail = self.client.get('/platform/audit/', {'event': str(event.pk), 'exclude_http': '1'})
        self.assertContains(detail, 'Clinical release after dual control')
        self.assertContains(detail, 'DOC-44')

    def test_exports_csv_excel_pdf(self):
        base = {'username': 'audit.admin', 'exclude_http': '1', 'export': '1'}
        csv_resp = self.client.get('/platform/audit/', {**base, 'format': 'csv'})
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn('text/csv', csv_resp['Content-Type'])
        self.assertIn(b'RESULT_RELEASED', csv_resp.content)
        xlsx = self.client.get('/platform/audit/', {**base, 'format': 'xlsx'})
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn('spreadsheetml', xlsx['Content-Type'])
        pdf = self.client.get('/platform/audit/', {**base, 'format': 'pdf'})
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_filter_helper_significant(self):
        qs = filter_audit_events({'significant': '1'})
        self.assertTrue(qs.filter(action='RESULT_RELEASED').exists())
        self.assertFalse(qs.filter(object_type='HTTP_REQUEST').exists())
