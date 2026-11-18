"""Credit-client wallet debits only when the invoice becomes Paid."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import QueryDict
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Payment
from billing.registration_billing import process_registration
from core.models import Centre, UserCentreAccess
from laboratory.models import ServiceGroup, Tariff, Test, TestPrice
from patients.channel_seed import ensure_default_client_types
from patients.models import ClientType, CreditWalletLedger, OrganisationClient, Patient, RegistrationChannel, Visit

User = get_user_model()


class CreditWalletPaidOnlyTests(TestCase):
    def setUp(self):
        ensure_default_client_types()
        self.user = User.objects.create_user(
            username='credit.acct', email='credit.acct@example.test', password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        self.user.groups.add(Group.objects.get_or_create(name='Front Desk')[0])
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True, is_default=True,
        )
        self.tariff = Tariff.objects.create(name='Credit tariff', status='APPROVAL', is_active=True)
        group = ServiceGroup.objects.create(name='Chemistry', code='1')
        self.test = Test.objects.create(code='GLU', name='Glucose', service_group=group, department_code='1')
        TestPrice.objects.create(tariff=self.tariff, test=self.test, price=Decimal('10000.00'))
        credit_type = ClientType.objects.get(code='CREDIT')
        self.client_org = OrganisationClient.objects.create(
            centre=self.centre,
            client_type=credit_type,
            client_type_code='CREDIT',
            name='Acme Credit Ltd',
            tariff=self.tariff,
            credit_limit=Decimal('50000.00'),
            credit_wallet_balance=Decimal('50000.00'),
            approval_status='APPROVED',
            is_active=True,
        )
        RegistrationChannel.objects.update_or_create(
            centre=self.centre, code='CREDIT_CLIENT',
            defaults={'name': 'Credit clients', 'tariff': self.tariff, 'is_active': True, 'show_test_prices': False},
        )
        self.patient = Patient.objects.create(first_name='Chidi', last_name='Eze', phone_number='08055556666')
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _register_credit_visit(self):
        visit = Visit.objects.create(
            patient=self.patient, centre=self.centre, registered_by=self.user,
            referral_source='CREDIT_CLIENT', client=self.client_org, client_name=self.client_org.name,
            status='REGISTERED',
        )
        post = QueryDict(mutable=True)
        post.setlist('lab_tests', [str(self.test.pk)])
        invoice = process_registration(visit, self.user, post)
        return visit, invoice

    def test_registration_does_not_debit_wallet_while_unpaid(self):
        before = self.client_org.credit_wallet_balance
        visit, invoice = self._register_credit_visit()
        self.client_org.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'UNPAID')
        self.assertEqual(self.client_org.credit_wallet_balance, before)
        self.assertFalse(CreditWalletLedger.objects.filter(invoice=invoice, entry_type='DEBIT').exists())
        self.assertEqual(visit.client_id, self.client_org.pk)

    def test_wallet_debits_only_when_invoice_becomes_paid(self):
        before = self.client_org.credit_wallet_balance
        _visit, invoice = self._register_credit_visit()
        invoice.refresh_from_db()
        self.assertEqual(invoice.total, Decimal('10000.00'))

        paid = self.api.post(
            '/api/payments/',
            {'invoice': str(invoice.pk), 'amount': '10000.00', 'method': 'CREDIT'},
            format='json',
        )
        self.assertEqual(paid.status_code, 201, paid.data)
        invoice.refresh_from_db()
        self.client_org.refresh_from_db()
        self.assertEqual(invoice.status, 'PAID')
        self.assertEqual(self.client_org.credit_wallet_balance, before - Decimal('10000.00'))
        self.assertEqual(self.client_org.credit_wallet_used, Decimal('10000.00'))
        debit = CreditWalletLedger.objects.get(invoice=invoice, entry_type='DEBIT')
        self.assertEqual(debit.created_by_id, self.user.pk)

        # Idempotent: recalculate again must not double-debit.
        invoice.recalculate()
        self.client_org.refresh_from_db()
        self.assertEqual(self.client_org.credit_wallet_balance, before - Decimal('10000.00'))
        self.assertEqual(CreditWalletLedger.objects.filter(invoice=invoice, entry_type='DEBIT').count(), 1)

        # Repair path: already-Paid invoice with no ledger is debited on recalculate.
        CreditWalletLedger.objects.filter(invoice=invoice, entry_type='DEBIT').delete()
        OrganisationClient.objects.filter(pk=self.client_org.pk).update(
            credit_wallet_balance=before, credit_wallet_used=Decimal('0.00'),
        )
        invoice.recalculate()
        self.client_org.refresh_from_db()
        repaired = CreditWalletLedger.objects.get(invoice=invoice, entry_type='DEBIT')
        self.assertEqual(self.client_org.credit_wallet_balance, before - Decimal('10000.00'))
        self.assertEqual(repaired.created_by_id, self.user.pk)

    def test_wallet_restores_when_payment_is_reversed(self):
        before = self.client_org.credit_wallet_balance
        _visit, invoice = self._register_credit_visit()
        paid = self.api.post(
            '/api/payments/',
            {'invoice': str(invoice.pk), 'amount': '10000.00', 'method': 'CREDIT'},
            format='json',
        )
        payment = Payment.objects.get(receipt_number=paid.data['receipt_number'])
        reverse = self.api.post(f'/api/payments/{payment.pk}/reverse/', {'reason': 'Wrong client bill'}, format='json')
        self.assertEqual(reverse.status_code, 200, reverse.data)
        invoice.refresh_from_db()
        self.client_org.refresh_from_db()
        self.assertEqual(invoice.status, 'UNPAID')
        self.assertEqual(self.client_org.credit_wallet_balance, before)
        restore = CreditWalletLedger.objects.get(
            invoice=invoice, entry_type='ADJUSTMENT', reference='WALLET_RESTORE',
        )
        self.assertEqual(restore.created_by_id, self.user.pk)
