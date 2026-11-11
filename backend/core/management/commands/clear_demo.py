"""Remove client-demo rows created by seed_demo."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.demo_seed import (
    DEMO_CENTRE_CODES,
    DEMO_DOCTOR_CODES,
    DEMO_HEALTH_CARD,
    DEMO_IMAGING_CODES,
    DEMO_SERVICE_GROUP_CODE,
    DEMO_SERVICE_GROUP_NAME,
    DEMO_TAG,
    DEMO_TARIFF_NAMES,
    DEMO_TEST_CODES,
    DEMO_USERNAMES,
)
from core.models import Centre, UserCentreAccess
from laboratory.models import LabOrder, SampleCodeConfig, ServiceGroup, Tariff, Test, TestPrice
from patients.models import OrganisationClient, Patient, Visit
from radiology.models import ImagingModality, ImagingOrder, ImagingPrice, ImagingProcedure
from referrals.models import ReferralDoctor


class Command(BaseCommand):
    help = 'Delete tagged client-demo data from seed_demo. Requires --confirm.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required. Confirms deletion of demo-tagged rows only.',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('Refused. Re-run with --confirm to delete demo seed data.')

        User = get_user_model()
        removed = []

        with transaction.atomic():
            centres = list(Centre.objects.filter(code__in=DEMO_CENTRE_CODES))
            centre_ids = [c.pk for c in centres]
            patients = Patient.objects.filter(health_card_number=DEMO_HEALTH_CARD)
            visits = Visit.objects.filter(comments=DEMO_TAG) | Visit.objects.filter(
                patient__in=patients, centre_id__in=centre_ids,
            )
            visit_ids = list(visits.values_list('pk', flat=True))

            # Billing / orders attached to demo visits (may be empty on a fresh seed).
            try:
                from billing.models import BillAmendment, DiscountRequest, Invoice, Payment, Refund

                invoices = Invoice.objects.filter(visit_id__in=visit_ids)
                invoice_ids = list(invoices.values_list('pk', flat=True))
                payments = Payment.objects.filter(invoice_id__in=invoice_ids)
                payment_ids = list(payments.values_list('pk', flat=True))
                n = Refund.objects.filter(payment_id__in=payment_ids).delete()[0]
                n += Refund.objects.filter(invoice_id__in=invoice_ids).delete()[0]
                if n:
                    removed.append(f'refunds:{n}')
                n = BillAmendment.objects.filter(invoice_id__in=invoice_ids).delete()[0]
                if n:
                    removed.append(f'amendments:{n}')
                n = DiscountRequest.objects.filter(invoice_id__in=invoice_ids).delete()[0]
                if n:
                    removed.append(f'discounts:{n}')
                n = payments.delete()[0]
                if n:
                    removed.append(f'payments:{n}')
                n = invoices.delete()[0]
                if n:
                    removed.append(f'invoices:{n}')
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f'Billing cleanup skipped: {exc}'))

            n = LabOrder.objects.filter(visit_id__in=visit_ids).delete()[0]
            if n:
                removed.append(f'lab_orders:{n}')

            try:
                from radiology.models import RadiologyExam, RadiologyReport

                orders = ImagingOrder.objects.filter(visit_id__in=visit_ids)
                order_ids = list(orders.values_list('pk', flat=True))
                exams = RadiologyExam.objects.filter(order_id__in=order_ids)
                exam_ids = list(exams.values_list('pk', flat=True))
                n = RadiologyReport.objects.filter(exam_id__in=exam_ids).delete()[0]
                if n:
                    removed.append(f'rad_reports:{n}')
                n = exams.delete()[0]
                if n:
                    removed.append(f'rad_exams:{n}')
                n = orders.delete()[0]
                if n:
                    removed.append(f'imaging_orders:{n}')
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f'Radiology cleanup skipped: {exc}'))

            n = visits.delete()[0]
            if n:
                removed.append(f'visits:{n}')
            n = patients.delete()[0]
            if n:
                removed.append(f'patients:{n}')

            n = OrganisationClient.objects.filter(name__startswith=DEMO_TAG).delete()[0]
            if n:
                removed.append(f'org_clients:{n}')

            doctors = ReferralDoctor.objects.filter(doctor_code__in=DEMO_DOCTOR_CODES)
            n = doctors.delete()[0]
            if n:
                removed.append(f'doctors:{n}')

            n = ImagingModality.objects.filter(code__startswith='DEMO-').delete()[0]
            if n:
                removed.append(f'modalities:{n}')

            tests = Test.objects.filter(code__in=DEMO_TEST_CODES)
            n = TestPrice.objects.filter(test__in=tests).delete()[0]
            if n:
                removed.append(f'test_prices:{n}')
            n = tests.delete()[0]
            if n:
                removed.append(f'tests:{n}')

            procedures = ImagingProcedure.objects.filter(code__in=DEMO_IMAGING_CODES)
            n = ImagingPrice.objects.filter(procedure__in=procedures).delete()[0]
            if n:
                removed.append(f'imaging_prices:{n}')
            n = procedures.delete()[0]
            if n:
                removed.append(f'imaging_procedures:{n}')

            n = Tariff.objects.filter(name__in=DEMO_TARIFF_NAMES).delete()[0]
            if n:
                removed.append(f'tariffs:{n}')

            n = ServiceGroup.objects.filter(
                name=DEMO_SERVICE_GROUP_NAME, code=DEMO_SERVICE_GROUP_CODE,
            ).delete()[0]
            if n:
                removed.append(f'service_groups:{n}')

            demo_users = User.objects.filter(username__in=DEMO_USERNAMES)
            n = UserCentreAccess.objects.filter(user__in=demo_users).delete()[0]
            if n:
                removed.append(f'user_centre_access:{n}')
            n = demo_users.delete()[0]
            if n:
                removed.append(f'users:{n}')

            n = SampleCodeConfig.objects.filter(centre_id__in=centre_ids).delete()[0]
            if n:
                removed.append(f'sample_codes:{n}')
            n = Centre.objects.filter(code__in=DEMO_CENTRE_CODES).delete()[0]
            if n:
                removed.append(f'centres:{n}')

        if removed:
            self.stdout.write(self.style.SUCCESS('Demo data removed: ' + ', '.join(removed)))
        else:
            self.stdout.write('No demo-tagged rows found.')
        if settings.DEBUG:
            self.stdout.write('Tip: re-seed locally with python manage.py seed_demo')
        else:
            self.stdout.write('Tip: re-seed on Railway with python manage.py seed_demo --confirm')
