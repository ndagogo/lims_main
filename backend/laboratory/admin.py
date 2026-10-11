from django.contrib import admin
from .models import (ServiceGroup,Test,Tariff,TestPrice,LabOrder,Sample,SampleCodeConfig,SampleActivity,SampleBatch,BatchSample,SampleProcessing,TestParameter,ResultTemplate,LabResult,ResultValue,ResultReview)
@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display=('sample_id','order','centre','status','collected_by','collected_at')
    list_filter=('centre','status','order__test__department')
    search_fields=('sample_id','order__visit__visit_id','order__visit__patient__uhid','order__visit__patient__phone_number')
    readonly_fields=('sample_id','created_at','updated_at','collector_ip','collector_browser')
@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display=('sample','status','entered_by','reviewed_by','authenticated_by','released_by')
    list_filter=('status','sample__centre','sample__order__test__department')
    readonly_fields=('entered_at','reviewed_at','authenticated_at','released_at')
admin.site.register([ServiceGroup,Test,Tariff,TestPrice,LabOrder,SampleCodeConfig,SampleActivity,SampleBatch,BatchSample,SampleProcessing,TestParameter,ResultTemplate,ResultValue,ResultReview])
