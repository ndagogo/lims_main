from django.contrib import admin

from .models import OrderWorkflowInstance, WorkflowDefinition, WorkflowStep, WorkflowTransition


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    extra = 0


class WorkflowTransitionInline(admin.TabularInline):
    model = WorkflowTransition
    extra = 0
    fk_name = 'workflow'


@admin.register(WorkflowDefinition)
class WorkflowDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'version', 'is_active')
    list_filter = ('is_active', 'code')
    inlines = [WorkflowStepInline, WorkflowTransitionInline]


@admin.register(OrderWorkflowInstance)
class OrderWorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ('lab_order', 'workflow', 'current_step', 'completed', 'updated_at')
    list_filter = ('completed', 'workflow')
    raw_id_fields = ('lab_order',)
