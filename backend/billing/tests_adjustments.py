from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from billing.adjustments import cancel_or_refund_item, replace_invoice_item
from billing.models import BillAmendment, Invoice, InvoiceItem, Payment, Refund
from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, ServiceGroup, Tariff, Test, TestPrice
from patients.models import Patient, Visit

User = get_user_model()


class AdjustmentScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='acct.adjust', email='acct.adjust@example.test',
            password='A-long-unique-password-2026!',
        )
        group, _ = Group.objects.get_or_create(name='Accounts')
        group.permissions.set(Permission.objects.filter(codename__in=[
            'payment_refund', 'issue_refund', 'bill_edit', 'bill_modify', 'view_invoice', 'view_payment',
        ]))
        self.user.groups.add(group)
        self.centre = Centre.objects.create(code='ADJ', name='Adjust Centre')
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True,
        )
        self.patient = Patient.objects.create(
            first_name='Mark', last_name='Refund', phone_number='8011112233',
        )
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.user)
        group_lab = ServiceGroup.objects.create(name='Haematology Adj', code='HA')
        self.cbc = Test.objects.create(code='CBC', name='CBC', service_group=group_lab)
        self.mp = Test.objects.create(code='MP', name='Malaria parasite', service_group=group_lab)
        self.tariff = Tariff.objects.create(name='Adj tariff')
        TestPrice.objects.create(test=self.cbc, tariff=self.tariff, price=Decimal('3000'))
        TestPrice.objects.create(test=self.mp, tariff=self.tariff, price=Decimal('1500'))
        self.invoice = Invoice.objects.create(visit=self.visit, payer_name='Mark', status='UNPAID', created_by=self.user)
        self.order_cbc = LabOrder.objects.create(visit=self.visit, test=self.cbc, tariff=self.tariff, price=Decimal('3000'))
        self.order_mp = LabOrder.objects.create(visit=self.visit, test=self.mp, tariff=self.tariff, price=Decimal('1500'))
        self.item_cbc = InvoiceItem.objects.create(
            invoice=self.invoice, test=self.cbc, tariff=self.tariff, lab_order=self.order_cbc, unit_price=Decimal('3000'),
        )
        self.item_mp = InvoiceItem.objects.create(
            invoice=self.invoice, test=self.mp, tariff=self.tariff, lab_order=self.order_mp, unit_price=Decimal('1500'),
        )
        self.invoice.refresh_from_db()

    def test_cancel_and_refund_one_test(self):
        Payment.objects.create(invoice=self.invoice, amount=self.invoice.total, method='CASH', received_by=self.user)
        self.invoice.refresh_from_db()
        refund = cancel_or_refund_item(
            user=self.user, invoice_item_id=self.item_mp.pk,
            mode='CANCEL_AND_REFUND', reason='Patient request',
        )
        self.invoice.refresh_from_db()
        self.order_mp.refresh_from_db()
        self.assertEqual(refund.amount, Decimal('1500.00'))
        self.assertEqual(refund.refund_mode, 'CANCEL_AND_REFUND')
        self.assertEqual(self.order_mp.status, 'CANCELLED')
        self.assertEqual(self.invoice.total, Decimal('3000.00'))
        self.assertFalse(InvoiceItem.objects.filter(pk=self.item_mp.pk).exists())
        self.assertEqual(Refund.objects.filter(invoice=self.invoice).count(), 1)

    def test_replace_item_balances(self):
        amendment = replace_invoice_item(
            user=self.user,
            invoice_item_id=self.item_mp.pk,
            replacement_kind='laboratory',
            replacement_id=str(self.cbc.pk),
            tariff_id=str(self.tariff.pk),
            reason='Wrong test selected',
        )
        self.invoice.refresh_from_db()
        self.assertEqual(amendment.action, 'REPLACE_ITEM')
        self.assertEqual(self.invoice.total, Decimal('6000.00'))
        self.assertEqual(BillAmendment.objects.filter(invoice=self.invoice, action='REPLACE_ITEM').count(), 1)
        self.assertEqual(self.client.login(username='acct.adjust', password='A-long-unique-password-2026!'), True)
        response = self.client.get('/accounts/refunds/')
        self.assertEqual(response.status_code, 200)
        edit = self.client.get('/accounts/bill-edit/')
        self.assertEqual(edit.status_code, 200)

    def test_resulted_line_cannot_be_edited(self):
        from django.core.exceptions import ValidationError
        from billing.adjustments import RESULTED_BILL_MESSAGE
        self.order_mp.status = 'COMPLETED'
        self.order_mp.save(update_fields=['status'])
        with self.assertRaisesMessage(ValidationError, RESULTED_BILL_MESSAGE):
            replace_invoice_item(
                user=self.user, invoice_item_id=self.item_mp.pk, replacement_kind='laboratory',
                replacement_id=str(self.cbc.pk), tariff_id=str(self.tariff.pk), reason='Change',
            )
        self.client.login(username='acct.adjust', password='A-long-unique-password-2026!')
        response = self.client.post('/accounts/bill-edit/', {
            'action': 'remove_item', 'invoice_item': str(self.item_mp.pk),
            'visit': str(self.visit.pk), 'reason': 'Change',
        }, follow=True)
        self.assertContains(response, 'The bill has been resulted already.')
        self.assertTrue(InvoiceItem.objects.filter(pk=self.item_mp.pk).exists())
        page = self.client.get('/accounts/bill-edit/', {'q': self.visit.visit_id, 'visit': str(self.visit.pk)})
        self.assertContains(page, 'This bill cannot be edited. The bill has been resulted already.', count=1)
        self.assertContains(page, 'Replace test', count=1)

    def test_resulted_line_cannot_be_cancelled_or_refunded(self):
        from django.core.exceptions import ValidationError
        from billing.adjustments import RESULTED_REFUND_MESSAGE
        Payment.objects.create(invoice=self.invoice, amount=self.invoice.total, method='CASH', received_by=self.user)
        self.order_mp.status = 'COMPLETED'
        self.order_mp.save(update_fields=['status'])
        with self.assertRaisesMessage(ValidationError, RESULTED_REFUND_MESSAGE):
            cancel_or_refund_item(
                user=self.user, invoice_item_id=self.item_mp.pk,
                mode='CANCEL_AND_REFUND', reason='Patient request',
            )
        self.client.login(username='acct.adjust', password='A-long-unique-password-2026!')
        response = self.client.post('/accounts/refunds/', {
            'action': 'cancel_refund_item', 'invoice_item': str(self.item_mp.pk), 'mode': 'CANCEL_ONLY',
            'visit': str(self.visit.pk), 'reason': 'Patient request',
        }, follow=True)
        self.assertContains(response, 'The bill has been resulted already.')
        self.assertTrue(InvoiceItem.objects.filter(pk=self.item_mp.pk).exists())
        self.assertFalse(Refund.objects.filter(invoice=self.invoice).exists())
        page = self.client.get('/accounts/refunds/', {'q': self.visit.visit_id, 'visit': str(self.visit.pk)})
        self.assertContains(page, RESULTED_REFUND_MESSAGE, count=1)
        self.assertContains(page, 'value="cancel_refund_item"', count=1)

    def test_phone_lookup_and_demographics_screens(self):
        desk = User.objects.create_user(
            username='desk.adjust', email='desk.adjust@example.test',
            password='A-long-unique-password-2026!',
        )
        front, _ = Group.objects.get_or_create(name='Front Desk')
        desk.groups.add(front)
        UserCentreAccess.objects.create(user=desk, centre=self.centre, can_view=True, can_register=True)
        self.client.login(username='desk.adjust', password='A-long-unique-password-2026!')
        lookup = self.client.get('/patients/register/phone-lookup/', {'phone': '8011112233'})
        self.assertEqual(lookup.status_code, 200)
        self.assertTrue(lookup.json()['found'])
        admin = User.objects.create_user(
            username='admin.adjust', email='admin.adjust@example.test',
            password='A-long-unique-password-2026!', is_superuser=True,
        )
        self.client.login(username='admin.adjust', password='A-long-unique-password-2026!')
        demo = self.client.get('/patients/demographics/', {'q': '8011112233'})
        self.assertEqual(demo.status_code, 200)
        self.assertContains(demo, 'Mark')
