from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Centre
from laboratory.models import ResultTemplate, ServiceGroup, Test, TestParameter, UnitOfMeasure
from laboratory.parameter_codes import allocate_parameter_code

User = get_user_model()


class ParameterAndFormatSetupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='param.admin', email='param.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.centre = Centre.objects.create(code='PRM', name='Parameter Centre')
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(name='Complete Blood Count', service_group=group, department='PATHOLOGY')
        self.client.force_login(self.admin)

    def test_units_are_seeded_and_parameter_code_uses_the_test_name(self):
        self.assertTrue(UnitOfMeasure.objects.filter(symbol='g/dL').exists())
        code = allocate_parameter_code(self.test, when=timezone.datetime(2026, 11, 25).date())
        self.assertTrue(code.startswith('LPCO251126'))

    def test_parameter_page_creates_a_code_and_format_maps_the_test(self):
        unit = UnitOfMeasure.objects.get(symbol='g/dL')
        self.assertEqual(self.client.get('/catalog/formats/').status_code, 200)
        page = self.client.get('/catalog/parameters/')
        self.assertContains(page, 'Test parameters')
        self.assertContains(page, 'g/dL')
        saved = self.client.post('/catalog/parameters/', {
            'action': 'save_parameter',
            'test': self.test.pk,
            'param_name': 'Haemoglobin',
            'result_type': 'NUMBER',
            'unit_of_measure': unit.pk,
            'decimal_places': '1',
            'display_order': '1',
            'text_size': 'SAME',
            'result_position': 'BENEATH',
            'is_required': 'on',
            'show_in_report': 'on',
            'low': '12',
            'high': '16',
        })
        self.assertEqual(saved.status_code, 302)
        param = TestParameter.objects.get(test=self.test, name='Haemoglobin')
        self.assertTrue(param.code.startswith('LPCO'))
        self.assertEqual(param.unit, 'g/dL')
        tests_page = self.client.get(f'/catalog/tests/?test={self.test.pk}')
        self.assertNotContains(tests_page, 'Parameters (structured result fields)')
        self.assertContains(tests_page, 'Test parameters')

        fmt = self.client.post('/catalog/formats/', {
            'action': 'save_format',
            'test': self.test.pk,
            'report_title': 'Complete blood count',
            'column_caption_1': 'Test',
            'column_caption_2': 'Result',
            'column_caption_3': 'Units',
            'column_caption_4': 'Biological Reference Interval',
            'add_parameter': param.pk,
            'add_sequence': '1',
            'is_default_male': 'on',
            'is_default_female': 'on',
        })
        self.assertEqual(fmt.status_code, 302)
        self.test.refresh_from_db()
        self.assertIsNotNone(self.test.default_template_id)
        template = ResultTemplate.objects.get(pk=self.test.default_template_id)
        self.assertTrue(template.format_code.startswith('FMT'))
        self.assertEqual(template.fields.get().parameter_id, param.pk)
        desk = self.client.get(f'/catalog/formats/?test={self.test.pk}')
        self.assertContains(desk, template.format_code)
        self.assertContains(desk, 'Haemoglobin')
