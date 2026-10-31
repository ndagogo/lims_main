from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Canonical role → privilege matrix. Kept in sync with local SuperAdmin roles.
# Running this command on Railway creates/updates these Groups and sets permissions.
ROLE_PERMISSIONS = {
    'Platform Administrator': [
        'view_user', 'add_user', 'change_user', 'view_group', 'add_group', 'change_group',
        'view_centre', 'add_centre', 'change_centre', 'view_usercentreaccess',
        'add_usercentreaccess', 'change_usercentreaccess', 'view_auditevent',
        'access_platform_controls', 'set_result_water_mark', 'approve_discount',
        'cancel_invoice', 'reprint_invoice', 'reverse_payment', 'issue_refund',
        'payment_refund', 'bill_edit', 'bill_modify', 'can_modify_patient_data',
        'change_referring_doctor', 'authenticate_labresult', 'release_labresult',
        'amend_labresult', 'modify_lab_result', 'dispatch_lab_result',
        'approve_referral_settlement', 'finalize_report', 'release_report',
        'approve_result', 'authenticate_result', 'dispatch_result', 'modify_result',
        'override_report', 'add_report_addendum', 'authorize_reporting_substitute',
        'perform_exam',
    ],
    'Centre Administrator': [
        'view_user', 'view_centre', 'view_usercentreaccess', 'add_usercentreaccess',
        'change_usercentreaccess', 'can_modify_patient_data', 'change_referring_doctor',
        'bill_edit', 'payment_refund',
    ],
    'Front Desk': [
        'view_patient', 'add_patient', 'change_patient', 'view_visit', 'add_visit',
        'change_visit', 'change_referring_doctor',
    ],
    'Cashier': [
        'view_invoice', 'view_payment', 'view_visit', 'view_patient', 'reprint_invoice',
    ],
    'Accounts': [
        'view_invoice', 'view_payment', 'view_visit', 'view_patient', 'approve_discount',
        'cancel_invoice', 'reprint_invoice', 'reverse_payment', 'issue_refund',
        'payment_refund', 'bill_edit', 'bill_modify', 'approve_referral_settlement',
    ],
    'Phlebotomist': [
        'view_laborder', 'view_sample', 'add_sample', 'change_sample', 'view_patient',
        'view_visit',
    ],
    'Laboratory Scientist': [
        'view_test', 'view_laborder', 'add_laborder', 'change_laborder', 'view_sample',
        'add_sample', 'change_sample', 'view_samplebatch', 'add_samplebatch',
        'view_labresult', 'add_labresult', 'change_labresult', 'view_testparameter',
        'view_resulttemplate',
    ],
    'Lab Manager': [
        'view_test', 'view_laborder', 'change_laborder', 'view_sample', 'change_sample',
        'view_samplebatch', 'add_samplebatch', 'change_samplebatch', 'view_labresult',
        'change_labresult', 'view_resultreview', 'add_resultreview', 'view_patient',
        'view_visit', 'authenticate_labresult', 'release_labresult', 'amend_labresult',
        'modify_lab_result', 'dispatch_lab_result',
    ],
    'Radiographer': [
        'view_patient', 'view_visit', 'perform_exam',
    ],
    'Radiologist': [
        'view_patient', 'view_visit', 'finalize_report', 'modify_result', 'override_report',
        'add_report_addendum', 'authorize_reporting_substitute',
    ],
    'Referral Doctor': [],
    'Auditor': [
        'view_auditevent', 'view_patient', 'view_visit', 'view_invoice', 'view_payment',
    ],
}


class Command(BaseCommand):
    help = (
        'Create/update baseline HFDN role groups and attach matching Django permissions. '
        'Safe to re-run on Railway; does not delete custom roles created in the UI.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be applied without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        all_codenames = {code for codes in ROLE_PERMISSIONS.values() for code in codes}
        found = {
            p.codename: p
            for p in Permission.objects.filter(codename__in=all_codenames)
        }
        missing = sorted(all_codenames - set(found))
        if missing:
            self.stdout.write(self.style.WARNING(
                f'{len(missing)} permission codename(s) not in DB yet (skipped): {", ".join(missing)}'
            ))

        for role, codenames in ROLE_PERMISSIONS.items():
            permissions = [found[c] for c in codenames if c in found]
            if dry_run:
                exists = Group.objects.filter(name=role).exists()
                self.stdout.write(
                    f'{"UPDATE" if exists else "CREATE"} {role}: {len(permissions)} permissions'
                )
                continue
            group, created = Group.objects.get_or_create(name=role)
            group.permissions.set(permissions)
            verb = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(f'{verb} {role}: {len(permissions)} permissions'))

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Seeded {len(ROLE_PERMISSIONS)} roles. Custom UI roles (if any) were left unchanged.'
            ))
