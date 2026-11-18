"""Centre receipt phones/footer, price-display defaults OFF, POS/TRANSFER bank tagging."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, Payment
from billing.pricing import effective_show_test_prices
from billing.receipt_pdf import build_receipt_pdf
from core.models import Centre, CentreBank, CentrePhone, UserCentreAccess
from patients.models import Patient, RegistrationChannel, Visit


User = get_user_model()


class CentreReceiptAndBankTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='TST', name='Test Centre', phone='0800111222')
        self.user = User.objects.create_user(
            username='cashier', email='cashier@example.test', password='A-long-unique-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        UserCentreAccess.objects.create(
            user=self.user, centre=self.centre, can_view=True, can_register=True, can_edit=True, is_default=True,
        )
        self.patient = Patient.objects.create(first_name='Ada', last_name='Okeke', phone_number='0800111222')
        self.visit = Visit.objects.create(
            patient=self.patient, centre=self.centre, referral_source='SELF', show_test_prices=False,
            registered_by=self.user,
        )
        self.invoice = Invoice.objects.create(
            visit=self.visit, payer_name='Ada', subtotal=Decimal('1000.00'), total=Decimal('1000.00'),
            status='UNPAID', created_by=self.user,
        )
        self.bank = CentreBank.objects.create(
            centre=self.centre, bank_name='Access Bank', account_name='HFDN TST', account_number='0123456789',
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_receipt_uses_selected_phones_and_footer(self):
        CentrePhone.objects.create(
            centre=self.centre, label='Lab', number='0800111000', show_on_receipt=True, sort_order=0,
        )
        CentrePhone.objects.create(
            centre=self.centre, label='Private', number='0800999000', show_on_receipt=False, sort_order=1,
        )
        self.centre.receipt_footer = 'NO REFUND after payment. Thanks Management'
        self.centre.save(update_fields=['receipt_footer'])
        phones = self.centre.receipt_phone_numbers()
        self.assertEqual(phones, ['Lab: 0800111000'])
        contact = self.centre.receipt_contact_line()
        self.assertIn('0800111000', contact)
        self.assertNotIn('0800999000', contact)
        payment = Payment.objects.create(
            invoice=self.invoice, amount=Decimal('1000.00'), method='CASH', received_by=self.user,
        )
        self.assertEqual(payment.invoice.visit.centre.receipt_footer, 'NO REFUND after payment. Thanks Management')
        pdf = build_receipt_pdf(payment)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 500)

    def test_price_display_defaults_off_when_channel_missing_or_off(self):
        self.assertFalse(effective_show_test_prices(centre=self.centre, channel_code='SELF'))
        RegistrationChannel.objects.create(
            centre=self.centre, code='SELF', name='Self', show_test_prices=False, is_active=True,
        )
        self.assertFalse(effective_show_test_prices(centre=self.centre, channel_code='SELF'))

    def test_pos_payment_requires_centre_bank(self):
        bad = self.api.post('/api/payments/', {
            'invoice': str(self.invoice.pk),
            'amount': '1000.00',
            'method': 'POS',
            'reference': 'slip-1',
        }, format='json')
        self.assertEqual(bad.status_code, 400)

        ok = self.api.post('/api/payments/', {
            'invoice': str(self.invoice.pk),
            'amount': '1000.00',
            'method': 'POS',
            'reference': 'slip-1',
            'centre_bank': str(self.bank.pk),
        }, format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        payment = Payment.objects.get(pk=ok.data['id'])
        self.assertEqual(payment.centre_bank_id, self.bank.pk)
