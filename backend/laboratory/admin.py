from django.contrib import admin
from .models import (
    ServiceGroup, Test, Tariff, TestPrice, LabOrder, Sample, SampleCodeConfig, SampleActivity,
    SampleBarcodePrint, SampleBatch, BatchSample, SampleProcessing, TestParameter, ResultTemplate,
    ResultTemplateField, LabResult, ResultValue, ResultReview, ReferenceRange,
    QcMaterial, QcRun, QcTarget, TatEscalation, TatPause, TatPauseReason, TatRule,
)


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ('sample_id', 'order', 'centre', 'status', 'disposition', 'collected_by', 'collected_at')
    list_filter = ('centre', 'status', 'disposition', 'order__test__department')
    search_fields = ('sample_id', 'order__visit__visit_id', 'order__visit__patient__uhid', 'order__visit__patient__phone_number')
    readonly_fields = ('sample_id', 'created_at', 'updated_at', 'collector_ip', 'collector_browser', 'rejected_at')


@admin.register(SampleBarcodePrint)
class SampleBarcodePrintAdmin(admin.ModelAdmin):
    list_display = ('barcode_value', 'sample', 'source', 'printed_by', 'printed_at')
    list_filter = ('source', 'printed_at')
    search_fields = ('barcode_value', 'sample__sample_id')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ('sample', 'status', 'entered_by', 'reviewed_by', 'authenticated_by', 'released_by')
    list_filter = ('status', 'sample__centre', 'sample__order__test__department')
    readonly_fields = ('entered_at', 'reviewed_at', 'authenticated_at', 'released_at')


@admin.register(QcMaterial)
class QcMaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'centre', 'test', 'lot_number', 'expires_on', 'is_active')
    list_filter = ('centre', 'is_active')
    search_fields = ('name', 'lot_number', 'test__code')


@admin.register(QcRun)
class QcRunAdmin(admin.ModelAdmin):
    list_display = ('material', 'parameter', 'value', 'acceptable', 'entered_by', 'run_at')
    list_filter = ('acceptable', 'material__centre')


@admin.register(TatRule)
class TatRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'test', 'service_group', 'expected_minutes', 'clock_start', 'clock_end', 'is_active')
    list_filter = ('is_active', 'clock_start', 'clock_end')
    search_fields = ('name', 'test__code', 'service_group__name')


admin.site.register([
    ServiceGroup, Test, Tariff, TestPrice, LabOrder, SampleCodeConfig, SampleActivity,
    SampleBatch, BatchSample, SampleProcessing, TestParameter, ResultTemplate, ResultTemplateField,
    ResultValue, ResultReview, ReferenceRange, QcTarget, TatPauseReason, TatPause, TatEscalation,
])
