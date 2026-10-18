from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Centre, CentreReportFormat, UserCentreAccess, AuditEvent
@admin.register(User)
class PlatformUserAdmin(UserAdmin):
    list_display=('username','email','display_name','is_active','is_staff','last_login')
    fieldsets=UserAdmin.fieldsets + ((
        'HFDN profile',
        {'fields': (
            'display_name', 'title', 'other_names', 'date_of_birth', 'sex', 'marital_status', 'address',
            'avatar', 'job_title', 'staff_id', 'department', 'specialty',
            'qualifications', 'license_number', 'license_authority',
            'phone_work', 'phone_mobile', 'office_extension', 'bio',
            'signature', 'signature_title',
            'must_change_password', 'failed_login_count', 'last_password_change',
        )},
    ),)
    add_fieldsets=UserAdmin.add_fieldsets + (('Contact', {'fields':('email','display_name')}),)
@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display=('code','name','is_active','phone'); search_fields=('code','name'); list_filter=('is_active',)
@admin.register(CentreReportFormat)
class CentreReportFormatAdmin(admin.ModelAdmin):
    list_display=('centre','layout_style','font_family','show_authenticator_signature')
    list_filter=('layout_style','font_family')
@admin.register(UserCentreAccess)
class UserCentreAccessAdmin(admin.ModelAdmin):
    list_display=('user','centre','can_view','can_register','can_edit','can_approve','is_default'); list_filter=('centre','can_view','can_register')
@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display=('created_at','actor','action','category','object_type','centre','patient_ref','ip_address')
    list_filter=('category','action','object_type','centre','created_at')
    search_fields=('actor__username','path','object_id','patient_ref','visit_ref','order_ref','result_ref','reason','department')
    readonly_fields=(
        'created_at','updated_at','actor','action','category','object_type','object_id','path','method',
        'ip_address','browser','centre','department','patient_ref','visit_ref','order_ref','result_ref',
        'previous_values','new_values','reason','approval_by','approval_note','evidence_ref','evidence_note','detail',
    )
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
