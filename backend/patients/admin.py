from django.contrib import admin
from .models import OrganisationClient, Patient, Visit, PatientDocument

@admin.register(OrganisationClient)
class OrganisationClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'code', 'centre', 'is_active')
    list_filter = ('kind', 'centre', 'is_active')
    search_fields = ('name', 'code')
@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin): list_display=('uhid','first_name','last_name','phone_number','created_at'); search_fields=('uhid','first_name','last_name','phone_number')
@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin): list_display=('visit_id','patient','centre','status','created_at'); search_fields=('visit_id','patient__uhid')
admin.site.register(PatientDocument)
