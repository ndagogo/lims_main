from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from billing.eligibility import check_visit_release
from billing.models import BillAmendment, Invoice, InvoiceItem, Payment, Refund
from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, ServiceGroup, Tariff, Test, TestPrice
from patients.models import Patient, Visit

User = get_user_model()


class BillingRoadmapTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='acct', email='acct@example.test', password='A-long-unique-password-2026!')
        self.user.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True)
        self.patient = Patient.objects.create(first_name='Bill', last_name='Road', phone_number='08055556666')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Chemistry', code='1')
        self.test = Test.objects.create(code='UREA', name='Urea', service_group=group)
        self.tariff = Tariff.objects.create(name='Std')
        TestPrice.objects.create(test=self.test, tariff=self.tariff, price=Decimal('2000'))
        self.order = LabOrder.objects.create(visit=self.visit, test=self.test, tariff=self.tariff, price=Decimal('2000'))
        self.invoice = Invoice.objects.create(visit=self.visit, payer_name='Bill', status='UNPAID', created_by=self.user)
        InvoiceItem.objects.create(invoice=self.invoice, test=self.test, tariff=self.tariff, lab_order=self.order, unit_price=Decimal('2000'))
        self.invoice.refresh_from_db()

    def test_release_blocked_without_document(self):
        blockers = check_visit_release(self.visit, check_workflow=False)
        codes = [b.code for b in blockers]
        self.assertIn('missing_document', codes)

    def test_bill_amendment_and_refund_models(self):
        payment = Payment.objects.create(invoice=self.invoice, amount=self.invoice.total, method='CASH', received_by=self.user)
        amendment = BillAmendment.objects.create(
            invoice=self.invoice, action='ADJUST_PRICE', description='Price correction',
            amount_delta=Decimal('-100'), reason='Tariff error', authorised_by=self.user,
        )
        refund = Refund.objects.create(
            payment=payment, amount=Decimal('100'), reason='Partial refund',
            authorised_by=self.user, status='COMPLETED',
        )
        self.assertEqual(amendment.invoice_id, self.invoice.pk)
        self.assertEqual(refund.payment_id, payment.pk)

    def test_release_eligibility_api(self):
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/billing/release-eligibility/', {'visit': str(self.visit.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['eligible'])
