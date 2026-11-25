from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from rest_framework.test import APIClient

from core.models import Centre, UserCentreAccess
from laboratory.models import BatchSample, LabOrder, Sample, SampleBatch, SampleCodeConfig, ServiceGroup, Test
from patients.models import Patient, Visit

User = get_user_model()


class BatchReceivingDeskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='recv.staff', email='recv@example.test', password='Strong-test-password-2026!',
        )
        self.user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        self.centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=self.user, centre=self.centre, can_view=True, can_edit=True)
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        patient = Patient.objects.create(
            first_name='Lisa', last_name='Aneke', phone_number='08033334444', sex='Female',
        )
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.user)
        group = ServiceGroup.objects.create(name='Immunology', code='3')
        self.test = Test.objects.create(
            code='AFG', name='Aspergillus IgG', department='IMMUNOLOGY', department_code='3',
            service_group=group, specimen='Serum', container='Yellow Gel Tube',
        )
        order_a = LabOrder.objects.create(visit=visit, test=self.test, price='8000')
        order_b = LabOrder.objects.create(visit=visit, test=self.test, price='8000')
        self.sample_a = Sample.objects.create(order=order_a, centre=self.centre, status='COLLECTED', specimen_type='Serum')
        self.sample_b = Sample.objects.create(order=order_b, centre=self.centre, status='COLLECTED', specimen_type='Serum')
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        created = self.api.post(
            '/api/sample-batches/',
            {
                'centre': str(self.centre.pk),
                'department': 'IMMUNOLOGY',
                'sample_ids': [self.sample_a.sample_id, self.sample_b.sample_id],
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.batch_id = created.data['id']
        sent = self.api.post(f'/api/sample-batches/{self.batch_id}/dispatch/', {}, format='json')
        self.assertEqual(sent.status_code, 200)
        self.screen = Client()
        self.screen.force_login(self.user)

    def test_receive_desk_renders_and_saves_accept_with_temperature(self):
        page = self.screen.get(f'/laboratory/?stage=receive&batch={self.batch_id}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Batch Receiving')
        self.assertContains(page, self.sample_a.sample_id)
        self.assertContains(page, 'Yellow Gel Tube')
        self.assertContains(page, 'Pending vacutainer')

        save = self.screen.post('/laboratory/', {
            'action': 'receive_desk',
            'stage': 'receive',
            'batch': self.batch_id,
            'accept': [self.sample_a.sample_id, self.sample_b.sample_id],
            'receive_temperature': '4.5',
            'receive_comments': 'Cool box intact',
            'remarks-' + self.sample_a.sample_id: 'OK',
        })
        self.assertEqual(save.status_code, 302)
        batch = SampleBatch.objects.get(pk=self.batch_id)
        self.assertEqual(batch.status, 'RECEIVED')
        self.assertEqual(batch.receive_temperature, Decimal('4.5'))
        self.assertEqual(batch.receive_comments, 'Cool box intact')
        self.sample_a.refresh_from_db()
        self.assertEqual(self.sample_a.status, 'RECEIVED')
        line = BatchSample.objects.get(batch=batch, sample=self.sample_a)
        self.assertEqual(line.receive_disposition, 'ACCEPTED')

    def test_receive_desk_can_mark_missing(self):
        save = self.screen.post('/laboratory/', {
            'action': 'receive_desk',
            'stage': 'receive',
            'batch': self.batch_id,
            'accept': [self.sample_a.sample_id],
            'missing': [self.sample_b.sample_id],
            'remarks-' + self.sample_b.sample_id: 'Tube not in cool box',
        })
        self.assertEqual(save.status_code, 302)
        self.sample_a.refresh_from_db()
        self.sample_b.refresh_from_db()
        self.assertEqual(self.sample_a.status, 'RECEIVED')
        self.assertEqual(self.sample_b.status, 'BATCHED')
        missing_line = BatchSample.objects.get(batch_id=self.batch_id, sample=self.sample_b)
        self.assertEqual(missing_line.receive_disposition, 'MISSING')
        self.assertIn('cool box', missing_line.remarks.lower())
