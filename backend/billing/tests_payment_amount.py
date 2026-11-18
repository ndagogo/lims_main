"""Payment amount validation must not 500 on settled / zero-balance invoices."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from rest_framework.test import APIClient

from billing.adjustments import cancel_or_refund_item
from billing.models import Invoice, InvoiceItem, Payment
from billing.workspace import _decorate
from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, ServiceGroup, Tariff, Test
from patients.models import Patient, Visit

User = get_user_model()


class PaymentAmountGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='pay.guard', email='pay.guard@example.test', password='A-long-unique-password-2026!',
        )
        group, _ = Group.objects.get_or_create(name='Accounts')
        group.permissions.set(Permission.objects.filter(codename__in=[
            'payment_refund', 'issue_refund', 'view_invoice', 'view_payment',
        ]))
        self.user.groups.add(group)
        self.centre = Centre.objects.create(code='PAY', name='Pay Centre')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True,
        )
        patient = Patient.objects.create(first_name='Zero', last_name='Balance', phone_number='08077778888')
        self.visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Chem', code='C')
        self.test = Test.objects.create(code='UREA', name='Urea', service_group=group)
        tariff = Tariff.objects.create(name='Pay tariff')
        self.invoice = Invoice.objects.create(visit=self.visit, status='UNPAID', created_by=self.user)
        order = LabOrder.objects.create(visit=self.visit, test=self.test, tariff=tariff, price='2000')
        self.item = InvoiceItem.objects.create(
            invoice=self.invoice, test=self.test, tariff=tariff, lab_order=order, unit_price=Decimal('2000'),
        )
        self.invoice.refresh_from_db()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_zero_amount_returns_validation_error_not_500(self):
        response = self.api.post(
            '/api/payments/',
            {'invoice': str(self.invoice.pk), 'amount': '0', 'method': 'CASH'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('amount', response.data)

    def test_negative_balance_after_refund_is_not_collectable(self):
        Payment.objects.create(
            invoice=self.invoice, amount=self.invoice.total, method='CASH', received_by=self.user,
        )
        self.invoice.refresh_from_db()
        # Force a ledger where confirmed payments exceed remaining total if refunds ignored.
        cancel_or_refund_item(
            user=self.user, invoice_item_id=self.item.pk,
            mode='CANCEL_AND_REFUND', reason='Patient left',
        )
        self.invoice.refresh_from_db()
        _decorate([self.invoice])
        self.assertFalse(self.invoice.can_collect)
        self.assertEqual(self.invoice.balance_input, '0.00')

        screen = Client()
        screen.force_login(self.user)
        posted = screen.post('/accounts/', {
            'action': 'pay',
            'invoice': str(self.invoice.pk),
            'amount': '0.00',
            'method': 'CASH',
        })
        self.assertEqual(posted.status_code, 302)
        self.assertFalse(Payment.objects.filter(invoice=self.invoice).exclude(status='REVERSED').count() > 1)
