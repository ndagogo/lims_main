from django.contrib import admin
from .models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport, RadiologyReportRevision

@admin.register(ImagingModality)
class ImagingModalityAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'centre', 'modality_type', 'ae_title', 'is_active')
    list_filter = ('centre', 'modality_type', 'is_active')
    search_fields = ('code', 'name', 'ae_title')

@admin.register(ImagingOrder)
class ImagingOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'visit', 'modality', 'priority', 'status', 'created_at')
    list_filter = ('priority', 'status', 'modality__centre')
    search_fields = ('visit__visit_id', 'visit__patient__uhid')

@admin.register(RadiologyExam)
class RadiologyExamAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'assigned_radiographer', 'status', 'started_at', 'ended_at')
    list_filter = ('status', 'order__modality__centre')

@admin.register(RadiologyReport)
class RadiologyReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'exam', 'author', 'status', 'version', 'finalized_at')
    list_filter = ('status', 'exam__order__modality__centre')
    search_fields = ('exam__order__visit__visit_id', 'impression')
    readonly_fields = tuple(field.name for field in RadiologyReport._meta.fields)
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and obj is None

@admin.register(RadiologyReportRevision)
class RadiologyReportRevisionAdmin(admin.ModelAdmin):
    list_display = ('report', 'version', 'changed_by', 'created_at')
    readonly_fields = ('report', 'version', 'snapshot', 'changed_by', 'reason', 'created_at', 'updated_at')
