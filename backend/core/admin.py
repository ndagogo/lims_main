from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Centre, UserCentreAccess, AuditEvent
@admin.register(User)
class PlatformUserAdmin(UserAdmin):
    list_display=('username','email','display_name','is_active','is_staff','last_login')
    fieldsets=UserAdmin.fieldsets + (('HFDN access', {'fields':('display_name','must_change_password','failed_login_count','last_password_change')}),)
    add_fieldsets=UserAdmin.add_fieldsets + (('Contact', {'fields':('email','display_name')}),)
@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display=('code','name','is_active','phone'); search_fields=('code','name'); list_filter=('is_active',)
@admin.register(UserCentreAccess)
class UserCentreAccessAdmin(admin.ModelAdmin):
    list_display=('user','centre','can_view','can_register','can_edit','can_approve','is_default'); list_filter=('centre','can_view','can_register')
@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display=('created_at','actor','action','object_type','object_id','ip_address')
    list_filter=('action','object_type','created_at'); search_fields=('actor__username','path','object_id')
    readonly_fields=('created_at','updated_at','actor','action','object_type','object_id','path','method','ip_address','browser','centre','detail')
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
