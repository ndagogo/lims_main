"""Local-only centres, staff, and catalogue rows for a synthetic walkthrough."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import Centre, SequenceCounter, UserCentreAccess
from patients.models import OrganisationClient
from referrals.models import ReferralDoctor, ReferralInstitution
from laboratory.models import SampleCodeConfig, ServiceGroup, Tariff, Test, TestParameter, TestPrice
from radiology.models import ImagingModality, ImagingPrice, ImagingProcedure

PASSWORD = 'Synthetic-demo-pass-2026!'


class Command(BaseCommand):
    help = 'Create two synthetic centres and staff accounts. Refuses to run unless DEBUG is on.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('seed_synthetic is for local DEBUG use. It will not run in production.')
        call_command('seed_roles')
        call_command('seed_notification_templates')
        User = get_user_model()
        nowas, _ = Centre.objects.get_or_create(code='NWS', defaults={'name': 'NOWAS', 'address': 'Synthetic centre'})
        independence, _ = Centre.objects.get_or_create(code='IND', defaults={'name': 'Independence', 'address': 'Synthetic centre'})
        SampleCodeConfig.objects.get_or_create(centre=nowas, defaults={'numeric_code': '2'})
        SampleCodeConfig.objects.get_or_create(centre=independence, defaults={'numeric_code': '3'})
        lab_catalog = [
            ('CBC', 'Complete Blood Count', 'HAEMATOLOGY', '1', 'Haematology', '1', 'EDTA', Decimal('5500.00')),
            ('MP', 'Malaria Parasite', 'PARASITOLOGY', '2', 'Microscopy', '1', 'EDTA', Decimal('2500.00')),
            ('FBS', 'Fasting Blood Sugar', 'CHEMISTRY', '3', 'Chemistry', '1', 'Fluoride', Decimal('3000.00')),
            ('LFT', 'Liver Function Test', 'CHEMISTRY', '3', 'Chemistry LFT', '2', 'Serum', Decimal('8500.00')),
            ('RFT', 'Renal Function Test', 'CHEMISTRY', '3', 'Chemistry RFT', '3', 'Serum', Decimal('8000.00')),
            ('WIDAL', 'Widal Test', 'MICROBIOLOGY', '4', 'Serology', '1', 'Serum', Decimal('3500.00')),
            ('URINE', 'Urinalysis', 'CLINICAL', '5', 'Urinalysis', '2', 'Urine', Decimal('2000.00')),
            ('ESR', 'Erythrocyte Sedimentation Rate', 'HAEMATOLOGY', '1', 'Haematology ESR', '2', 'Citrate', Decimal('2200.00')),
            ('BG', 'Blood Group', 'HAEMATOLOGY', '6', 'Blood bank', '1', 'EDTA', Decimal('2500.00')),
            ('HBSAG', 'Hepatitis B Surface Antigen', 'MICROBIOLOGY', '4', 'Serology HBsAg', '2', 'Serum', Decimal('4000.00')),
        ]
        imaging_catalog = [
            ('XR-CHEST', 'Chest X-ray', 'XRAY', Decimal('15000.00')),
            ('XR-ABD', 'Abdomen X-ray', 'XRAY', Decimal('15000.00')),
            ('XR-PELVIS', 'Pelvis X-ray', 'XRAY', Decimal('14000.00')),
            ('US-ABD', 'Abdominopelvic Ultrasound', 'US', Decimal('18000.00')),
            ('US-OBS', 'Obstetric Ultrasound', 'US', Decimal('20000.00')),
            ('ECG-REST', 'Resting ECG', 'ECG', Decimal('8000.00')),
        ]
        tariff_factors = [
            ('Standard', Decimal('1.00')),
            ('HMO', Decimal('0.80')),
            ('AXAMAX_TARRIF', Decimal('1.20')),
            ('HFDN_XPAY', Decimal('0.90')),
        ]
        tariffs = {}
        for name, _factor in tariff_factors:
            tariffs[name], _created = Tariff.objects.get_or_create(name=name)
        for code, name, department, department_code, service_group_name, service_group_code, specimen, base in lab_catalog:
            group, _ = ServiceGroup.objects.get_or_create(
                name=service_group_name, code=service_group_code, defaults={'is_active': True},
            )
            row, _created = Test.objects.get_or_create(code=code, defaults={
                'name': name, 'department': department, 'department_code': department_code,
                'service_group': group, 'service_group_code': service_group_code, 'specimen': specimen,
            })
            if not _created and row.service_group_id != group.id:
                row.service_group = group
                row.save(update_fields=['service_group', 'service_group_code', 'updated_at'])
            for tariff_name, factor in tariff_factors:
                TestPrice.objects.get_or_create(tariff=tariffs[tariff_name], test=row, defaults={'price': (base * factor).quantize(Decimal('0.01'))})
        from laboratory.cbc_seed import ensure_cbc_catalogue
        from radiology.modality_templates import ensure_modality_report_templates
        ensure_cbc_catalogue(publish=True)
        ensure_modality_report_templates(publish=True)

        for code, name, modality_type, base in imaging_catalog:
            row, _created = ImagingProcedure.objects.get_or_create(code=code, defaults={'name': name, 'modality_type': modality_type})
            for tariff_name, factor in tariff_factors:
                ImagingPrice.objects.get_or_create(tariff=tariffs[tariff_name], procedure=row, defaults={'price': (base * factor).quantize(Decimal('0.01'))})
        for centre in (nowas, independence):
            ImagingModality.objects.get_or_create(centre=centre, code='XR1', defaults={'name': 'X-ray Room 1', 'modality_type': 'XRAY'})
            ImagingModality.objects.get_or_create(centre=centre, code='US1', defaults={'name': 'Ultrasound Room 1', 'modality_type': 'US'})
            ImagingModality.objects.get_or_create(centre=centre, code='ECG1', defaults={'name': 'ECG Room', 'modality_type': 'ECG'})
        doctor, _created = ReferralDoctor.objects.get_or_create(
            doctor_code='NWS-DR0001',
            defaults={'centre': nowas, 'title': 'Dr', 'first_name': 'Chioma', 'last_name': 'Adeyemi', 'phone': '08030001111', 'commission_type': 'PERCENT', 'commission_value': Decimal('0')},
        )
        for name in ['University of Nigeria Teaching Hospital', 'Annunciation Hospital', 'Mother of Christ Specialist Hospital', 'Park Lane Hospital']:
            ReferralInstitution.objects.get_or_create(doctor=doctor, name=name)
        roster = [
            (nowas, 'NWS-DR0002', 'Dr', 'Emeka', 'Obi', '08030002222', ['Memfys Hospital', 'Niger Foundation Hospital', 'Ntasiobi Specialist Hospital']),
            (nowas, 'NWS-DR0003', 'Dr', 'Ngozi', 'Eze', '08030003333', ['Enugu State University Teaching Hospital', 'Blessed Assurance Hospital', 'Hansel Clinic']),
            (nowas, 'NWS-DR0004', 'Dr', 'Tunde', 'Bakare', '08030004444', ['Lagoon Hospital', 'Reddington Hospital', 'St Nicholas Hospital']),
            (nowas, 'NWS-DR0005', 'Dr', 'Amina', 'Yusuf', '08030005555', ['Federal Medical Centre', 'Garki Hospital', 'Cedarcrest Hospital']),
            (independence, 'IND-DR0001', 'Dr', 'Ifeanyi', 'Nwosu', '08030006666', ['Annunciation Hospital', 'Mother of Christ Specialist Hospital', 'Park Lane Hospital']),
            (independence, 'IND-DR0002', 'Dr', 'Halima', 'Bello', '08030007777', ['Federal Medical Centre', 'Garki Hospital', 'Cedarcrest Hospital']),
        ]
        for centre, code, title, first, last, phone, places in roster:
            row, _created = ReferralDoctor.objects.get_or_create(
                doctor_code=code,
                defaults={'centre': centre, 'title': title, 'first_name': first, 'last_name': last, 'phone': phone, 'commission_type': 'PERCENT', 'commission_value': Decimal('0')},
            )
            for place in places:
                ReferralInstitution.objects.get_or_create(doctor=row, name=place)
        for key, minimum in (('referral-doctor-NWS', 5), ('referral-doctor-IND', 2)):
            counter, _created = SequenceCounter.objects.get_or_create(key=key, defaults={'value': minimum})
            if counter.value < minimum:
                counter.value = minimum
                counter.save(update_fields=['value'])
        accounts = [
            (nowas, 'HMO', ['Hygeia HMO', 'Avon HMO', 'AIICO Multishield', 'Reliance HMO', 'Leadway Health']),
            (nowas, 'CORPORATE', ['Nigerian Breweries', 'MTN Nigeria', 'Access Bank', 'Enugu Electricity Distribution Company', 'Innoson Vehicle Manufacturing']),
            (independence, 'HMO', ['Hygeia HMO', 'Reliance HMO']),
            (independence, 'CORPORATE', ['Access Bank', 'MTN Nigeria']),
        ]
        for centre, kind, names in accounts:
            for name in names:
                OrganisationClient.objects.get_or_create(centre=centre, kind=kind, name=name)

        def staff(username, email, groups, centre, **grants):
            user, created = User.objects.get_or_create(username=username, defaults={'email': email, 'display_name': username})
            if created:
                user.set_password(PASSWORD)
                user.save()
            for name in groups:
                user.groups.add(Group.objects.get(name=name))
            UserCentreAccess.objects.update_or_create(user=user, centre=centre, defaults=grants)
            return user

        full = {'can_view': True, 'can_register': True, 'can_edit': True, 'can_approve': True, 'is_default': True}
        staff('nowas.desk', 'nowas.desk@example.test', ['Front Desk'], nowas, **full)
        staff('nowas.cashier', 'nowas.cashier@example.test', ['Cashier'], nowas, **full)
        staff('nowas.accounts', 'nowas.accounts@example.test', ['Accounts'], nowas, **full)
        staff('nowas.phleb', 'nowas.phleb@example.test', ['Phlebotomist'], nowas, can_view=True, can_edit=True, is_default=True)
        staff('nowas.scientist', 'nowas.scientist@example.test', ['Laboratory Scientist'], nowas, can_view=True, can_edit=True, is_default=True)
        staff('nowas.reviewer', 'nowas.reviewer@example.test', ['Lab Manager'], nowas, can_view=True, can_edit=True, can_approve=True, is_default=True)
        staff('nowas.auth', 'nowas.auth@example.test', ['Lab Manager'], nowas, can_view=True, can_edit=True, can_approve=True, is_default=True)
        staff('nowas.radio', 'nowas.radio@example.test', ['Radiographer'], nowas, can_view=True, can_edit=True, is_default=True)
        staff('nowas.rad', 'nowas.rad@example.test', ['Radiologist'], nowas, can_view=True, can_edit=True, can_approve=True, is_default=True)
        staff('ind.desk', 'ind.desk@example.test', ['Front Desk'], independence, can_view=True, can_register=True, can_edit=True, is_default=True)
        admin, created = User.objects.get_or_create(username='platform.admin', defaults={'email': 'platform.admin@example.test', 'display_name': 'Platform Administrator', 'is_staff': True, 'is_superuser': True})
        if created:
            admin.set_password(PASSWORD)
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()
        admin.groups.add(Group.objects.get(name='Platform Administrator'))
        self.stdout.write(self.style.SUCCESS('Synthetic centres NWS and IND are ready.'))
        self.stdout.write('Staff password for every synthetic account: ' + PASSWORD)
        self.stdout.write('Accounts: platform.admin, nowas.desk, nowas.cashier, nowas.accounts, nowas.phleb, nowas.scientist, nowas.reviewer, nowas.auth, nowas.radio, nowas.rad, ind.desk')
