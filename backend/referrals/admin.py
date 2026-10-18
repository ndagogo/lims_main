from django.contrib import admin
from .models import ReferralDoctor, ReferralInstitution, ReferralAttribution, CommissionLedgerEntry, WithdrawalRequest
class ReferralInstitutionInline(admin.TabularInline):
    model=ReferralInstitution
    extra=1
@admin.register(ReferralDoctor)
class ReferralDoctorAdmin(admin.ModelAdmin):
    list_display=('doctor_code','first_name','last_name','facility_name','centre','onboarded_by','appear_on_reports','commission_type','commission_value','is_active')
    list_filter=('centre','is_active','commission_type','appear_on_reports')
    search_fields=('doctor_code','first_name','last_name','facility_name','phone','account_number')
    filter_horizontal=('centres',)
    inlines=[ReferralInstitutionInline]
@admin.register(ReferralInstitution)
class ReferralInstitutionAdmin(admin.ModelAdmin):
    list_display=('name','doctor','is_active')
    list_filter=('is_active','doctor__centre')
    search_fields=('name','doctor__doctor_code','doctor__last_name')
@admin.register(ReferralAttribution)
class ReferralAttributionAdmin(admin.ModelAdmin):
    list_display=('visit','doctor','created_at')
    search_fields=('visit__visit_id','doctor__doctor_code')
    filter_horizontal=('institutions',)
@admin.register(CommissionLedgerEntry)
class LedgerAdmin(admin.ModelAdmin): list_display=('created_at','doctor','entry_type','amount','invoice'); readonly_fields=('id','doctor','invoice','entry_type','amount','currency','description','source_entry','created_by','created_at','updated_at'); has_delete_permission=lambda self, request, obj=None: False
@admin.register(WithdrawalRequest)
class WithdrawalAdmin(admin.ModelAdmin): list_display=('requested_at','doctor','amount','status','reviewed_by','settled_at'); list_filter=('status','doctor__centre'); readonly_fields=('requested_at','settled_at')
