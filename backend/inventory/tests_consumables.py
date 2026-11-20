from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from rest_framework.test import APIClient

from core.models import Centre, UserCentreAccess
from inventory.models import ConsumableDepartment, ReagentSku, StockMovement
from inventory.services import issue_stock, receive_stock
from patients.models import Patient, Visit
from radiology.models import ExamSupplyUse, ImagingModality, ImagingOrder, RadiologyExam

User = get_user_model()


class ConsumablesStockTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='INV', name='Inventory Centre')
        self.admin = User.objects.create_user(
            username='inv.admin', email='inv.admin@example.test', password='A-long-unique-password-2026!',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='Platform Administrator')[0])
        UserCentreAccess.objects.create(
            user=self.admin, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
        )
        self.sku = ReagentSku.objects.create(
            centre=self.centre,
            department=ConsumableDepartment.RADIOLOGY,
            sku_code='RAD-SYR',
            name='Syringe 10ml',
            description='Disposable syringe for contrast injection',
            category='Injection',
            unit='each',
            reorder_level=Decimal('5'),
        )

    def test_receive_and_issue_updates_on_hand(self):
        receive_stock(sku=self.sku, quantity=20, user=self.admin, lot_number='L1')
        self.assertEqual(self.sku.on_hand, Decimal('20'))
        issue_stock(sku=self.sku, quantity=3, user=self.admin, reference_type='test', reference_id='1')
        self.assertEqual(self.sku.on_hand, Decimal('17'))
        self.assertEqual(StockMovement.objects.filter(sku=self.sku, movement_type='ISSUE').count(), 1)

    def test_issue_blocks_when_insufficient(self):
        receive_stock(sku=self.sku, quantity=2, user=self.admin)
        with self.assertRaises(ValidationError):
            issue_stock(sku=self.sku, quantity=5, user=self.admin)

    def test_dashboard_lists_department_stock(self):
        receive_stock(sku=self.sku, quantity=10, user=self.admin)
        client = Client()
        client.force_login(self.admin)
        response = client.get('/inventory/?department=RADIOLOGY')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'RAD-SYR')
        self.assertContains(response, 'Syringe 10ml')

    def test_create_consumable_from_dashboard(self):
        client = Client()
        client.force_login(self.admin)
        response = client.post('/inventory/', {
            'action': 'create_sku',
            'centre': str(self.centre.pk),
            'department': 'LABORATORY',
            'sku_code': 'LAB-EDTA',
            'name': 'EDTA tube',
            'unit': 'tube',
            'reorder_level': '10',
            'description': 'Purple top',
            'category': 'Vacutainer',
        })
        self.assertEqual(response.status_code, 302)
        row = ReagentSku.objects.get(sku_code='LAB-EDTA')
        self.assertEqual(row.department, ConsumableDepartment.LABORATORY)
        self.assertEqual(row.description, 'Purple top')


class RadiologyConsumableDeductionTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user(
            username='rad.tech.inv', email='rad.tech.inv@example.test', password='A-long-unique-password-2026!',
        )
        self.rad = User.objects.create_user(
            username='rad.report.inv', email='rad.report.inv@example.test', password='A-long-unique-password-2026!',
        )
        self.tech.groups.add(Group.objects.get_or_create(name='Radiographer')[0])
        self.rad.groups.add(Group.objects.get_or_create(name='Radiologist')[0])
        self.centre = Centre.objects.create(code='RCI', name='Rad Consumable Centre')
        for person in (self.tech, self.rad):
            UserCentreAccess.objects.create(
                user=person, centre=self.centre, can_view=True, can_edit=True, can_approve=True,
            )
        patient = Patient.objects.create(first_name='Chi', last_name='Oka', phone_number='08000000999')
        visit = Visit.objects.create(patient=patient, centre=self.centre, registered_by=self.tech)
        modality = ImagingModality.objects.create(centre=self.centre, code='XR1', name='X-Ray', modality_type='XR')
        self.order = ImagingOrder.objects.create(visit=visit, modality=modality, requested_by=self.tech)
        self.exam = RadiologyExam.objects.create(order=self.order, status='ASSIGNED')
        self.sku = ReagentSku.objects.create(
            centre=self.centre,
            department=ConsumableDepartment.RADIOLOGY,
            sku_code='FILM-A4',
            name='Film',
            unit='sheet',
            reorder_level=Decimal('2'),
        )
        receive_stock(sku=self.sku, quantity=10, user=self.tech)
        self.client = APIClient()

    def test_end_exam_deducts_linked_consumable(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'radiologist': self.rad.pk,
            'supplies': [{'name': 'Film', 'quantity': 2, 'sku': str(self.sku.pk)}],
        }, format='json')
        self.assertEqual(ended.status_code, 200, ended.content)
        self.assertEqual(self.sku.on_hand, Decimal('8'))
        use = ExamSupplyUse.objects.get(exam=self.exam, item_name='Film')
        self.assertEqual(use.quantity, 2)
        self.assertEqual(use.sku_id, self.sku.pk)
        self.assertIsNotNone(use.stock_movement_id)
        move = StockMovement.objects.get(pk=use.stock_movement_id)
        self.assertEqual(move.movement_type, 'ISSUE')
        self.assertEqual(move.quantity, Decimal('-2'))

    def test_end_exam_rejects_insufficient_stock(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'radiologist': self.rad.pk,
            'supplies': [{'name': 'Film', 'quantity': 50, 'sku': str(self.sku.pk)}],
        }, format='json')
        self.assertEqual(ended.status_code, 400)
        body = ended.json() if hasattr(ended, 'json') else ended.data
        detail = str(body)
        self.assertIn('insufficient', detail.lower())
        self.assertIn('restock', detail.lower())
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'IN_PROGRESS')
        self.assertEqual(self.sku.on_hand, Decimal('10'))

    def test_end_exam_allows_using_exact_on_hand(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'radiologist': self.rad.pk,
            'supplies': [{'name': 'Film', 'quantity': 10, 'sku': str(self.sku.pk)}],
        }, format='json')
        self.assertEqual(ended.status_code, 200, getattr(ended, 'content', ended.data))
        self.assertEqual(self.sku.on_hand, Decimal('0'))

    def test_end_exam_rejects_free_text_without_inventory_sku(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'radiologist': self.rad.pk,
            'supplies': [{'name': 'Mystery syringe', 'quantity': 1}],
        }, format='json')
        self.assertEqual(ended.status_code, 400)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'IN_PROGRESS')

    def test_end_exam_requires_items_used_before_radiologist(self):
        self.client.force_authenticate(self.tech)
        self.client.post(f'/api/radiology-exams/{self.exam.pk}/start/', {}, format='json')
        ended = self.client.post(f'/api/radiology-exams/{self.exam.pk}/end/', {
            'radiologist': self.rad.pk,
            'supplies': [{'name': 'Film', 'quantity': 0, 'sku': str(self.sku.pk)}],
        }, format='json')
        self.assertEqual(ended.status_code, 400)
        detail = str(ended.data).lower()
        self.assertIn('items used', detail)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, 'IN_PROGRESS')
