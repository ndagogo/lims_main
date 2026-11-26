import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from core.models import Centre
from core.report_format import format_snapshot, get_report_format
from laboratory.models import ServiceGroup, Test
from radiology.models import ImagingProcedure
from workflows.models import WorkflowAssignment, WorkflowDefinition


def _png(name='header.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (12, 8), (16, 32, 51)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class CatalogWorkflowAndLetterheadTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username='workflow.admin', email='workflow.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.group = ServiceGroup.objects.create(name='Haematology', code='1', is_active=True)
        self.centre = Centre.objects.create(code='WF1', name='Workflow Centre')
        self.workflow = WorkflowDefinition.objects.create(
            code='NORMAL', name='Normal laboratory', version=1, status='PUBLISHED', is_active=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_laboratory_test_must_map_a_published_workflow(self):
        page = self.client.get('/catalog/tests/')
        self.assertContains(page, 'Normal laboratory (NORMAL)')
        missing = self.client.post('/catalog/tests/', {
            'action': 'create', 'name': 'Unmapped assay', 'department': 'PATHOLOGY',
            'department_code': '1', 'service_group': self.group.pk,
            'is_active': 'on', 'is_active_submitted': '1',
        }, follow=True)
        self.assertContains(missing, 'Choose a published workflow')
        self.assertFalse(Test.objects.filter(name='Unmapped assay').exists())

        created = self.client.post('/catalog/tests/', {
            'action': 'create', 'name': 'Full blood count', 'department': 'HAEMATOLOGY',
            'department_code': '1', 'service_group': self.group.pk,
            'default_workflow_code': 'NORMAL',
            'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created.status_code, 302)
        test = Test.objects.get(name='Full blood count')
        self.assertEqual(test.default_workflow_code, 'NORMAL')
        self.assertTrue(WorkflowAssignment.objects.filter(test=test, workflow_code='NORMAL', is_active=True).exists())

    def test_radiology_procedure_maps_a_published_workflow(self):
        page = self.client.get('/catalog/imaging/')
        self.assertContains(page, 'name="default_workflow_code"')
        created = self.client.post('/catalog/imaging/', {
            'action': 'create_procedure', 'code': 'CXR', 'name': 'Chest X-ray',
            'modality_type': 'XRAY', 'default_workflow_code': 'NORMAL',
            'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created.status_code, 302)
        procedure = ImagingProcedure.objects.get(code='CXR')
        self.assertEqual(procedure.default_workflow_code, 'NORMAL')
        self.assertTrue(
            WorkflowAssignment.objects.filter(procedure=procedure, workflow_code='NORMAL', is_active=True).exists()
        )

    def test_centre_header_upload_is_stored_for_result_preview(self):
        saved = self.client.post('/catalog/lab-result-templates/', {
            'action': 'save_report_format',
            'centre': str(self.centre.pk),
            'title_lab': 'Laboratory Report',
            'layout_style': 'CLASSIC',
            'font_family': 'Helvetica',
            'body_font_size': '10',
            'margin_mm': '18',
            'primary_color': '#102033',
            'accent_color': '#0f766e',
            'header_mode': 'COMPOSED',
            'logo_align': 'RIGHT',
            'address_align': 'LEFT',
            'contact_align': 'CENTER',
            'placement_submitted': '1',
            'show_logo': 'on',
            'show_centre_address': 'on',
            'show_centre_contact': 'on',
            'signature_label': 'Authenticated by',
            'footer_text': 'Centre letter footer line',
            'header_file': _png(),
        })
        self.assertEqual(saved.status_code, 302)
        fmt = get_report_format(self.centre)
        self.assertTrue(fmt.header_file.name.lower().endswith('.png'))
        self.assertEqual(fmt.header_mode, 'COMPOSED')
        self.assertEqual(fmt.logo_align, 'RIGHT')
        self.assertTrue(fmt.show_authenticator_signature)
        snap = format_snapshot(self.centre)
        self.assertEqual(snap['header_file'], fmt.header_file.name)
        self.assertEqual(snap['logo_align'], 'RIGHT')
        self.assertTrue(snap['show_authenticator_signature'])
        page = self.client.get(f'/catalog/lab-result-templates/?centre={self.centre.pk}')
        self.assertContains(page, 'Letter heading')
        self.assertContains(page, 'printed on every authenticated result')
        preview = self.client.get(f'/catalog/lab-result-templates/preview/?centre={self.centre.pk}')
        self.assertContains(preview, 'TEST DESCRIPTION')
        self.assertContains(preview, 'BIOLOGICAL REFERENCE INTERVAL')
        self.assertContains(preview, 'Lab Manager')
        self.assertContains(preview, 'Centre letter footer line')
        plain = self.client.get(f'/catalog/lab-result-templates/preview/?centre={self.centre.pk}&plain=1')
        self.assertContains(plain, 'TEST DESCRIPTION')
        self.assertNotContains(plain, 'Centre letter footer line')
        self.assertContains(plain, 'Lab Manager')

    def test_workflow_guide_downloads_as_pdf(self):
        response = self.client.get('/platform/workflows/guide.pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        screen = self.client.get('/platform/workflows/')
        self.assertContains(screen, 'Download guide (PDF)')
