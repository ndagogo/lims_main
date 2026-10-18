from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from billing.models import Invoice, InvoiceItem, Payment
from core.models import Centre, UserCentreAccess
from laboratory.models import Test
from patients.models import Patient, Visit
from .models import CommissionLedgerEntry, ReferralAttribution, ReferralDoctor

class ReferralAmountTests(SimpleTestCase):
    def test_percentage_commission_math(self):
        self.assertEqual((Decimal('10000.00')*Decimal('5.00')/Decimal('100')).quantize(Decimal('0.01')),Decimal('500.00'))

class CommissionAccrualTests(TestCase):
    def test_paid_invoice_accrues_and_reversal_undoes_it(self):
        user = get_user_model().objects.create_user(username='finance1', email='finance1@example.test', password='A-long-unique-password-2026!')
        user.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=user, centre=centre, can_view=True, can_edit=True, can_register=True)
        patient = Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08033334444')
        visit = Visit.objects.create(patient=patient, centre=centre, registered_by=user)
        doctor = ReferralDoctor.objects.create(centre=centre, doctor_code='DR1', first_name='Ife', last_name='Balogun', commission_type='PERCENT', commission_value=Decimal('10'))
        ReferralAttribution.objects.create(visit=visit, doctor=doctor, recorded_by=user)
        from laboratory.models import ServiceGroup
        group = ServiceGroup.objects.create(name='Chemistry', code='1')
        test = Test.objects.create(code='GLU', name='Glucose', service_group=group)
        invoice = Invoice.objects.create(visit=visit, created_by=user, status='UNPAID')
        InvoiceItem.objects.create(invoice=invoice, test=test, unit_price=Decimal('10000.00'))
        client = APIClient()
        client.force_authenticate(user)
        paid = client.post('/api/payments/', {'invoice': str(invoice.pk), 'amount': '10000.00', 'method': 'TRANSFER'}, format='json')
        self.assertEqual(paid.status_code, 201)
        accrual = CommissionLedgerEntry.objects.get(entry_type='ACCRUAL')
        self.assertEqual(accrual.amount, Decimal('1000.00'))
        payment = Payment.objects.get(receipt_number=paid.data['receipt_number'])
        reversed_payment = client.post(f'/api/payments/{payment.pk}/reverse/', {'reason': 'Duplicate receipt'}, format='json')
        self.assertEqual(reversed_payment.status_code, 200)
        reversal = CommissionLedgerEntry.objects.get(entry_type='REVERSAL')
        self.assertEqual(reversal.amount, Decimal('-1000.00'))
        self.assertEqual(reversal.source_entry_id, accrual.pk)
