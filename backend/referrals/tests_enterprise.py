from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import Client, TestCase

from billing.models import Invoice, InvoiceItem, Payment
from core.models import Centre, UserCentreAccess
from laboratory.models import ServiceGroup, Test
from patients.models import Patient, Visit
from referrals.models import (
    CommissionLedgerEntry, ReferralAttribution, ReferralDoctor, ReferralInstitution, WithdrawalRequest,
)
from referrals.reporting import build_commission_due, parse_referral_filters


class EnterpriseReferralWorkspaceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.admin = User.objects.create_superuser(
            username='ref.admin', email='ref.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.accounts = User.objects.create_user(
            username='ref.acct', email='ref.acct@example.test', password='A-long-unique-password-2026!',
        )
        self.accounts.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        UserCentreAccess.objects.create(
            user=self.accounts, centre=self.centre, can_view=True, can_edit=True, can_register=True, is_default=True,
        )
        self.outsider = User.objects.create_user(
            username='ref.out', email='ref.out@example.test', password='A-long-unique-password-2026!',
        )
        self.doctor = ReferralDoctor.objects.create(
            centre=self.centre, doctor_code='DR-ENT1', first_name='Ife', last_name='Balogun',
            commission_type='PERCENT', commission_value=Decimal('10'),
            bank_name='Access', account_name='Ife Balogun', account_number='0123456789',
        )
        self.institution = ReferralInstitution.objects.create(doctor=self.doctor, name='City Clinic')
        self.fixed = ReferralDoctor.objects.create(
            centre=self.centre, doctor_code='DR-FIX', first_name='Ada', last_name='Okeke',
            commission_type='FIXED', commission_value=Decimal('500.00'),
        )
        patient = Patient.objects.create(first_name='Chidi', last_name='Eze', phone_number='08011112222')
        self.visit = Visit.objects.create(
            patient=patient, centre=self.centre, registered_by=self.accounts, referral_source='DOCTOR',
        )
        attr = ReferralAttribution.objects.create(visit=self.visit, doctor=self.doctor, recorded_by=self.accounts)
        attr.institutions.add(self.institution)
        group = ServiceGroup.objects.create(name='Chemistry', code='1')
        test = Test.objects.create(code='GLU', name='Glucose', service_group=group)
        self.invoice = Invoice.objects.create(visit=self.visit, created_by=self.accounts, status='UNPAID')
        InvoiceItem.objects.create(invoice=self.invoice, test=test, unit_price=Decimal('10000.00'))
        Payment.objects.create(
            invoice=self.invoice, amount=Decimal('10000.00'), method='TRANSFER',
            status='CONFIRMED', received_by=self.accounts,
        )

    def test_commission_due_math_percent_and_outstanding_balance(self):
        filters = parse_referral_filters({'period': 'annual'}, self.admin)
        report = build_commission_due(filters)
        row = next(r for r in report['rows'] if r[0] == 'DR-ENT1')
        self.assertEqual(row[6], Decimal('1000.00'))
        self.assertEqual(row[10], Decimal('1000.00'))
        self.assertEqual(row[3], '10%')
        self.assertEqual(row[11], 'Access')

    def test_fixed_commission_doctor_appears_after_accrual(self):
        patient = Patient.objects.create(first_name='Ngozi', last_name='Oka', phone_number='08022223333')
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.accounts)
        ReferralAttribution.objects.create(visit=visit, doctor=self.fixed, recorded_by=self.accounts)
        test = Test.objects.get(code='GLU')
        invoice = Invoice.objects.create(visit=visit, created_by=self.accounts, status='UNPAID')
        InvoiceItem.objects.create(invoice=invoice, test=test, unit_price=Decimal('2000.00'))
        Payment.objects.create(
            invoice=invoice, amount=Decimal('2000.00'), method='CASH',
            status='CONFIRMED', received_by=self.accounts,
        )
        filters = parse_referral_filters({'period': 'annual', 'commission_type': 'FIXED'}, self.admin)
        report = build_commission_due(filters)
        row = next(r for r in report['rows'] if r[0] == 'DR-FIX')
        self.assertEqual(row[6], Decimal('500.00'))
        self.assertEqual(row[10], Decimal('500.00'))

    def test_centre_scope_hides_other_centre_doctors(self):
        other = Centre.objects.create(code='IND', name='Independent')
        ReferralDoctor.objects.create(
            centre=other, doctor_code='DR-OTHER', first_name='Other', last_name='Doc',
            commission_type='PERCENT', commission_value=Decimal('5'),
        )
        filters = parse_referral_filters({'period': 'annual', 'centre': str(self.centre.pk)}, self.accounts)
        report = build_commission_due(filters)
        codes = [row[0] for row in report['rows']]
        self.assertIn('DR-ENT1', codes)
        self.assertNotIn('DR-OTHER', codes)

    def test_workspace_pages_and_exports(self):
        screen = Client()
        screen.force_login(self.admin)
        for path in (
            '/enterprise/referrals/',
            '/enterprise/referrals/doctors/',
            '/enterprise/referrals/commission/',
            '/enterprise/referrals/visits/',
            '/enterprise/referrals/payouts/',
        ):
            response = screen.get(path)
            self.assertEqual(response.status_code, 200, path)
        for fmt in ('csv', 'xlsx', 'pdf'):
            export = screen.get(f'/enterprise/referrals/export/?kind=commission_due&format={fmt}&period=annual')
            self.assertEqual(export.status_code, 200, fmt)
            self.assertTrue(export.get('Content-Disposition', '').startswith('attachment'))
        catalogue = screen.get('/reporting/catalogue/?report=commission_due&period=annual')
        self.assertEqual(catalogue.status_code, 200)
        self.assertContains(catalogue, 'Referral commission due')

    def test_outsider_cannot_open_workspace(self):
        screen = Client()
        screen.force_login(self.outsider)
        self.assertEqual(screen.get('/enterprise/referrals/').status_code, 403)

    def test_payout_desk_settle_creates_settlement(self):
        withdrawal = WithdrawalRequest.objects.create(
            doctor=self.doctor, amount=Decimal('400.00'), status='REQUESTED',
            requested_by=self.accounts,
        )
        screen = Client()
        screen.force_login(self.accounts)
        approved = screen.post('/enterprise/referrals/payouts/', {
            'action': 'approve', 'withdrawal': str(withdrawal.pk), 'review_note': 'OK',
        })
        self.assertEqual(approved.status_code, 302)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, 'APPROVED')
        proof = SimpleUploadedFile('proof.pdf', b'%PDF-1.4 settlement', content_type='application/pdf')
        settled = screen.post(
            '/enterprise/referrals/payouts/',
            {'action': 'settle', 'withdrawal': str(withdrawal.pk), 'proof_of_payment': proof},
        )
        self.assertEqual(settled.status_code, 302)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, 'SETTLED')
        self.assertTrue(
            CommissionLedgerEntry.objects.filter(
                doctor=self.doctor, entry_type='SETTLEMENT', amount=Decimal('-400.00'),
            ).exists()
        )
        balance = CommissionLedgerEntry.objects.filter(doctor=self.doctor).aggregate(t=Sum('amount'))['t']
        self.assertEqual(balance, Decimal('600.00'))
