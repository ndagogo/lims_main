from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from laboratory.models import ContainerType, ServiceGroup, SpecimenType, Test


class CollectionCatalogTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username='collection.admin', email='collection.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.group = ServiceGroup.objects.create(name='Haematology', code='1', is_active=True)
        self.client = Client()
        self.client.force_login(self.admin)

    def test_seeded_types_are_dropdowns_and_can_be_managed(self):
        page = self.client.get('/catalog/tests/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Specimen types')
        self.assertContains(page, 'Container types')
        self.assertContains(page, 'Serum')
        self.assertContains(page, 'EDTA (lavender)')
        self.assertContains(page, 'name="specimen_type"')
        self.assertContains(page, 'name="container_types"')
        self.assertNotContains(page, 'placeholder="EDTA whole blood"')
        self.assertNotContains(page, 'placeholder="Lavender EDTA"')

        created = self.client.post('/catalog/tests/', {
            'action': 'create_specimen', 'name': 'Synovial fluid', 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(created.status_code, 302)
        specimen = SpecimenType.objects.get(name='Synovial fluid')
        renamed = self.client.post('/catalog/tests/', {
            'action': 'update_specimen', 'specimen': specimen.pk, 'name': 'Joint fluid',
            'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(renamed.status_code, 302)
        specimen.refresh_from_db()
        self.assertEqual(specimen.name, 'Joint fluid')

        serum = SpecimenType.objects.get(name='Serum')
        gold = ContainerType.objects.get(name='Serum separator (gold)')
        edta = ContainerType.objects.get(name='EDTA (lavender)')
        saved = self.client.post('/catalog/tests/', {
            'action': 'create', 'code': 'GLU', 'name': 'Glucose', 'department': 'PATHOLOGY',
            'department_code': '1', 'service_group': self.group.pk, 'specimen_type': serum.pk,
            'container_types': [gold.pk, edta.pk], 'is_active': 'on', 'is_active_submitted': '1',
        })
        self.assertEqual(saved.status_code, 302)
        test = Test.objects.get(code='GLU')
        self.assertEqual(test.specimen_type, serum)
        self.assertEqual(test.specimen, 'Serum')
        self.assertEqual(set(test.container_types.values_list('name', flat=True)), {'Serum separator (gold)', 'EDTA (lavender)'})
        self.assertIn('EDTA (lavender)', test.container)
        self.assertIn('Serum separator (gold)', test.container)

        blocked = self.client.post('/catalog/tests/', {'action': 'delete_specimen', 'specimen': serum.pk})
        self.assertEqual(blocked.status_code, 302)
        self.assertTrue(SpecimenType.objects.filter(pk=serum.pk).exists())

        removed = self.client.post('/catalog/tests/', {'action': 'delete_specimen', 'specimen': specimen.pk})
        self.assertEqual(removed.status_code, 302)
        self.assertFalse(SpecimenType.objects.filter(pk=specimen.pk).exists())
