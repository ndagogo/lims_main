from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase

from communications.messaging import (
    EVENT_RESULT_READY, notify_patient, notify_payment_receipt, validate_template_text,
)
from communications.models import NotificationOutbox
from core.models import Centre
from patients.models import Patient, Visit
from billing.models import Invoice, Payment


class TemplateVariableTests(TestCase):
    def test_recommended_variables_are_accepted(self):
        validate_template_text('Hello {{ patient_name }} at {{ centre_name }}. Visit {{ visit_id }}. {{ result_link }}')

    def test_unknown_and_sensitive_variables_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_template_text('See {{ unknown_field }}')
        with self.assertRaises(ValidationError):
            validate_template_text('Your password is {{ password }}')
        with self.assertRaises(ValidationError):
            validate_template_text('Result {{ narrative }}')


class NotificationDeliveryTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.patient = Patient.objects.create(
            first_name='Ada', last_name='Okeke', phone_number='8012345678',
            email='ada@example.test', date_of_birth=date(1990, 1, 2),
            notify_sms=True, notify_email=True, notify_whatsapp=True,
        )
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre)

    def test_result_ready_uses_each_consented_channel_and_omits_the_result(self):
        notes = notify_patient(
            event_key=EVENT_RESULT_READY, visit=self.visit,
            idempotency_key='result-ready:test',
            extra={'narrative': 'Haemoglobin 12.4'},
        )
        self.assertEqual({note.channel for note in notes}, {'SMS', 'EMAIL', 'WHATSAPP'})
        for note in notes:
            self.assertIn('/portals/patient/access/', note.body)
            self.assertNotIn('Haemoglobin', note.body)
            self.assertNotIn('password', note.body.lower())
        self.patient.notify_email = False
        self.patient.notify_whatsapp = False
        self.patient.save(update_fields=['notify_email', 'notify_whatsapp'])
        again = notify_patient(event_key=EVENT_RESULT_READY, visit=self.visit, idempotency_key='result-ready:sms-only')
        self.assertEqual([note.channel for note in again], ['SMS'])

    def test_payment_receipt_goes_to_the_patient(self):
        invoice = Invoice.objects.create(visit=self.visit, payer_name='Ada Okeke')
        payment = Payment.objects.create(invoice=invoice, amount=Decimal('1500.00'), method='CASH')
        notify_payment_receipt(payment)
        self.assertTrue(NotificationOutbox.objects.filter(visit=self.visit, channel='SMS', body__icontains=payment.receipt_number).exists())
