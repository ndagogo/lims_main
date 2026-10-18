from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from core.models import Centre

User = get_user_model()


class PlatformControlsAccessTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='PLT', name='Platform Centre')
        self.admin = User.objects.create_user(
            username='plat.admin', email='plat.admin@example.test',
            password='A-long-unique-password-2026!',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        self.desk = User.objects.create_user(
            username='plat.desk', email='plat.desk@example.test',
            password='A-long-unique-password-2026!',
        )
        self.desk.groups.add(Group.objects.get_or_create(name='Front Desk')[0])
        self.granted = User.objects.create_user(
            username='plat.granted', email='plat.granted@example.test',
            password='A-long-unique-password-2026!',
        )
        perm = Permission.objects.get(codename='access_platform_controls')
        self.granted.user_permissions.add(perm)

    def test_hub_restricted(self):
        self.assertEqual(self.client.get('/platform/').status_code, 302)
        self.client.login(username='plat.desk', password='A-long-unique-password-2026!')
        self.assertEqual(self.client.get('/platform/').status_code, 403)
        self.client.login(username='plat.admin', password='A-long-unique-password-2026!')
        hub = self.client.get('/platform/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Workflow templates')
        self.assertContains(hub, 'Modality workstations')
        self.assertContains(hub, '/platform/modalities/')
        self.assertContains(hub, '/platform/workflows/')
        self.assertNotContains(hub, '/admin/workflows/')
        workflows = self.client.get('/platform/workflows/')
        self.assertEqual(workflows.status_code, 200)
        self.client.login(username='plat.granted', password='A-long-unique-password-2026!')
        self.assertEqual(self.client.get('/platform/').status_code, 200)
        self.assertEqual(self.client.get('/platform/analyzers/').status_code, 200)
