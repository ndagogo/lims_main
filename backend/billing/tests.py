from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from rest_framework.test import APIClient
from core.models import Centre, UserCentreAccess
from patients.models import Patient, Visit
from laboratory.models import ServiceGroup, Test, Tariff, TestPrice
from .models import Invoice, InvoiceItem, Payment, DiscountRequest

User=get_user_model()
class PhaseTwoAccountsTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.user=User.objects.create_user(username='accounts1',email='accounts1@example.test',password='A-long-unique-password-2026!')
        self.centre=Centre.objects.create(code='NWS',name='NOWAS')
        UserCentreAccess.objects.create(user=self.user,centre=self.centre,can_view=True,can_register=True,can_edit=True,can_approve=True,is_default=True)
        self.user.groups.add(Group.objects.get_or_create(name='Accounts')[0])
        self.patient=Patient.objects.create(first_name='Ada',last_name='Okafor',phone_number='8000000001')
        self.visit=Visit.objects.create(patient=self.patient,centre=self.centre,registered_by=self.user)
        group=ServiceGroup.objects.create(name='Haematology',code='1')
        self.test=Test.objects.create(code='CBC',name='Full Blood Count',service_group=group)
        self.tariff=Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=self.tariff,test=self.test,price=Decimal('5500.00'))
        self.invoice=Invoice.objects.create(visit=self.visit,created_by=self.user,status='UNPAID')
        self.client.force_authenticate(self.user)
    def test_patient_duplicate_is_rejected(self):
        response=self.client.post('/api/patients/',{'first_name':'Ada','last_name':'Okafor','phone_number':'8000000001'},format='json')
        self.assertEqual(response.status_code,400)
        self.assertIn('duplicate',str(response.data))
    def test_invoice_number_is_generated(self):
        self.assertTrue(self.invoice.invoice_number.startswith('HFDN-INV-'))
    def test_add_test_requires_tariff_price(self):
        response=self.client.post(f'/api/invoices/{self.invoice.pk}/add_test/',{'test':self.test.pk,'tariff':self.tariff.pk},format='json')
        self.assertEqual(response.status_code,201)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total,Decimal('5500.00'))
    def test_payment_generates_receipt_and_updates_status(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        response=self.client.post('/api/payments/',{'invoice':str(self.invoice.pk),'amount':'5500.00','method':'CASH'},format='json')
        self.assertEqual(response.status_code,201)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status,'PAID')
        self.assertTrue(response.data['receipt_number'].startswith('HFDN-RCP-'))
    def test_overpayment_is_rejected(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        response=self.client.post('/api/payments/',{'invoice':str(self.invoice.pk),'amount':'5501.00','method':'CASH'},format='json')
        self.assertEqual(response.status_code,400)
    def test_discount_approval_updates_invoice(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        discount=DiscountRequest.objects.create(invoice=self.invoice,requested_by=self.user,discount_type='PERCENT',requested_value=Decimal('10'),reason='Approved service adjustment')
        response=self.client.post(f'/api/discount-requests/{discount.pk}/review/',{'decision':'APPROVED'},format='json')
        self.assertEqual(response.status_code,200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.discount_amount,Decimal('550.00'))
        self.assertEqual(self.invoice.total,Decimal('4950.00'))
    def test_user_cannot_access_another_centre_invoice(self):
        other=Centre.objects.create(code='IND',name='Independence')
        other_patient=Patient.objects.create(first_name='John',last_name='Doe',phone_number='08000000002')
        other_visit=Visit.objects.create(patient=other_patient,centre=other)
        other_invoice=Invoice.objects.create(visit=other_visit,status='UNPAID')
        response=self.client.get(f'/api/invoices/{other_invoice.pk}/')
        self.assertEqual(response.status_code,404)
    def test_reversal_keeps_the_amount_and_reopens_the_invoice(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        paid=self.client.post('/api/payments/',{'invoice':str(self.invoice.pk),'amount':'5500.00','method':'CASH'},format='json')
        payment=Payment.objects.get(receipt_number=paid.data['receipt_number'])
        response=self.client.post(f'/api/payments/{payment.pk}/reverse/',{'reason':'Taken on the wrong invoice'},format='json')
        self.assertEqual(response.status_code,200)
        payment.refresh_from_db(); self.invoice.refresh_from_db()
        self.assertEqual(payment.status,'REVERSED')
        self.assertEqual(payment.amount,Decimal('5500.00'))
        self.assertEqual(self.invoice.status,'UNPAID')
        payment.amount=Decimal('1.00')
        with self.assertRaises(ValidationError):
            payment.save()
    def test_cancel_is_refused_while_a_payment_is_confirmed(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        self.client.post('/api/payments/',{'invoice':str(self.invoice.pk),'amount':'5500.00','method':'CASH'},format='json')
        response=self.client.post(f'/api/invoices/{self.invoice.pk}/cancel/',{'reason':'Changed mind'},format='json')
        self.assertEqual(response.status_code,400)
    def test_accounts_screen_records_a_payment(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        screen=Client()
        screen.force_login(self.user)
        response=screen.post('/accounts/',{'action':'pay','invoice':str(self.invoice.pk),'amount':'5500.00','method':'CASH'})
        self.assertEqual(response.status_code,302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status,'PAID')

    def test_cashier_cannot_see_discount_approval_or_reports(self):
        cashier=User.objects.create_user(username='cashier1',email='cashier1@example.test',password='A-long-unique-password-2026!')
        UserCentreAccess.objects.create(user=cashier,centre=self.centre,can_view=True,can_register=True,can_edit=True,is_default=True)
        cashier.groups.add(Group.objects.get_or_create(name='Cashier')[0])
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        DiscountRequest.objects.create(
            invoice=self.invoice,requested_by=cashier,discount_type='PERCENT',
            requested_value=Decimal('10'),reason='Walk-in goodwill',
        )
        screen=Client()
        screen.force_login(cashier)
        paypoint=screen.get('/accounts/')
        self.assertContains(paypoint,'Ready to collect')
        self.assertContains(paypoint,'No invoices ready to collect')
        self.assertContains(paypoint,'My discount requests')
        self.assertContains(paypoint,'PENDING')
        self.assertContains(paypoint,'Held for review')
        self.assertNotContains(paypoint,'Pending discount approvals')
        selected=screen.get('/accounts/',{'due':str(self.invoice.pk)})
        self.assertContains(selected,'waiting for accountant review')
        self.assertNotContains(selected,'name="action" value="pay"')
        self.assertNotContains(selected,'Submit discount request')
        self.assertEqual(screen.get('/accounts/reports/').status_code,302)
        blocked=screen.post('/accounts/',{
            'action':'review_discount',
            'discount':str(DiscountRequest.objects.get(invoice=self.invoice).pk),
            'decision':'APPROVED',
        },follow=True)
        self.assertContains(blocked,'Only an accountant')

    def test_approved_discount_returns_invoice_for_cashier_payment(self):
        cashier=User.objects.create_user(username='cashier2',email='cashier2@example.test',password='A-long-unique-password-2026!')
        UserCentreAccess.objects.create(user=cashier,centre=self.centre,can_view=True,can_register=True,can_edit=True,is_default=True)
        cashier.groups.add(Group.objects.get_or_create(name='Cashier')[0])
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        desk=Client()
        desk.force_login(cashier)
        submitted=desk.post('/accounts/',{
            'action':'discount','invoice':str(self.invoice.pk),
            'discount_type':'PERCENT','requested_value':'10','reason':'Staff relative',
        },follow=True)
        self.assertContains(submitted,'Discount request submitted')
        self.assertContains(submitted,'PENDING')
        self.assertContains(submitted,'No invoices ready to collect')
        pay_blocked=desk.post('/accounts/',{'action':'pay','invoice':str(self.invoice.pk),'amount':'4950.00','method':'CASH'},follow=True)
        self.assertContains(pay_blocked,'waiting for accountant review')
        discount=DiscountRequest.objects.get(invoice=self.invoice)
        approved=self.client.post(f'/api/discount-requests/{discount.pk}/review/',{'decision':'APPROVED'},format='json')
        self.assertEqual(approved.status_code,200)
        ready=desk.get('/accounts/')
        self.assertContains(ready,'APPROVED')
        self.assertContains(ready,'Collect payment')
        self.assertContains(ready,'Ada Okafor')
        self.assertNotContains(ready,'No invoices ready to collect')
        collect=desk.get('/accounts/',{'due':str(self.invoice.pk)})
        self.assertContains(collect,'Record payment')
        self.assertContains(collect,'4950.00')
        paid=desk.post('/accounts/',{'action':'pay','invoice':str(self.invoice.pk),'amount':'4950.00','method':'CASH'})
        self.assertEqual(paid.status_code,302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status,'PAID')

    def test_accountant_reports_and_exports(self):
        InvoiceItem.objects.create(invoice=self.invoice,test=self.test,unit_price=Decimal('5500.00'))
        Payment.objects.create(invoice=self.invoice,amount=Decimal('5500.00'),method='CASH',received_by=self.user,status='CONFIRMED')
        self.invoice.status='PAID'
        self.invoice.save(update_fields=['status'])
        screen=Client()
        screen.force_login(self.user)
        DiscountRequest.objects.create(
            invoice=self.invoice, requested_by=self.user, discount_type='PERCENT',
            requested_value=Decimal('5'), reason='Staff courtesy', status='APPROVED',
            reviewed_by=self.user, applied_amount=Decimal('275.00'),
        )
        reports=screen.get('/accounts/reports/',{'period':'monthly'})
        self.assertEqual(reports.status_code,200)
        self.assertContains(reports,'Financial reports')
        self.assertContains(reports,'Collections')
        self.assertContains(reports,'Export')
        self.assertContains(reports,'Discount history')
        self.assertContains(reports,'Staff courtesy')
        self.assertContains(reports,'Ada Okafor')
        csv_resp=screen.get('/accounts/export/',{'period':'monthly','format':'csv','dataset':'payments'})
        self.assertEqual(csv_resp.status_code,200)
        self.assertIn('text/csv',csv_resp['Content-Type'])
        xlsx_resp=screen.get('/accounts/export/',{'period':'monthly','format':'xlsx','dataset':'summary'})
        self.assertEqual(xlsx_resp.status_code,200)
        self.assertIn('spreadsheetml',xlsx_resp['Content-Type'])
        pdf_resp=screen.get('/accounts/export/',{'period':'monthly','format':'pdf','dataset':'summary'})
        self.assertEqual(pdf_resp.status_code,200)
        self.assertEqual(pdf_resp['Content-Type'],'application/pdf')

    def test_accounts_can_reprint_invoice_and_others_cannot(self):
        from core.models import AuditEvent
        InvoiceItem.objects.create(invoice=self.invoice, test=self.test, unit_price=Decimal('5500.00'))
        screen = Client()
        screen.force_login(self.user)
        page = screen.get('/accounts/')
        self.assertContains(page, 'Reprint Invoice')
        self.assertContains(page, f'/accounts/invoices/{self.invoice.pk}/reprint/')
        printed = screen.get(f'/accounts/invoices/{self.invoice.pk}/reprint/')
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, self.invoice.invoice_number)
        self.assertContains(printed, 'Print invoice')
        self.assertTrue(AuditEvent.objects.filter(action='INVOICE_REPRINTED', visit_ref=self.visit.visit_id).exists())
        outsider = User.objects.create_user(username='lab.only', email='lab.only@example.test', password='A-long-unique-password-2026!')
        UserCentreAccess.objects.create(user=outsider, centre=self.centre, can_view=True, can_edit=True)
        outsider.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        screen.force_login(outsider)
        denied = screen.get(f'/accounts/invoices/{self.invoice.pk}/reprint/')
        self.assertIn(denied.status_code, (302, 403))
