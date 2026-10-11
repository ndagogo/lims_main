from django.test import SimpleTestCase, TestCase
from .services import parse_hl7_obx, redact_payload_for_logs

class AnalyzerParserTests(SimpleTestCase):
    def test_extracts_obx_observation(self):
        payload = "MSH|^~\\&|ANALYZER\rOBX|1|NM|WBC^White cells||7.2|10^9/L|4.0-11.0|N"
        got = parse_hl7_obx(payload)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['instrument_test_code'], 'WBC')
        self.assertEqual(got[0]['value'], '7.2')

    def test_empty_payload_has_no_observations(self):
        self.assertEqual(parse_hl7_obx('MSH|header'), [])

    def test_pid_is_redacted(self):
        self.assertIn('PID|[REDACTED]', redact_payload_for_logs('PID|1||12345||Patient'))

class AnalyzerDraftApplyTests(TestCase):
    def test_mapped_message_becomes_a_draft_and_is_not_released(self):
        from django.utils import timezone
        from rest_framework.test import APIClient
        from django.contrib.auth import get_user_model
        from core.models import Centre, UserCentreAccess
        from patients.models import Patient, Visit
        from laboratory.models import LabOrder, Sample, SampleCodeConfig, SampleProcessing, Test, TestParameter
        from .models import Analyzer, AnalyzerMessage, AnalyzerTestMapping
        from django.contrib.auth.models import Group
        user = get_user_model().objects.create_user(username='gateway1', email='gateway1@example.test', password='A-long-unique-password-2026!')
        user.groups.add(Group.objects.get_or_create(name='Laboratory Scientist')[0])
        centre = Centre.objects.create(code='NWS', name='NOWAS')
        UserCentreAccess.objects.create(user=user, centre=centre, can_view=True, can_edit=True)
        SampleCodeConfig.objects.create(centre=centre, numeric_code='3')
        patient = Patient.objects.create(first_name='Kay', last_name='Ade', phone_number='08022223333')
        visit = Visit.objects.create(patient=patient, centre=centre, registered_by=user)
        test = Test.objects.create(code='WBC', name='White cells', department='PATHOLOGY', department_code='1', service_group_code='1')
        parameter = TestParameter.objects.create(test=test, code='WBC', name='WBC', is_required=True)
        order = LabOrder.objects.create(visit=visit, test=test, price='1000')
        sample = Sample.objects.create(order=order, centre=centre, status='PROCESSING')
        SampleProcessing.objects.create(sample=sample, action='PROCESSING_STARTED', performed_by=user, completed_at=timezone.now())
        analyzer = Analyzer.objects.create(centre=centre, name='Haematology 1', protocol='HL7')
        AnalyzerTestMapping.objects.create(analyzer=analyzer, instrument_test_code='WBC', test=test, parameter=parameter)
        payload = f'OBX|1|NM|WBC^White cells||7.2|10^9/L||||{sample.sample_id}'
        message = AnalyzerMessage.objects.create(analyzer=analyzer, raw_payload=payload, parsed_payload={'observations': parse_hl7_obx(payload)}, status='PARSED')
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(f'/api/analyzer-messages/{message.pk}/apply-to-draft/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        message.refresh_from_db(); sample.refresh_from_db()
        self.assertEqual(message.status, 'ACCEPTED')
        self.assertEqual(message.sample_id, sample.pk)
        self.assertEqual(sample.status, 'RESULT_ENTERED')
        self.assertEqual(sample.result.status, 'DRAFT')
        self.assertEqual(sample.result.values.get().value, '7.2')
        again = client.post(f'/api/analyzer-messages/{message.pk}/apply-to-draft/', {}, format='json')
        self.assertEqual(again.status_code, 409)
