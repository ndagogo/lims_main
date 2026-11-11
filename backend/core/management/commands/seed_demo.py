"""Small client-demo catalogue and staff (≤5 per type). Safe to clear later."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.demo_seed import (
    DEMO_CENTRE_CODES,
    DEMO_DOCTOR_CODES,
    DEMO_EMAIL_DOMAIN,
    DEMO_HEALTH_CARD,
    DEMO_IMAGING_CODES,
    DEMO_PASSWORD,
    DEMO_SERVICE_GROUP_CODE,
    DEMO_SERVICE_GROUP_NAME,
    DEMO_TAG,
    DEMO_TARIFF_NAMES,
    DEMO_TEST_CODES,
    DEMO_USERNAMES,
)
from core.models import Centre, UserCentreAccess
from laboratory.models import SampleCodeConfig, ServiceGroup, Tariff, Test, TestPrice
from patients.models import OrganisationClient, Patient, Visit
from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure
from referrals.models import ReferralDoctor, ReferralInstitution


class Command(BaseCommand):
    help = (
        'Seed a tiny client-demo dataset (≤5 of each kind). '
        'Requires --confirm when DJANGO_DEBUG is False. Remove later with: python manage.py clear_demo --confirm'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required outside local DEBUG. Confirms you intend to write demo rows on this database.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['confirm']:
            raise CommandError(
                'Production/demo seed refused. Re-run with --confirm, e.g.\n'
                '  python manage.py seed_demo --confirm'
            )

        call_command('seed_roles')
        try:
            call_command('seed_notification_templates')
        except Exception as exc:  # noqa: BLE001 — demo bootstrap should continue
            self.stdout.write(self.style.WARNING(f'Notification templates skipped: {exc}'))
        try:
            call_command('seed_workflows')
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f'Workflows skipped: {exc}'))

        with transaction.atomic():
            centres = self._centres()
            tariffs = self._tariffs()
            group = self._service_group()
            tests = self._tests(group, tariffs)
            procedures = self._imaging(tariffs, centres)
            doctors = self._doctors(centres[0])
            clients = self._org_clients(centres[0])
            users = self._staff(centres[0])
            patients = self._patients(centres[0], users['demo.desk'])

        self.stdout.write(self.style.SUCCESS('Demo seed complete (≤5 per type).'))
        self.stdout.write(f'Centres: {", ".join(c.code for c in centres)}')
        self.stdout.write(f'Tests: {len(tests)} · Imaging: {len(procedures)} · Doctors: {len(doctors)} · Clients: {len(clients)}')
        self.stdout.write(f'Patients/visits: {len(patients)}')
        self.stdout.write('Staff (password for all): ' + DEMO_PASSWORD)
        self.stdout.write('  ' + ', '.join(DEMO_USERNAMES))
        self.stdout.write(self.style.WARNING('Remove later: python manage.py clear_demo --confirm'))

    def _centres(self):
        rows = []
        for code, name, digit in (
            (DEMO_CENTRE_CODES[0], f'{DEMO_TAG} Victoriaugu Hub', '8'),
            (DEMO_CENTRE_CODES[1], f'{DEMO_TAG} Independence Annex', '9'),
        ):
            centre, _ = Centre.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'address': f'{DEMO_TAG} Temporary client-demo centre — delete with clear_demo',
                    'phone': '08000000001' if code == DEMO_CENTRE_CODES[0] else '08000000002',
                    'email': f'{code.lower()}@{DEMO_EMAIL_DOMAIN}',
                    'is_active': True,
                },
            )
            SampleCodeConfig.objects.get_or_create(centre=centre, defaults={'numeric_code': digit})
            rows.append(centre)
        return rows

    def _tariffs(self):
        tariffs = {}
        for name in DEMO_TARIFF_NAMES:
            tariffs[name], _ = Tariff.objects.get_or_create(name=name, defaults={'is_active': True})
        return tariffs

    def _service_group(self):
        group, _ = ServiceGroup.objects.get_or_create(
            name=DEMO_SERVICE_GROUP_NAME,
            code=DEMO_SERVICE_GROUP_CODE,
            defaults={'is_active': True},
        )
        return group

    def _tests(self, group, tariffs):
        catalog = [
            (DEMO_TEST_CODES[0], 'Demo Complete Blood Count', 'HAEMATOLOGY', '1', 'EDTA', Decimal('5500.00')),
            (DEMO_TEST_CODES[1], 'Demo Malaria Parasite', 'PARASITOLOGY', '2', 'EDTA', Decimal('2500.00')),
            (DEMO_TEST_CODES[2], 'Demo Fasting Blood Sugar', 'CHEMISTRY', '3', 'Fluoride', Decimal('3000.00')),
            (DEMO_TEST_CODES[3], 'Demo Liver Function Test', 'CHEMISTRY', '3', 'Serum', Decimal('8500.00')),
            (DEMO_TEST_CODES[4], 'Demo Urinalysis', 'CLINICAL', '5', 'Urine', Decimal('2000.00')),
        ]
        factors = {DEMO_TARIFF_NAMES[0]: Decimal('1.00'), DEMO_TARIFF_NAMES[1]: Decimal('0.80')}
        created = []
        for code, name, department, dept_code, specimen, base in catalog:
            row, _ = Test.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'department': department,
                    'department_code': dept_code,
                    'service_group': group,
                    'service_group_code': group.code,
                    'specimen': specimen,
                    'collection_requirements': DEMO_TAG,
                    'is_active': True,
                },
            )
            for tariff_name, factor in factors.items():
                TestPrice.objects.get_or_create(
                    tariff=tariffs[tariff_name],
                    test=row,
                    defaults={'price': (base * factor).quantize(Decimal('0.01'))},
                )
            created.append(row)
        return created

    def _imaging(self, tariffs, centres):
        catalog = [
            (DEMO_IMAGING_CODES[0], 'Demo Chest X-ray', 'XRAY', Decimal('15000.00')),
            (DEMO_IMAGING_CODES[1], 'Demo Abdominopelvic Ultrasound', 'US', Decimal('18000.00')),
            (DEMO_IMAGING_CODES[2], 'Demo Resting ECG', 'ECG', Decimal('8000.00')),
        ]
        factors = {DEMO_TARIFF_NAMES[0]: Decimal('1.00'), DEMO_TARIFF_NAMES[1]: Decimal('0.80')}
        created = []
        for code, name, modality_type, base in catalog:
            row, _ = ImagingProcedure.objects.get_or_create(
                code=code,
                defaults={'name': name, 'modality_type': modality_type},
            )
            for tariff_name, factor in factors.items():
                ImagingPrice.objects.get_or_create(
                    tariff=tariffs[tariff_name],
                    procedure=row,
                    defaults={'price': (base * factor).quantize(Decimal('0.01'))},
                )
            created.append(row)
        for centre in centres:
            ImagingModality.objects.get_or_create(
                centre=centre, code='DEMO-XR1',
                defaults={'name': f'{DEMO_TAG} X-ray Room', 'modality_type': 'XRAY'},
            )
            ImagingModality.objects.get_or_create(
                centre=centre, code='DEMO-US1',
                defaults={'name': f'{DEMO_TAG} Ultrasound Room', 'modality_type': 'US'},
            )
        return created

    def _doctors(self, centre):
        roster = [
            (DEMO_DOCTOR_CODES[0], 'Chioma', 'Adeyemi', '08031110001', 'Demo Teaching Hospital'),
            (DEMO_DOCTOR_CODES[1], 'Emeka', 'Obi', '08031110002', 'Demo Specialist Clinic'),
            (DEMO_DOCTOR_CODES[2], 'Amina', 'Yusuf', '08031110003', 'Demo Medical Centre'),
        ]
        created = []
        for code, first, last, phone, place in roster:
            doctor, _ = ReferralDoctor.objects.get_or_create(
                doctor_code=code,
                defaults={
                    'centre': centre,
                    'title': 'Dr',
                    'first_name': first,
                    'last_name': last,
                    'phone': phone,
                    'email': f'{code.lower()}@{DEMO_EMAIL_DOMAIN}',
                    'commission_type': 'PERCENT',
                    'commission_value': Decimal('0'),
                    'notes': DEMO_TAG,
                    'is_active': True,
                },
            )
            doctor.assign_centres([centre])
            ReferralInstitution.objects.get_or_create(doctor=doctor, name=place)
            created.append(doctor)
        return created

    def _org_clients(self, centre):
        rows = [
            ('HMO', f'{DEMO_TAG} Hygeia Demo'),
            ('HMO', f'{DEMO_TAG} Avon Demo'),
            ('CORPORATE', f'{DEMO_TAG} Access Bank Demo'),
        ]
        created = []
        for kind, name in rows:
            client, _ = OrganisationClient.objects.get_or_create(
                centre=centre,
                kind=kind,
                name=name,
                defaults={'code': name.replace(' ', '-')[:40], 'is_active': True},
            )
            created.append(client)
        return created

    def _staff(self, centre):
        User = get_user_model()
        roster = [
            ('demo.admin', 'Platform Administrator', True, True, True, True, True),
            ('demo.desk', 'Front Desk', False, True, True, True, False),
            ('demo.accounts', 'Accounts', False, True, True, True, True),
            ('demo.lab', 'Lab Manager', False, True, False, True, True),
            ('demo.radio', 'Radiologist', False, True, False, True, True),
        ]
        users = {}
        for username, role, is_super, can_view, can_register, can_edit, can_approve in roster:
            email = f'{username}@{DEMO_EMAIL_DOMAIN}'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'display_name': f'{DEMO_TAG} {username}',
                    'first_name': 'Demo',
                    'last_name': username.split('.')[-1].title(),
                    'is_staff': is_super or role == 'Platform Administrator',
                    'is_superuser': is_super,
                    'must_change_password': False,
                },
            )
            if created or not user.has_usable_password():
                user.set_password(DEMO_PASSWORD)
            user.email = email
            user.display_name = f'{DEMO_TAG} {username}'
            user.is_active = True
            user.must_change_password = False
            if is_super:
                user.is_staff = True
                user.is_superuser = True
            user.save()
            group = Group.objects.filter(name=role).first()
            if group:
                user.groups.add(group)
            UserCentreAccess.objects.update_or_create(
                user=user,
                centre=centre,
                defaults={
                    'can_view': can_view,
                    'can_register': can_register,
                    'can_edit': can_edit,
                    'can_approve': can_approve,
                    'is_default': True,
                },
            )
            users[username] = user
        assert set(users) == set(DEMO_USERNAMES)
        return users

    def _patients(self, centre, registrar):
        people = [
            ('Ada', 'Okeke', 'Female', '08032220001'),
            ('Chinedu', 'Eze', 'Male', '08032220002'),
            ('Fatima', 'Bello', 'Female', '08032220003'),
            ('Ibrahim', 'Musa', 'Male', '08032220004'),
            ('Ngozi', 'Okafor', 'Female', '08032220005'),
        ]
        created = []
        from datetime import date
        for index, (first, last, sex, phone) in enumerate(people, start=1):
            patient = Patient.objects.filter(phone_number=phone, health_card_number=DEMO_HEALTH_CARD).first()
            if patient is None:
                patient = Patient.objects.create(
                    first_name=first,
                    last_name=last,
                    phone_number=phone,
                    email=f'{first.lower()}.{last.lower()}@{DEMO_EMAIL_DOMAIN}',
                    sex=sex,
                    date_of_birth=date(1985 + index, 3, 15),
                    address=f'{DEMO_TAG} Demo street, Enugu',
                    city='Enugu',
                    state='Enugu',
                    health_card_number=DEMO_HEALTH_CARD,
                )
            if not patient.visits.filter(centre=centre, comments=DEMO_TAG).exists():
                Visit.objects.create(
                    patient=patient,
                    centre=centre,
                    registered_by=registrar,
                    referral_source='SELF',
                    comments=DEMO_TAG,
                    clinical_history=f'{DEMO_TAG} Client walkthrough visit',
                    status='REGISTERED',
                )
            created.append(patient)
        return created
