from django.core.exceptions import ValidationError
from django.test import TestCase

from laboratory.models import LabOrder, ServiceGroup, Tariff, Test, TestPrice
from patients.models import Patient, Visit
from core.models import Centre
from django.contrib.auth import get_user_model
from decimal import Decimal

from workflows.management.commands.seed_workflows import Command as SeedCommand
from workflows.services import assert_transition, bind_workflow, pick_template_code

User = get_user_model()


class WorkflowTemplateTests(TestCase):
    def setUp(self):
        SeedCommand().handle()
        self.user = User.objects.create_user(username='wf', email='wf@example.test', password='A-long-unique-password-2026!')
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        self.patient = Patient.objects.create(first_name='W', last_name='F', phone_number='08011112222')
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(code='CBC', name='CBC', department='HAEMATOLOGY', service_group=group)
        self.tariff = Tariff.objects.create(name='Std')
        TestPrice.objects.create(test=self.test, tariff=self.tariff, price=Decimal('500'))
        self.order = LabOrder.objects.create(visit=self.visit, test=self.test, tariff=self.tariff, price=Decimal('500'))

    def test_pick_and_bind_normal(self):
        self.assertEqual(pick_template_code(self.test), 'NORMAL')
        instance = bind_workflow(self.order)
        self.assertIsNotNone(instance)
        self.assertEqual(instance.workflow.code, 'NORMAL')
        self.assertEqual(instance.current_step.code, 'ORDERED')

    def test_enforced_transition(self):
        bind_workflow(self.order)
        assert_transition(self.order, 'collect')
        self.order.workflow_instance.refresh_from_db()
        self.assertEqual(self.order.workflow_instance.current_step.code, 'COLLECTED')
        with self.assertRaises(ValidationError):
            assert_transition(self.order, 'release')
