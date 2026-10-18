from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission

ROLE_PERMISSIONS = {
 'Platform Administrator': ['view_user','add_user','change_user','view_group','add_group','change_group','view_centre','add_centre','change_centre','view_usercentreaccess','add_usercentreaccess','change_usercentreaccess','view_auditevent','access_platform_controls','approve_discount','cancel_invoice','reverse_payment','issue_refund','payment_refund','bill_edit','bill_modify','can_modify_patient_data','change_referring_doctor','authenticate_labresult','release_labresult','amend_labresult','approve_referral_settlement','finalize_report','release_report','override_report','add_report_addendum','authorize_reporting_substitute','perform_exam'],
 'Centre Administrator': ['view_user','view_centre','view_usercentreaccess','add_usercentreaccess','change_usercentreaccess','can_modify_patient_data','change_referring_doctor','bill_edit','payment_refund'],
 'Front Desk': ['view_patient','add_patient','change_patient','view_visit','add_visit','change_visit','change_referring_doctor'],
 'Cashier': ['view_invoice','view_payment','view_visit','view_patient'],
 'Accounts': ['view_invoice','view_payment','view_visit','view_patient','approve_discount','cancel_invoice','reverse_payment','issue_refund','payment_refund','bill_edit','bill_modify','approve_referral_settlement'],
 'Phlebotomist': ['view_laborder','view_sample','add_sample','change_sample','view_patient','view_visit'],
 'Laboratory Scientist': ['view_test','view_laborder','add_laborder','change_laborder','view_sample','add_sample','change_sample','view_samplebatch','add_samplebatch','view_labresult','add_labresult','change_labresult','view_testparameter','view_resulttemplate'],
 'Lab Manager': ['view_test','view_laborder','change_laborder','view_sample','change_sample','view_samplebatch','add_samplebatch','change_samplebatch','view_labresult','change_labresult','view_resultreview','add_resultreview','view_patient','view_visit','authenticate_labresult','release_labresult','amend_labresult'],
 'Radiographer': ['view_patient','view_visit','perform_exam'],
 'Radiologist': ['view_patient','view_visit','finalize_report','release_report','override_report','add_report_addendum','authorize_reporting_substitute'],
 'Referral Doctor': [],
 'Auditor': ['view_auditevent','view_patient','view_visit','view_invoice','view_payment'],
}
class Command(BaseCommand):
    help = 'Create baseline HFDN role groups and attach matching Django model permissions.'
    def handle(self, *args, **options):
        for role, codenames in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=role)
            permissions = Permission.objects.filter(codename__in=codenames)
            group.permissions.set(permissions)
            self.stdout.write(self.style.SUCCESS(f'{role}: {permissions.count()} permissions'))
