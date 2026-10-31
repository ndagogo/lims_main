from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, RequestFactory
from rest_framework.test import APIClient
from .models import Centre, UserCentreAccess, AuditEvent
User=get_user_model()
class PhaseOneSecurityTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.user=User.objects.create_user(username='staff1',email='staff1@example.test',password='A-long-unique-password-2026!')
        self.centre=Centre.objects.create(code='NWS',name='NOWAS')
        UserCentreAccess.objects.create(user=self.user,centre=self.centre,can_view=True,can_register=True,is_default=True)
    def test_login_returns_jwt_and_centre_access(self):
        response=self.client.post('/api/auth/login/',{'username':'staff1','password':'A-long-unique-password-2026!'},format='json')
        self.assertEqual(response.status_code,200)
        self.assertIn('access',response.data); self.assertEqual(response.data['user']['centres'][0]['centre__code'],'NWS')
    def test_invalid_login_rejected(self):
        response=self.client.post('/api/auth/login/',{'username':'staff1','password':'wrong'},format='json')
        self.assertEqual(response.status_code,401)

    def test_three_wrong_passwords_lock_the_account(self):
        from core.auth_services import LOCKED
        for _ in range(2):
            response = self.client.post('/api/auth/login/', {'username': 'staff1', 'password': 'wrong'}, format='json')
            self.assertEqual(response.status_code, 401)
            self.assertNotEqual(response.data['detail'], LOCKED)
        locked = self.client.post('/api/auth/login/', {'username': 'staff1', 'password': 'wrong'}, format='json')
        self.assertEqual(locked.status_code, 401)
        self.assertEqual(locked.data['detail'], LOCKED)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.locked_until)
        still = self.client.post('/api/auth/login/', {'username': 'staff1', 'password': 'A-long-unique-password-2026!'}, format='json')
        self.assertEqual(still.status_code, 401)
        self.assertEqual(still.data['detail'], LOCKED)
        page = self.client.post('/login/', {'username': 'staff1', 'password': 'wrong'})
        self.assertContains(page, LOCKED)
    def test_profile_requires_authentication(self):
        self.assertEqual(self.client.get('/api/auth/me/').status_code,401)
    def test_profile_contains_assigned_centres(self):
        self.client.force_authenticate(self.user)
        response=self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code,200); self.assertEqual(len(response.data['centres']),1)

    def test_account_profile_page_updates_credentials(self):
        from django.test import Client
        web = Client()
        web.force_login(self.user)
        page = web.get('/account/profile/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Professional credentials')
        saved = web.post('/account/profile/', {
            'action': 'save',
            'first_name': 'Ada',
            'last_name': 'Okeke',
            'display_name': 'Dr Ada Okeke',
            'email': 'staff1@example.test',
            'job_title': 'Consultant Radiologist',
            'staff_id': 'NWS-RAD-01',
            'department': 'Radiology',
            'specialty': 'CT / MRI',
            'qualifications': 'MBBS, FWACS',
            'license_number': 'MDCN-12345',
            'license_authority': 'Medical and Dental Council of Nigeria',
            'phone_work': '08030001111',
            'phone_mobile': '08030002222',
            'office_extension': '214',
            'bio': 'Cross-sectional imaging.',
        })
        self.assertEqual(saved.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.job_title, 'Consultant Radiologist')
        self.assertEqual(self.user.signature_title, 'Consultant Radiologist')
        self.assertEqual(self.user.staff_id, 'NWS-RAD-01')
        self.assertEqual(self.user.license_number, 'MDCN-12345')
        # Old signature URL redirects into the profile page.
        self.assertEqual(web.get('/account/signature/').status_code, 302)

    def test_account_profile_page_updates_credentials(self):
        from django.test import Client
        web = Client()
        web.force_login(self.user)
        page = web.get('/account/profile/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Professional credentials')
        saved = web.post('/account/profile/', {
            'action': 'save',
            'first_name': 'Ada',
            'last_name': 'Okeke',
            'display_name': 'Dr Ada Okeke',
            'email': 'staff1@example.test',
            'job_title': 'Consultant Radiologist',
            'staff_id': 'NWS-RAD-01',
            'department': 'Radiology',
            'specialty': 'CT / MRI',
            'qualifications': 'MBBS, FWACS',
            'license_number': 'MDCN-12345',
            'license_authority': 'Medical and Dental Council of Nigeria',
            'phone_work': '08030001111',
            'phone_mobile': '08030002222',
            'office_extension': '214',
            'bio': 'Cross-sectional imaging.',
        })
        self.assertEqual(saved.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.job_title, 'Consultant Radiologist')
        self.assertEqual(self.user.signature_title, 'Consultant Radiologist')
        self.assertEqual(self.user.staff_id, 'NWS-RAD-01')
        self.assertEqual(self.user.license_number, 'MDCN-12345')
        # Old signature URL redirects into the profile page.
        self.assertEqual(web.get('/account/signature/').status_code, 302)
    def test_user_cannot_list_unassigned_centre(self):
        other=Centre.objects.create(code='IND',name='Independence')
        self.client.force_authenticate(self.user)
        response=self.client.get('/api/centres/')
        self.assertEqual(response.status_code,200)
        self.assertEqual([row['code'] for row in response.data['results']],['NWS'])
    def test_default_centre_is_unique_per_user(self):
        other=Centre.objects.create(code='IND',name='Independence')
        UserCentreAccess.objects.create(user=self.user,centre=other,is_default=True)
        self.assertEqual(UserCentreAccess.objects.filter(user=self.user,is_default=True).count(),1)
    def test_authenticated_write_request_is_audited(self):
        self.client.force_authenticate(self.user)
        self.client.post('/api/auth/token/verify/',{'token':'not-a-token'},format='json')
        self.assertTrue(AuditEvent.objects.filter(actor=self.user,method='POST').exists())

class StaffWorkspaceTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='desk1',email='desk1@example.test',password='A-long-unique-password-2026!')
        self.user.groups.add(Group.objects.get_or_create(name='Front Desk')[0])
        self.centre=Centre.objects.create(code='NWS',name='NOWAS')
        UserCentreAccess.objects.create(user=self.user,centre=self.centre,can_view=True,can_register=True,can_edit=True,is_default=True)

    def test_forwarded_ip_is_ignored_unless_the_proxy_flag_is_on(self):
        from django.test import RequestFactory, override_settings
        from core.access import client_ip
        from laboratory.api import request_meta
        request = RequestFactory().get('/')
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.8'
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        with override_settings(AUDIT_TRUST_X_FORWARDED_FOR=False):
            self.assertEqual(client_ip(request), '127.0.0.1')
            self.assertEqual(request_meta(request)[0], '127.0.0.1')
        with override_settings(AUDIT_TRUST_X_FORWARDED_FOR=True):
            self.assertEqual(client_ip(request), '203.0.113.8')

    def test_registration_search_hides_another_centres_patient(self):
        from django.core.files.base import ContentFile
        from django.test import Client
        from patients.models import Patient, PatientDocument, Visit
        other = Centre.objects.create(code='IND', name='Independence')
        hidden = Patient.objects.create(first_name='Hidden', last_name='Patient', phone_number='08099998888')
        Visit.objects.create(patient=hidden, centre=other, registered_by=self.user)
        visible = Patient.objects.create(first_name='Visible', last_name='Patient', phone_number='08011110000')
        visit = Visit.objects.create(patient=visible, centre=self.centre, registered_by=self.user)
        document = PatientDocument.objects.create(visit=visit, document_type='REGISTRATION', file=ContentFile(b'%PDF-1.4\n', name='form.pdf'), uploaded_by=self.user)
        screen = Client()
        screen.force_login(self.user)
        hidden_search = screen.get('/patients/register/', {'q': '08099998888'})
        self.assertNotContains(hidden_search, hidden.uhid)
        visible_search = screen.get('/patients/register/', {'q': visible.uhid})
        self.assertContains(visible_search, visible.uhid)
        from decimal import Decimal
        from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        priced = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=priced, price=Decimal('1000.00'))
        opened = screen.post('/patients/register/', {
            'action': 'new_visit', 'patient': str(visible.pk), 'centre': str(self.centre.pk), 'referral_source': 'SELF',
            'tariff': str(tariff.pk), 'lab_tests': str(priced.pk),
        })
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(visible.visits.count(), 2)
        blocked = screen.post('/patients/register/', {'action': 'new_visit', 'patient': str(hidden.pk), 'centre': str(other.pk), 'referral_source': 'SELF'})
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(hidden.visits.count(), 1)
        download = screen.get(f'/patients/documents/{document.pk}/download/')
        self.assertEqual(download.status_code, 200)
        outsider = User.objects.create_user(username='outsider', email='outsider@example.test', password='A-long-unique-password-2026!')
        UserCentreAccess.objects.create(user=outsider, centre=other, can_view=True, can_register=True)
        other_screen = Client()
        other_screen.force_login(outsider)
        self.assertEqual(other_screen.get(f'/patients/documents/{document.pk}/download/').status_code, 404)
        overview = screen.get('/')
        self.assertContains(overview, 'VISITS')
        self.assertEqual(screen.get('/users/').status_code, 403)
        self.assertEqual(screen.get('/roles/').status_code, 403)

    def test_superuser_can_suspend_a_user_and_assign_a_role(self):
        from django.contrib.auth.models import Group
        from django.test import Client
        admin = User.objects.create_superuser(username='platform', email='platform@example.test', password='A-long-unique-password-2026!')
        staff = User.objects.create_user(username='bench', email='bench@example.test', password='A-long-unique-password-2026!')
        role, _created = Group.objects.get_or_create(name='Front Desk')
        screen = Client()
        screen.force_login(admin)
        self.assertContains(screen.get('/'), 'Laboratory sample pipeline')
        suspended = screen.post('/users/', {'action': 'suspend', 'user': str(staff.pk)})
        self.assertEqual(suspended.status_code, 302)
        staff.refresh_from_db()
        self.assertFalse(staff.is_active)
        self.assertFalse(Client().login(username='bench', password='A-long-unique-password-2026!'))
        blocked = screen.post('/users/', {'action': 'suspend', 'user': str(admin.pk)})
        self.assertEqual(blocked.status_code, 302)
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)
        assigned = screen.post('/roles/', {'action': 'add_member', 'role': str(role.pk), 'user': str(staff.pk)})
        self.assertEqual(assigned.status_code, 302)
        self.assertTrue(staff.groups.filter(name='Front Desk').exists())
        created = screen.post('/users/', {
            'action': 'create',
            'username': 'new.staff',
            'email': 'new.staff@example.test',
            'password': 'A-long-unique-password-2026!',
            'title': 'Mrs',
            'first_name': 'Ngozi',
            'other_names': 'Ada',
            'last_name': 'Eze',
            'phone_mobile': '08031112222',
            'date_of_birth': '1992-03-14',
            'sex': 'Female',
            'marital_status': 'MARRIED',
            'department': 'Haematology',
            'groups': [str(role.pk)],
        })
        self.assertEqual(created.status_code, 302)
        person = User.objects.get(username='new.staff')
        self.assertEqual(person.first_name, 'Ngozi')
        self.assertEqual(person.phone_mobile, '08031112222')
        self.assertEqual(person.sex, 'Female')
        self.assertEqual(person.marital_status, 'MARRIED')
        self.assertEqual(person.date_of_birth.isoformat(), '1992-03-14')
        self.assertIn('Ngozi', person.display_name)
        missing = screen.post('/users/', {
            'action': 'create',
            'username': 'incomplete',
            'email': 'incomplete@example.test',
            'password': 'A-long-unique-password-2026!',
        }, follow=True)
        self.assertContains(missing, 'First name and last name are required')
        roles_page = screen.get('/roles/')
        self.assertContains(roles_page, 'Create a role')
        created_role = screen.post('/roles/', {'action': 'create_role', 'name': 'Night Shift Supervisor'})
        self.assertEqual(created_role.status_code, 302)
        custom = Group.objects.get(name='Night Shift Supervisor')
        self.assertContains(screen.get('/users/'), 'Night Shift Supervisor')
        with_custom = screen.post('/users/', {
            'action': 'create',
            'username': 'night.lead',
            'email': 'night.lead@example.test',
            'password': 'A-long-unique-password-2026!',
            'first_name': 'Chidi',
            'last_name': 'Okeke',
            'phone_mobile': '08039998877',
            'groups': [str(custom.pk)],
        })
        self.assertEqual(with_custom.status_code, 302)
        night = User.objects.get(username='night.lead')
        self.assertTrue(night.groups.filter(name='Night Shift Supervisor').exists())
        blocked_delete = screen.post('/roles/', {
            'action': 'delete_role',
            'role': str(Group.objects.get_or_create(name='Platform Administrator')[0].pk),
        }, follow=True)
        self.assertContains(blocked_delete, 'protected system role')

    def test_each_role_opens_only_its_own_workspace(self):
        from django.test import Client
        def person(username, role):
            user = User.objects.create_user(username=username, email=f'{username}@example.test', password='A-long-unique-password-2026!')
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            UserCentreAccess.objects.create(user=user, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True, is_default=True)
            screen = Client()
            screen.force_login(user)
            return screen
        radio = person('radio.only', 'Radiographer')
        self.assertRedirects(radio.get('/'), '/radiology/')
        worklist = radio.get('/radiology/')
        self.assertEqual(worklist.status_code, 200)
        self.assertContains(worklist, 'Radiology worklist')
        self.assertNotContains(worklist, 'Laboratory sample pipeline')
        self.assertNotContains(worklist, 'Accounts & receipts')
        self.assertEqual(radio.get('/accounts/').status_code, 403)
        self.assertEqual(radio.get('/laboratory/').status_code, 403)
        self.assertEqual(radio.get('/patients/register/').status_code, 403)
        self.assertEqual(radio.get('/phlebotomy/').status_code, 403)
        accounts = person('accounts.only', 'Accounts')
        self.assertEqual(accounts.get('/accounts/').status_code, 200)
        self.assertContains(accounts.get('/'), 'INVOICES')
        self.assertNotContains(accounts.get('/'), 'Laboratory sample pipeline')
        self.assertEqual(accounts.get('/radiology/').status_code, 403)
        self.assertEqual(accounts.get('/laboratory/').status_code, 403)
        lab = person('lab.only', 'Laboratory Scientist')
        self.assertEqual(lab.get('/laboratory/').status_code, 200)
        self.assertContains(lab.get('/'), 'Laboratory sample pipeline')
        self.assertNotContains(lab.get('/'), 'Imaging worklist')
        self.assertEqual(lab.get('/radiology/').status_code, 403)
        self.assertEqual(lab.get('/accounts/').status_code, 403)
        self.assertEqual(lab.get('/phlebotomy/').status_code, 403)
        phleb = person('phleb.only', 'Phlebotomist')
        self.assertEqual(phleb.get('/phlebotomy/').status_code, 200)
        self.assertContains(phleb.get('/phlebotomy/'), 'barcode generation')
        self.assertNotContains(phleb.get('/'), 'Laboratory sample pipeline')
        self.assertEqual(phleb.get('/laboratory/').status_code, 403)
        self.assertEqual(phleb.get('/accounts/').status_code, 403)
        desk = person('desk.only', 'Front Desk')
        self.assertEqual(desk.get('/patients/register/').status_code, 200)
        self.assertContains(desk.get('/'), 'VISITS')
        self.assertEqual(desk.get('/accounts/').status_code, 403)
        self.assertEqual(desk.get('/laboratory/').status_code, 403)

    def test_referring_doctor_maps_only_their_own_institutions(self):
        from decimal import Decimal
        from django.test import Client
        from patients.models import Patient, Visit
        from referrals.models import ReferralDoctor, ReferralInstitution
        doctor = ReferralDoctor.objects.create(centre=self.centre, doctor_code='NWS-DR9', title='Dr', first_name='Chioma', last_name='Adeyemi', commission_type='PERCENT', commission_value=Decimal('0'))
        unth = ReferralInstitution.objects.create(doctor=doctor, name='UNTH')
        park = ReferralInstitution.objects.create(doctor=doctor, name='Park Lane')
        other_centre = Centre.objects.create(code='IND', name='Independence')
        other_doctor = ReferralDoctor.objects.create(centre=other_centre, doctor_code='IND-DR9', title='Dr', first_name='Tunde', last_name='Bello', commission_type='PERCENT', commission_value=Decimal('0'))
        foreign = ReferralInstitution.objects.create(doctor=other_doctor, name='Other Clinic')
        from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        priced = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=priced, price=Decimal('1000.00'))
        screen = Client()
        screen.force_login(self.user)
        page = screen.get('/patients/register/')
        self.assertContains(page, 'Request type')
        self.assertContains(page, 'Search tests')
        self.assertNotContains(page, 'Tick every laboratory')
        self.assertContains(page, 'Referral institutions')
        self.assertContains(page, 'Search doctor by name')
        self.assertContains(page, 'UNTH')
        self.assertNotContains(page, 'Other Clinic')
        self.assertNotContains(page, 'Referral doctors and institutions')
        self.assertNotContains(page, 'Add doctor')
        registered = screen.post('/patients/register/', {
            'action': 'register', 'centre': str(self.centre.pk), 'title': 'Mr', 'first_name': 'Mark', 'last_name': 'Okeke',
            'phone_number': '8022223333', 'date_of_birth': '1990-04-12', 'sex': 'Male', 'city': 'Enugu', 'state': 'Enugu',
            'referral_source': 'DOCTOR', 'referral_doctor': str(doctor.pk), 'referral_institutions': [str(unth.pk), str(park.pk)],
            'tariff': str(tariff.pk), 'lab_tests': str(priced.pk),
        })
        self.assertEqual(registered.status_code, 302)
        visit = Visit.objects.get(patient__phone_number='8022223333')
        self.assertEqual(visit.referral_attribution.doctor_id, doctor.id)
        self.assertEqual(set(visit.referral_attribution.institutions.values_list('name', flat=True)), {'UNTH', 'Park Lane'})
        rejected = screen.post('/patients/register/', {
            'action': 'new_visit', 'patient': str(visit.patient_id), 'centre': str(self.centre.pk),
            'referral_source': 'DOCTOR', 'referral_doctor': str(doctor.pk), 'referral_institutions': [str(foreign.pk)],
        })
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(Patient.objects.get(pk=visit.patient_id).visits.count(), 1)

    def test_hmo_and_corporate_clients_come_from_the_centre_list(self):
        from decimal import Decimal
        from django.test import Client
        from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
        from patients.models import OrganisationClient, Visit
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        priced = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=priced, price=Decimal('1000.00'))
        corporate = OrganisationClient.objects.create(centre=self.centre, kind='CORPORATE', name='MTN Nigeria')
        hmo = OrganisationClient.objects.create(centre=self.centre, kind='HMO', name='Hygeia HMO')
        other = Centre.objects.create(code='IND', name='Independence')
        OrganisationClient.objects.create(centre=other, kind='CORPORATE', name='Secret Corp')
        screen = Client()
        screen.force_login(self.user)
        page = screen.get('/patients/register/')
        self.assertContains(page, 'MTN Nigeria')
        self.assertContains(page, 'Hygeia HMO')
        self.assertNotContains(page, 'Secret Corp')
        registered = screen.post('/patients/register/', {
            'action': 'register', 'centre': str(self.centre.pk), 'first_name': 'Ada', 'last_name': 'Corporate',
            'phone_number': '8044445555', 'referral_source': 'CORPORATE', 'client': str(corporate.pk),
            'client_employee_number': 'EMP-19', 'tariff': str(tariff.pk), 'lab_tests': str(priced.pk),
        })
        self.assertEqual(registered.status_code, 302)
        visit = Visit.objects.get(patient__phone_number='8044445555')
        self.assertEqual(visit.client_id, corporate.id)
        self.assertEqual(visit.client_name, 'MTN Nigeria')
        self.assertEqual(visit.client_employee_number, 'EMP-19')
        rejected = screen.post('/patients/register/', {
            'action': 'register', 'centre': str(self.centre.pk), 'first_name': 'Bola', 'last_name': 'Mismatch',
            'phone_number': '8044446666', 'referral_source': 'HMO', 'client': str(corporate.pk),
            'tariff': str(tariff.pk), 'lab_tests': str(priced.pk),
        })
        self.assertEqual(rejected.status_code, 302)
        self.assertFalse(Visit.objects.filter(patient__phone_number='8044446666').exists())
        hmo_visit = screen.post('/patients/register/', {
            'action': 'register', 'centre': str(self.centre.pk), 'first_name': 'Bola', 'last_name': 'Covered',
            'phone_number': '8044447777', 'referral_source': 'HMO', 'client': str(hmo.pk),
            'client_employee_number': 'HMO-7', 'tariff': str(tariff.pk), 'lab_tests': str(priced.pk),
        })
        self.assertEqual(hmo_visit.status_code, 302)
        covered = Visit.objects.get(patient__phone_number='8044447777')
        self.assertEqual(covered.client_id, hmo.id)
        self.assertEqual(covered.client_name, 'Hygeia HMO')

    def test_processed_registration_waits_on_outstanding_dues(self):
        from decimal import Decimal
        from django.test import Client
        from billing.models import Invoice
        from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
        from patients.models import Visit
        from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        priced = Test.objects.create(code='CBC', name='Complete Blood Count', service_group=group)
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=priced, price=Decimal('5500.00'))
        procedure = ImagingProcedure.objects.create(code='XR-CHEST', name='Chest X-ray', modality_type='XRAY')
        ImagingPrice.objects.create(tariff=tariff, procedure=procedure, price=Decimal('15000.00'))
        ImagingModality.objects.create(centre=self.centre, code='XR1', name='X-ray Room 1', modality_type='XRAY')

        def person(username, role):
            user = User.objects.create_user(username=username, email=f'{username}@example.test', password='A-long-unique-password-2026!')
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            UserCentreAccess.objects.create(user=user, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True, is_default=True)
            screen = Client()
            screen.force_login(user)
            return screen

        desk = Client()
        desk.force_login(self.user)
        registered = desk.post('/patients/register/', {
            'action': 'register', 'centre': str(self.centre.pk), 'first_name': 'Ifeoma', 'last_name': 'Dues',
            'phone_number': '8077776666', 'referral_source': 'SELF', 'tariff': str(tariff.pk),
            'lab_tests': str(priced.pk), 'imaging_tests': str(procedure.pk),
        })
        self.assertEqual(registered.status_code, 302)
        visit = Visit.objects.get(patient__last_name='Dues')
        self.assertEqual(visit.status, 'PROCESSED')
        invoice = Invoice.objects.get(visit=visit)
        self.assertEqual(invoice.status, 'UNPAID')
        self.assertEqual(invoice.total, Decimal('20500.00'))
        cashier = person('cashier.dues', 'Cashier')
        lookup = cashier.get('/accounts/', {'q': 'Ifeoma'})
        self.assertContains(lookup, 'Outstanding dues')
        self.assertContains(lookup, 'Ifeoma')
        self.assertContains(lookup, 'Complete Blood Count')
        self.assertContains(lookup, 'Chest X-ray')
        self.assertNotContains(lookup, 'Pending discount approvals')
        self.assertNotContains(lookup, 'Accountant reports')
        selected = cashier.get('/accounts/', {'q': 'Ifeoma', 'due': str(invoice.pk)})
        self.assertContains(selected, 'Record payment')
        self.assertContains(selected, 'Discount request')
        self.assertContains(selected, '20500.00')
        self.assertNotContains(selected, 'Pending discount approvals')
        self.assertEqual(cashier.get('/accounts/reports/').status_code, 302)
        phleb = person('phleb.dues', 'Phlebotomist')
        radio = person('radio.dues', 'Radiographer')
        lab = person('lab.dues', 'Laboratory Scientist')
        self.assertNotContains(phleb.get('/phlebotomy/'), 'Ifeoma')
        self.assertNotContains(radio.get('/radiology/'), 'Ifeoma')
        self.assertNotContains(lab.get('/laboratory/'), 'Ifeoma')
        partial = cashier.post('/accounts/', {'action': 'pay', 'invoice': str(invoice.pk), 'amount': '5000.00', 'method': 'POS', 'reference': 'POS-4412'}, follow=True)
        self.assertEqual(partial.status_code, 200)
        self.assertContains(partial, 'Payment receipt')
        self.assertContains(partial, 'HFDN-RCP-')
        self.assertContains(partial, 'Ifeoma Dues')
        self.assertContains(partial, 'Complete Blood Count')
        self.assertContains(partial, 'Chest X-ray')
        self.assertContains(partial, '5000.00')
        self.assertContains(partial, 'POS-4412')
        self.assertContains(partial, 'Part payment')
        receipt_url = partial.redirect_chain[-1][0]
        self.assertIn('/accounts/receipts/', receipt_url)
        self.assertEqual(lab.get(receipt_url).status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'PART_PAID')
        self.assertNotContains(phleb.get('/phlebotomy/'), 'Ifeoma')
        self.assertNotContains(radio.get('/radiology/'), 'Ifeoma')
        paid = cashier.post('/accounts/', {'action': 'pay', 'invoice': str(invoice.pk), 'amount': '15500.00', 'method': 'CASH'})
        self.assertEqual(paid.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'PAID')
        self.assertContains(phleb.get('/phlebotomy/'), 'Ifeoma')
        self.assertContains(radio.get('/radiology/'), 'Ifeoma')
        self.assertContains(lab.get('/laboratory/'), 'Ifeoma')
        cleared = cashier.get('/accounts/', {'q': 'Ifeoma'})
        self.assertContains(cleared, 'No outstanding dues match that lookup')

    def test_recent_visits_filter_and_paginate(self):
        from django.test import Client
        from patients.models import Patient, Visit
        screen = Client()
        screen.force_login(self.user)
        for index in range(6):
            patient = Patient.objects.create(first_name='Queue', last_name=f'Person{index}', phone_number=f'0801000000{index}')
            Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.user, referral_source='HMO' if index == 0 else 'SELF', status='PROCESSED')
        other = Centre.objects.create(code='IND', name='Independence')
        hidden = Patient.objects.create(first_name='Queue', last_name='Hidden', phone_number='08019990000')
        Visit.objects.create(patient=hidden, centre=other, registered_by=self.user)
        filtered = screen.get('/patients/register/', {'vq': 'Person0', 'vchannel': 'HMO', 'vstatus': 'PROCESSED'})
        self.assertContains(filtered, 'Person0')
        self.assertNotContains(filtered, 'Person1')
        self.assertNotContains(filtered, 'Hidden')
        self.assertContains(filtered, 'Apply filter')
        first = screen.get('/patients/register/', {'per': 5, 'vpage': 1})
        self.assertContains(first, 'Next')
        self.assertContains(first, '1–5 of 6')
        second = screen.get('/patients/register/', {'per': 5, 'vpage': 2})
        self.assertContains(second, 'Previous')
        self.assertContains(second, '6–6 of 6')
        self.assertContains(second, 'Person0')
        self.assertNotContains(second, 'Person5')

    def test_catalog_is_forbidden_for_front_desk_and_accounts(self):
        from django.test import Client
        def person(username, role):
            user = User.objects.create_user(username=username, email=f'{username}@example.test', password='A-long-unique-password-2026!')
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            UserCentreAccess.objects.create(user=user, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True, is_default=True)
            screen = Client()
            screen.force_login(user)
            return screen
        for role, username in (('Front Desk', 'desk.catalog'), ('Accounts', 'accounts.catalog')):
            screen = person(username, role)
            for path in ('/catalog/', '/catalog/tariffs/', '/catalog/tests/', '/catalog/doctors/'):
                self.assertEqual(screen.get(path).status_code, 403, msg=f'{role} should not open {path}')
            home = screen.get('/')
            self.assertNotContains(home, 'Catalog & pricing')
            self.assertNotContains(home, 'Open catalog')

    def test_platform_admin_can_manage_catalog_and_see_dashboard_strip(self):
        from decimal import Decimal
        from django.contrib.auth.models import Group
        from django.test import Client
        from laboratory.models import ServiceGroup, Tariff, Test
        from referrals.models import ReferralDoctor
        admin = User.objects.create_user(username='catalog.admin', email='catalog.admin@example.test', password='A-long-unique-password-2026!')
        admin.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        UserCentreAccess.objects.create(user=admin, centre=self.centre, can_view=True, can_register=True, can_edit=True, can_approve=True, is_default=True)
        screen = Client()
        screen.force_login(admin)
        hub = screen.get('/catalog/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Catalog & pricing')
        self.assertContains(hub, 'Lab and imaging prices by tariff')
        home = screen.get('/')
        self.assertContains(home, 'Open catalog')
        self.assertContains(home, 'Catalog setup')
        self.assertContains(home, 'Catalog & pricing')
        created_tariff = screen.post('/catalog/tariffs/', {'action': 'create', 'name': 'Walk-in 2026', 'is_active': 'on', 'is_active_submitted': '1'})
        self.assertEqual(created_tariff.status_code, 302)
        tariff = Tariff.objects.get(name='Walk-in 2026')
        self.assertTrue(tariff.is_active)
        detail = screen.get(f'/catalog/tariffs/{tariff.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Laboratory prices')
        group = ServiceGroup.objects.create(name='Haematology', code='1', is_active=True)
        created_test = screen.post('/catalog/tests/', {
            'action': 'create', 'code': 'FBC', 'name': 'Full blood count', 'department': 'HAEMATOLOGY',
            'department_code': '1', 'service_group': str(group.pk), 'specimen': 'EDTA',
            'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created_test.status_code, 302)
        test = Test.objects.get(code='FBC')
        self.assertEqual(test.service_group_id, group.pk)
        missing_group = screen.post('/catalog/tests/', {
            'action': 'create', 'code': 'ESR', 'name': 'ESR', 'department': 'HAEMATOLOGY',
            'department_code': '1', 'specimen': 'Citrate', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(missing_group.status_code, 302)
        self.assertFalse(Test.objects.filter(code='ESR').exists())
        duplicate_group = screen.post('/catalog/service-groups/', {
            'action': 'create', 'name': 'haematology', 'code': '9', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(duplicate_group.status_code, 302)
        self.assertEqual(ServiceGroup.objects.filter(name__iexact='haematology').count(), 1)
        missing_group = screen.post('/catalog/tests/', {
            'action': 'create', 'code': 'ESR', 'name': 'ESR', 'department': 'HAEMATOLOGY',
            'department_code': '1', 'specimen': 'Citrate', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(missing_group.status_code, 302)
        self.assertFalse(Test.objects.filter(code='ESR').exists())
        duplicate_group = screen.post('/catalog/service-groups/', {
            'action': 'create', 'name': 'haematology', 'code': '9', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(duplicate_group.status_code, 302)
        self.assertEqual(ServiceGroup.objects.filter(name__iexact='haematology').count(), 1)
        deactivated = screen.post('/catalog/tests/', {'action': 'deactivate', 'test': str(test.pk)})
        self.assertEqual(deactivated.status_code, 302)
        test.refresh_from_db()
        self.assertFalse(test.is_active)
        created_doctor = screen.post('/catalog/doctors/', {
            'action': 'create', 'centre': str(self.centre.pk), 'doctor_code': 'NWS-CAT1',
            'title': 'Dr', 'first_name': 'Ada', 'last_name': 'Okoro', 'commission_type': 'PERCENT',
            'commission_value': '10', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created_doctor.status_code, 302)
        doctor = ReferralDoctor.objects.get(doctor_code='NWS-CAT1')
        self.assertEqual(doctor.centre_id, self.centre.pk)
        self.assertEqual(doctor.commission_value, Decimal('10'))
        superuser = User.objects.create_superuser(username='catalog.super', email='catalog.super@example.test', password='A-long-unique-password-2026!')
        super_screen = Client()
        super_screen.force_login(superuser)
        self.assertEqual(super_screen.get('/catalog/').status_code, 200)
        self.assertContains(super_screen.get('/'), 'Open catalog')


class RoadmapSecurityHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.centre_a = Centre.objects.create(code='NWS', name='NOWAS')
        self.centre_b = Centre.objects.create(code='IND', name='Independence')
        self.scientist = User.objects.create_user(username='lab.sci', email='lab.sci@example.test', password='A-long-unique-password-2026!')
        self.scientist.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        UserCentreAccess.objects.create(user=self.scientist, centre=self.centre_a, can_view=True, can_edit=True)
        self.manager = User.objects.create_user(username='lab.mgr', email='lab.mgr@example.test', password='A-long-unique-password-2026!')
        self.manager.groups.add(Group.objects.get_or_create(name='Lab Manager')[0])
        UserCentreAccess.objects.create(user=self.manager, centre=self.centre_a, can_view=True, can_edit=True, can_approve=True)

    def test_cross_centre_patient_list_is_scoped(self):
        from patients.models import Patient, Visit
        other_user = User.objects.create_user(username='other', email='other@example.test', password='A-long-unique-password-2026!')
        UserCentreAccess.objects.create(user=other_user, centre=self.centre_b, can_view=True, can_register=True)
        patient = Patient.objects.create(first_name='Cross', last_name='Centre', phone_number='08012345678')
        Visit.objects.create(patient=patient, centre=self.centre_b, registered_by=other_user)
        self.client.force_authenticate(self.scientist)
        response = self.client.get('/api/patients/')
        self.assertEqual(response.status_code, 200)
        ids = [row['uhid'] for row in response.data.get('results', response.data)]
        self.assertNotIn(patient.uhid, ids)

    def test_unauthorised_user_cannot_release_result(self):
        from decimal import Decimal
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from laboratory.models import LabOrder, LabResult, Sample, SampleCodeConfig, ServiceGroup, Tariff, Test, TestPrice
        from patients.models import Patient, PatientDocument, Visit
        patient = Patient.objects.create(first_name='Rel', last_name='Ease', phone_number='08022223333')
        visit = Visit.objects.create(patient=patient, centre=self.centre_a, registered_by=self.manager)
        PatientDocument.objects.create(visit=visit, document_type='REGISTRATION', file=ContentFile(b'%PDF-1.4\n', name='f.pdf'), uploaded_by=self.manager)
        group = ServiceGroup.objects.create(name='Chem', code='2', is_active=True)
        test = Test.objects.create(code='GLU', name='Glucose', department='CHEMISTRY', department_code='1', service_group=group)
        tariff = Tariff.objects.create(name='Std')
        TestPrice.objects.create(test=test, tariff=tariff, price=Decimal('1000'))
        order = LabOrder.objects.create(visit=visit, test=test, tariff=tariff, price=Decimal('1000'), status='IN_PROGRESS')
        SampleCodeConfig.objects.get_or_create(centre=self.centre_a, defaults={'numeric_code': '1', 'is_active': True})
        sample = Sample.objects.create(order=order, centre=self.centre_a, status='AUTHENTICATED', collected_by=self.manager, collected_at=timezone.now())
        result = LabResult.objects.create(sample=sample, status='AUTHENTICATED', entered_by=self.scientist, authenticated_by=self.manager)
        self.client.force_authenticate(self.scientist)
        response = self.client.post(f'/api/lab-results/{result.pk}/release/', {}, format='json')
        self.assertIn(response.status_code, (403, 400))

    def test_password_reset_request_page_ok(self):
        from django.test import Client
        screen = Client()
        response = screen.get('/account/password-reset/')
        self.assertEqual(response.status_code, 200)

    def test_mfa_secret_generation(self):
        from core.mfa import generate_mfa_secret, verify_totp, _hotp
        secret = generate_mfa_secret()
        self.assertTrue(len(secret) >= 16)
        import time
        token = _hotp(secret, int(time.time()) // 30)
        self.assertTrue(verify_totp(secret, token))
