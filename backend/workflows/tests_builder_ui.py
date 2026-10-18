from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from laboratory.models import ServiceGroup, Test
from workflows.models import WorkflowAssignment, WorkflowDefinition, WorkflowStep, WorkflowTransition
from workflows.services import clone_workflow_version, publish_workflow

User = get_user_model()


class WorkflowBuilderUITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='wf.builder', email='wf.builder@example.test',
            password='A-long-unique-password-2026!', is_superuser=True,
        )
        Group.objects.get_or_create(name='Platform Administrator')
        self.user.groups.add(Group.objects.get(name='Platform Administrator'))
        self.client.login(username='wf.builder', password='A-long-unique-password-2026!')
        self.wf = WorkflowDefinition.objects.create(
            code='BUILDER_TEST', name='Builder test', version=1, status='DRAFT', is_active=False,
        )
        self.s1 = WorkflowStep.objects.create(workflow=self.wf, code='A', name='Start', sort_order=10)
        self.s2 = WorkflowStep.objects.create(workflow=self.wf, code='B', name='End', sort_order=20)
        WorkflowTransition.objects.create(
            workflow=self.wf, from_step=self.s1, to_step=self.s2, action='advance', label='Advance',
        )

    def test_builder_page_and_actions(self):
        page = self.client.get(f'/platform/workflows/?workflow={self.wf.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Workflow builder')
        self.assertContains(page, 'Drag to reorder')
        self.assertContains(page, 'Publish / activate')
        self.assertContains(page, 'Batching')
        self.assertContains(page, 'data-code="BATCHED"')

        reorder = self.client.post('/platform/workflows/', {
            'action': 'reorder_steps', 'workflow': str(self.wf.pk),
            'step_order': [str(self.s2.pk), str(self.s1.pk)],
        })
        self.assertEqual(reorder.status_code, 302)
        self.s1.refresh_from_db()
        self.s2.refresh_from_db()
        self.assertLess(self.s2.sort_order, self.s1.sort_order)

        update = self.client.post('/platform/workflows/', {
            'action': 'update_step', 'step': str(self.s1.pk), 'workflow': str(self.wf.pk),
            'step_name': 'Start configured', 'department': 'Pathology',
            'allowed_roles': ['Lab Manager'], 'required_fields': ['collector'],
            'required_documents': ['REGISTRATION'], 'expected_tat_minutes': '45',
            'escalation_after_minutes': '90', 'escalation_roles': ['Platform Administrator'],
            'required_submitted': '1', 'required': 'on', 'instructions': 'Do the thing',
        })
        self.assertEqual(update.status_code, 302)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.department, 'Pathology')
        self.assertIn('Lab Manager', self.s1.allowed_roles)
        self.assertEqual(self.s1.expected_tat_minutes, 45)

        group = ServiceGroup.objects.create(name='Builder Group', code='7')
        assign = self.client.post('/platform/workflows/', {
            'action': 'add_assignment', 'workflow': str(self.wf.pk),
            'workflow_code': 'BUILDER_TEST', 'service_group': str(group.pk), 'priority': '10',
        })
        self.assertEqual(assign.status_code, 302)
        self.assertTrue(WorkflowAssignment.objects.filter(workflow_code='BUILDER_TEST').exists())

        publish = self.client.post('/platform/workflows/', {
            'action': 'publish', 'workflow': str(self.wf.pk), 'notes': 'UI test publish',
        })
        self.assertEqual(publish.status_code, 302)
        self.wf.refresh_from_db()
        self.assertEqual(self.wf.status, 'PUBLISHED')
        self.assertTrue(self.wf.is_active)

        clone = self.client.post('/platform/workflows/', {
            'action': 'clone_version', 'workflow': str(self.wf.pk),
        })
        self.assertEqual(clone.status_code, 302)
        draft = WorkflowDefinition.objects.filter(code='BUILDER_TEST', status='DRAFT').order_by('-version').first()
        self.assertIsNotNone(draft)
        self.assertEqual(draft.version, 2)
