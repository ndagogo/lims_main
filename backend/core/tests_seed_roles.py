from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase

from core.management.commands.seed_roles import PLATFORM_ROLE, ROLE_PERMISSIONS


class SeedRolesTests(TestCase):
    def test_seed_creates_every_baseline_role_and_the_full_privilege_catalogue(self):
        custom = Group.objects.create(name='Night Shift Supervisor')
        extra = Permission.objects.filter(codename='view_patient').first()
        self.assertIsNotNone(extra)
        custom.permissions.add(extra)

        call_command('seed_roles')

        self.assertEqual(Group.objects.filter(name__in=ROLE_PERMISSIONS).count(), len(ROLE_PERMISSIONS))
        platform = Group.objects.get(name=PLATFORM_ROLE)
        self.assertEqual(platform.permissions.count(), Permission.objects.count())
        self.assertGreater(platform.permissions.count(), len(ROLE_PERMISSIONS[PLATFORM_ROLE]))
        kept = Group.objects.get(name='Night Shift Supervisor')
        self.assertEqual(list(kept.permissions.values_list('codename', flat=True)), ['view_patient'])

        call_command('seed_roles')
        self.assertEqual(Group.objects.filter(name=PLATFORM_ROLE).count(), 1)
        self.assertTrue(Group.objects.filter(name='Night Shift Supervisor').exists())
