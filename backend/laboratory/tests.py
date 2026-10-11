from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient
from core.models import Centre, UserCentreAccess, SequenceCounter
from patients.models import Patient, Visit
from .models import ServiceGroup, Test, Tariff, LabOrder, Sample, SampleCodeConfig, SampleActivity
User=get_user_model()

class SampleIdentityTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='labstaff',email='lab@example.test',password='Strong-test-password-2026!')
        self.centre=Centre.objects.create(code='NWS',name='NOWAS')
        UserCentreAccess.objects.create(user=self.user,centre=self.centre,can_view=True,can_register=True,can_edit=True,can_approve=True)
        SampleCodeConfig.objects.create(centre=self.centre,numeric_code='2')
        self.patient=Patient.objects.create(first_name='Test',last_name='Patient',phone_number='08000000000')
        self.visit=Visit.objects.create(patient=self.patient,centre=self.centre,registered_by=self.user)
        group=ServiceGroup.objects.create(name='Haematology',code='1')
        self.test=Test.objects.create(code='CBC',name='Complete Blood Count',department='PATHOLOGY',department_code='1',service_group=group)
        self.tariff=Tariff.objects.create(name='Standard')
        self.order=LabOrder.objects.create(visit=self.visit,test=self.test,tariff=self.tariff,price='5000')
    def test_sample_id_is_eight_characters_and_encodes_centre_department_group(self):
        sample=Sample.objects.create(order=self.order,centre=self.centre)
        self.assertEqual(len(sample.sample_id),8)
        self.assertTrue(sample.sample_id.startswith('211'))
        self.assertEqual(sample.status,'PENDING_COLLECTION')
    def test_sample_sequence_increments(self):
        first=Sample.objects.create(order=self.order,centre=self.centre)
        second=Sample.objects.create(order=self.order,centre=self.centre)
        self.assertEqual(int(second.sample_id[-5:]),int(first.sample_id[-5:])+1)
    def test_duplicate_sample_ids_rejected_by_database(self):
        first=Sample.objects.create(order=self.order,centre=self.centre)
        other_order=LabOrder.objects.create(visit=self.visit,test=self.test,tariff=self.tariff,price='5000')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sample.objects.create(sample_id=first.sample_id,order=other_order,centre=self.centre)

class SampleCentreIsolationTests(TestCase):
    def test_sample_list_is_centre_scoped(self):
        user=User.objects.create_user(username='centreuser',email='centre@example.test',password='Strong-test-password-2026!')
        centre_a=Centre.objects.create(code='NWS',name='NOWAS')
        centre_b=Centre.objects.create(code='IND',name='Independence')
        UserCentreAccess.objects.create(user=user,centre=centre_a,can_view=True)
        patient=Patient.objects.create(first_name='A',last_name='Patient',phone_number='08000000001')
        visit_a=Visit.objects.create(patient=patient,centre=centre_a,registered_by=user)
        visit_b=Visit.objects.create(patient=patient,centre=centre_b,registered_by=user)
        test=Test.objects.create(code='TEST1',name='Test 1')
        tariff=Tariff.objects.create(name='T1')
        order_a=LabOrder.objects.create(visit=visit_a,test=test,tariff=tariff,price='10')
        order_b=LabOrder.objects.create(visit=visit_b,test=test,tariff=tariff,price='10')
        SampleCodeConfig.objects.create(centre=centre_a,numeric_code='1')
        SampleCodeConfig.objects.create(centre=centre_b,numeric_code='2')
        Sample.objects.create(order=order_a,centre=centre_a)
        Sample.objects.create(order=order_b,centre=centre_b)
        client=APIClient(); client.force_authenticate(user)
        response=client.get('/api/samples/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['count'],1)

class SampleBatchCreateTests(TestCase):
    def test_batch_can_be_created_and_dispatched(self):
        from django.contrib.auth.models import Group
        user=User.objects.create_user(username='batchstaff', email='batch@example.test', password='Strong-test-password-2026!')
        user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        centre=Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=user, centre=centre, can_view=True, can_edit=True)
        SampleCodeConfig.objects.create(centre=centre, numeric_code='2')
        patient=Patient.objects.create(first_name='Batch', last_name='Patient', phone_number='08000000009')
        visit=Visit.objects.create(patient=patient, centre=centre, registered_by=user)
        test=Test.objects.create(code='CBC', name='Complete Blood Count', department='HAEMATOLOGY', department_code='1', service_group_code='1')
        order=LabOrder.objects.create(visit=visit, test=test, price='5500')
        sample=Sample.objects.create(order=order, centre=centre, status='COLLECTED')
        client=APIClient(); client.force_authenticate(user)
        created=client.post('/api/sample-batches/', {'centre': str(centre.pk), 'department': 'HAEMATOLOGY', 'sample_ids': [sample.sample_id]}, format='json')
        self.assertEqual(created.status_code, 201)
        sent=client.post(f"/api/sample-batches/{created.data['id']}/dispatch/", {}, format='json')
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.data['status'], 'DISPATCHED')


class ResultControlTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        from django.core.files.base import ContentFile
        from patients.models import PatientDocument
        from .models import LabResult
        self.scientist=User.objects.create_user(username='scientist1',email='scientist1@example.test',password='A-long-unique-password-2026!')
        self.manager=User.objects.create_user(username='manager1',email='manager1@example.test',password='A-long-unique-password-2026!')
        self.authenticator=User.objects.create_user(username='auth1',email='auth1@example.test',password='A-long-unique-password-2026!')
        self.scientist.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        self.manager.groups.add(Group.objects.get_or_create(name='Lab Manager')[0])
        self.centre=Centre.objects.create(code='NWS',name='NOWAS')
        for person in (self.scientist, self.manager):
            UserCentreAccess.objects.create(user=person, centre=self.centre, can_view=True, can_edit=True, can_approve=True)
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='2')
        patient=Patient.objects.create(first_name='Ada', last_name='Okafor', phone_number='08011112222')
        visit=Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.scientist)
        PatientDocument.objects.create(visit=visit, document_type='REFERRAL', file=ContentFile(b'%PDF-1.4\n', name='referral.pdf'), uploaded_by=self.scientist)
        test=Test.objects.create(code='CBC', name='Complete Blood Count', department='PATHOLOGY', department_code='1', service_group_code='1')
        order=LabOrder.objects.create(visit=visit, test=test, price='5500')
        self.sample=Sample.objects.create(order=order, centre=self.centre, status='AUTHENTICATED')
        self.result=LabResult.objects.create(sample=self.sample, status='AUTHENTICATED', entered_by=self.scientist, reviewed_by=self.scientist, authenticated_by=self.authenticator, narrative='Within range')
        self.client=APIClient()

    def test_scientist_cannot_release_and_manager_can(self):
        from communications.models import NotificationOutbox
        self.client.force_authenticate(self.scientist)
        refused=self.client.post(f'/api/lab-results/{self.result.pk}/release/', {}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.client.force_authenticate(self.manager)
        released=self.client.post(f'/api/lab-results/{self.result.pk}/release/', {}, format='json')
        self.assertEqual(released.status_code, 200)
        self.sample.refresh_from_db(); self.result.refresh_from_db()
        self.assertEqual(self.sample.status, 'RELEASED')
        self.assertTrue(NotificationOutbox.objects.filter(subject=f'result-ready:{self.result.pk}').exists())
        amended=self.client.post(f'/api/lab-results/{self.result.pk}/amend/', {'reason': 'Corrected a transcription'}, format='json')
        self.assertEqual(amended.status_code, 200)
        self.result.refresh_from_db(); self.sample.refresh_from_db()
        self.assertEqual(self.result.status, 'AMENDED')
        self.assertEqual(self.result.version, 2)
        self.assertEqual(self.sample.status, 'RELEASED')
        self.assertEqual(self.result.reviews.filter(action='AMENDED').count(), 1)
