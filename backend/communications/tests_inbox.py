from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from billing.models import DiscountRequest, Invoice
from communications.inbox import record_corporate_invoice, record_discount_request, record_study_assigned
from communications.messaging import EVENT_RESULT_READY, notify_patient
from communications.models import AppNotification
from core.models import Centre, UserCentreAccess
from patients.models import OrganisationClient, Patient, Visit
from portals.models import PortalAccessToken
from radiology.models import ImagingModality, ImagingOrder, RadiologyExam

User = get_user_model()


class InAppNoticeTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.radiologist = User.objects.create_user(username='rad-notice', email='rad-notice@example.test', password='A-long-unique-password-2026!')
        self.other = User.objects.create_user(username='other-notice', email='other-notice@example.test', password='A-long-unique-password-2026!')
        self.cashier = User.objects.create_user(username='cashier-notice', email='cashier-notice@example.test', password='A-long-unique-password-2026!')
        self.accountant = User.objects.create_user(
            username='accounts-notice', email='accounts@example.test', password='A-long-unique-password-2026!',
        )
        Group.objects.get_or_create(name='Accounts')[0].user_set.add(self.accountant)
        UserCentreAccess.objects.create(user=self.accountant, centre=self.centre, can_view=True)
        UserCentreAccess.objects.create(user=self.cashier, centre=self.centre, can_view=True, can_register=True)
        self.patient = Patient.objects.create(
            first_name='Ada', last_name='Okeke', phone_number='8012345678', date_of_birth=date(1990, 1, 2),
        )
        self.other_patient = Patient.objects.create(
            first_name='Emeka', last_name='Nwosu', phone_number='8012345679', date_of_birth=date(1991, 2, 3),
        )
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.cashier)
        modality = ImagingModality.objects.create(centre=self.centre, code='XR1', name='X-ray Room 1', modality_type='XRAY')
        order = ImagingOrder.objects.create(visit=self.visit, modality=modality, requested_by=self.cashier)
        self.exam = RadiologyExam.objects.create(order=order, status='ACQUIRED')

    def test_assigned_study_reaches_only_that_radiologist(self):
        record_study_assigned(self.exam, self.radiologist)
        record_study_assigned(self.exam, self.radiologist)
        self.assertEqual(AppNotification.objects.filter(user=self.radiologist).count(), 1)
        self.assertFalse(AppNotification.objects.filter(user=self.other).exists())
        note = AppNotification.objects.get(user=self.radiologist)
        self.assertEqual(note.title, 'New study assigned')
        self.assertIn('X-ray Room 1', note.body)
        self.assertIn('Ada Okeke', note.body)

        client = Client()
        client.force_login(self.radiologist)
        page = client.get(reverse('notification-inbox'))
        self.assertContains(page, 'New study assigned')
        self.assertContains(page, 'nb-btn')
        opened = client.post(reverse('notification-open', args=[note.pk]))
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(opened.url, '/radiology/')
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)
        feed = client.get(reverse('notification-feed'))
        self.assertEqual(feed.json()['unread'], 0)

        outsider = Client()
        outsider.force_login(self.other)
        self.assertEqual(outsider.post(reverse('notification-open', args=[note.pk])).status_code, 404)
        self.assertNotContains(outsider.get(reverse('notification-inbox')), 'Ada Okeke')

    def test_discount_request_reaches_accounts_and_not_the_requester(self):
        invoice = Invoice.objects.create(visit=self.visit, status='UNPAID', created_by=self.cashier)
        discount = DiscountRequest.objects.create(
            invoice=invoice, requested_by=self.cashier, discount_type='PERCENT',
            requested_value=Decimal('10'), reason='Staff courtesy',
        )
        record_discount_request(discount, actor=self.cashier)
        self.assertTrue(AppNotification.objects.filter(user=self.accountant, title='Discount request').exists())
        self.assertFalse(AppNotification.objects.filter(user=self.cashier, event_key='discount-requested').exists())
        self.assertFalse(AppNotification.objects.filter(user=self.radiologist, event_key='discount-requested').exists())

    def test_patient_sees_only_their_result_notice(self):
        notify_patient(event_key=EVENT_RESULT_READY, visit=self.visit, idempotency_key='result-ready:inbox')
        notify_patient(event_key=EVENT_RESULT_READY, visit=self.visit, idempotency_key='result-ready:inbox')
        note = AppNotification.objects.get(patient=self.patient)
        self.assertEqual(note.title, 'Your result is ready')
        self.assertNotIn('password', note.body.lower())

        own = Client()
        session = own.session
        session['portal_token'] = PortalAccessToken.issue('PATIENT', patient=self.patient).token
        session.save()
        page = own.get(reverse('notification-inbox'))
        self.assertContains(page, 'Your result is ready')
        own.post(reverse('notification-open', args=[note.pk]))
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)

        other = Client()
        other_session = other.session
        other_session['portal_token'] = PortalAccessToken.issue('PATIENT', patient=self.other_patient).token
        other_session.save()
        self.assertNotContains(other.get(reverse('notification-inbox')), 'Your result is ready')
        self.assertEqual(other.post(reverse('notification-open', args=[note.pk])).status_code, 404)

    def test_corporate_invoice_stays_on_that_account(self):
        company = OrganisationClient.objects.create(centre=self.centre, kind='CORPORATE', name='Harbour Clinic')
        self.visit.client = company
        self.visit.save(update_fields=['client', 'updated_at'])
        invoice = Invoice.objects.create(visit=self.visit, status='UNPAID', created_by=self.cashier)
        record_corporate_invoice(invoice)
        note = AppNotification.objects.get(organisation=company)
        self.assertEqual(note.title, 'New invoice')
        self.assertIn(invoice.invoice_number, note.body)

        client = Client()
        session = client.session
        session['portal_token'] = PortalAccessToken.issue('CORPORATE', organisation=company).token
        session.save()
        self.assertContains(client.get(reverse('notification-inbox')), 'New invoice')

        patient = Client()
        patient_session = patient.session
        patient_session['portal_token'] = PortalAccessToken.issue('PATIENT', patient=self.patient).token
        patient_session.save()
        self.assertNotContains(patient.get(reverse('notification-inbox')), 'New invoice')
