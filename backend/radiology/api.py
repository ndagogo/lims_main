from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from core.access import require_workspace
from core.models import AuditEvent, UserCentreAccess
from patients.models import Visit
from .models import ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport, RadiologyReportRevision
from .serializers import (ImagingModalitySerializer, ImagingOrderSerializer,
                          RadiologyExamSerializer, RadiologyReportSerializer,
                          RadiologyReportRevisionSerializer)


def require_centre(user, centre, capability='can_view'):
    if user.is_superuser:
        return
    if not UserCentreAccess.objects.filter(user=user, centre=centre, centre__is_active=True,
                                           can_view=True, **{capability: True}).exists():
        raise PermissionDenied(f'{capability.replace("can_", "").replace("_", " ").capitalize()} access is required for this centre.')


def log_event(request, action, obj, centre, detail=None):
    AuditEvent.objects.create(actor=request.user, action=action, object_type=obj.__class__.__name__,
                              object_id=str(obj.pk), path=request.path[:500], method=request.method,
                              ip_address=getattr(request, 'audit_ip', None),
                              browser=getattr(request, 'audit_browser', ''), centre=centre,
                              detail=detail or {})


class ImagingModalityViewSet(viewsets.ModelViewSet):
    serializer_class = ImagingModalitySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['centre', 'modality_type', 'is_active']
    search_fields = ['code', 'name', 'ae_title']
    def get_queryset(self):
        qs = ImagingModality.objects.select_related('centre').all()
        if self.request.user.is_superuser or self.request.user.is_staff:
            return qs
        return qs.filter(centre__user_access__user=self.request.user,
                         centre__user_access__can_view=True, centre__is_active=True).distinct()
    def perform_create(self, serializer):
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            raise PermissionDenied('Only system administrators may configure imaging modalities.')
        serializer.save()
    def perform_update(self, serializer):
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            raise PermissionDenied('Only system administrators may configure imaging modalities.')
        serializer.save()
    def perform_destroy(self, instance):
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            raise PermissionDenied('Only system administrators may configure imaging modalities.')
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])


class ImagingOrderViewSet(viewsets.ModelViewSet):
    serializer_class = ImagingOrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['status', 'priority', 'modality', 'visit__centre']
    search_fields = ['visit__visit_id', 'visit__patient__uhid', 'visit__patient__first_name', 'visit__patient__last_name']
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    def get_queryset(self):
        qs = ImagingOrder.objects.select_related('visit__centre', 'visit__patient', 'modality').prefetch_related('exam')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(visit__centre__user_access__user=self.request.user,
                         visit__centre__user_access__can_view=True, visit__centre__is_active=True).distinct()
    def perform_create(self, serializer):
        require_workspace(self.request.user, 'radiology')
        visit = serializer.validated_data['visit']
        modality = serializer.validated_data['modality']
        require_centre(self.request.user, visit.centre, 'can_edit')
        if not modality.is_active or not visit.centre.is_active:
            raise ValidationError('The centre and modality must be active.')
        with transaction.atomic():
            order = serializer.save(requested_by=self.request.user)
            RadiologyExam.objects.create(order=order)
            log_event(self.request, 'IMAGING_ORDER_CREATED', order, visit.centre,
                      {'modality': modality.code, 'priority': order.priority})
    def perform_update(self, serializer):
        order = self.get_object()
        require_centre(self.request.user, order.visit.centre, 'can_edit')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('Completed or cancelled imaging orders cannot be edited.')
        serializer.save()
    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        order = self.get_object(); require_centre(request.user, order.visit.centre, 'can_edit')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order can no longer be assigned.')
        staff_id = request.data.get('radiographer')
        from django.contrib.auth import get_user_model
        User = get_user_model()
        staff = User.objects.filter(pk=staff_id, is_active=True).first()
        if not staff: raise ValidationError({'radiographer': 'Select an active staff account.'})
        if not staff.is_superuser and not UserCentreAccess.objects.filter(user=staff, centre=order.visit.centre, centre__is_active=True, can_view=True, can_edit=True).exists():
            raise ValidationError({'radiographer': 'The selected radiographer must have edit access to this centre.'})
        exam = order.exam; exam.assigned_radiographer = staff; exam.status = 'ASSIGNED'
        exam.save(update_fields=['assigned_radiographer', 'status', 'updated_at'])
        order.status = 'SCHEDULED'; order.scheduled_at = request.data.get('scheduled_at') or order.scheduled_at
        order.save(update_fields=['status', 'scheduled_at', 'updated_at'])
        log_event(request, 'RADIOLOGY_EXAM_ASSIGNED', exam, order.visit.centre, {'radiographer': staff.username})
        return Response(RadiologyExamSerializer(exam).data)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = self.get_object(); require_centre(request.user, order.visit.centre, 'can_approve')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order is already closed.')
        order.status = 'CANCELLED'; order.save(update_fields=['status', 'updated_at'])
        exam = order.exam; exam.status = 'CANCELLED'; exam.save(update_fields=['status', 'updated_at'])
        log_event(request, 'IMAGING_ORDER_CANCELLED', order, order.visit.centre, {'reason': request.data.get('reason', '')})
        return Response(ImagingOrderSerializer(order).data)


class RadiologyExamViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RadiologyExamSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['status', 'order__modality', 'assigned_radiographer', 'order__visit__centre']
    search_fields = ['order__visit__visit_id', 'order__visit__patient__uhid', 'order__modality__name']
    def get_queryset(self):
        qs = RadiologyExam.objects.select_related('order__visit__centre', 'order__visit__patient',
                                                  'order__modality', 'assigned_radiographer')
        if self.request.user.is_superuser: return qs
        return qs.filter(order__visit__centre__user_access__user=self.request.user,
                         order__visit__centre__user_access__can_view=True,
                         order__visit__centre__is_active=True).distinct()
    def _check_assigned_or_edit(self, request, exam):
        require_centre(request.user, exam.order.visit.centre, 'can_edit')
        if not exam.assigned_radiographer_id:
            raise ValidationError('Assign a radiographer before starting or ending the exam.')
        if exam.assigned_radiographer_id != request.user.pk and not request.user.is_superuser:
            raise PermissionDenied('Only the assigned radiographer or a system administrator may perform this action.')
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object(); self._check_assigned_or_edit(request, exam)
        if exam.status != 'ASSIGNED': raise ValidationError('Only an assigned exam can be started.')
        now = timezone.now(); exam.status = 'IN_PROGRESS'; exam.started_at = now; exam.started_by = request.user
        exam.save(update_fields=['status', 'started_at', 'started_by', 'updated_at'])
        order = exam.order; order.status = 'IN_PROGRESS'; order.save(update_fields=['status', 'updated_at'])
        log_event(request, 'RADIOLOGY_EXAM_STARTED', exam, order.visit.centre)
        return Response(RadiologyExamSerializer(exam).data)
    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object(); self._check_assigned_or_edit(request, exam)
        if exam.status != 'IN_PROGRESS': raise ValidationError('Only an in-progress exam can be ended.')
        now = timezone.now(); exam.status = 'ACQUIRED'; exam.ended_at = now; exam.ended_by = request.user
        exam.acquisition_notes = (request.data.get('acquisition_notes') or '').strip()
        exam.save(update_fields=['status', 'ended_at', 'ended_by', 'acquisition_notes', 'updated_at'])
        log_event(request, 'RADIOLOGY_EXAM_ENDED', exam, exam.order.visit.centre)
        return Response(RadiologyExamSerializer(exam).data)


class RadiologyReportViewSet(viewsets.ModelViewSet):
    serializer_class = RadiologyReportSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['status', 'author', 'exam__order__visit__centre']
    search_fields = ['exam__order__visit__visit_id', 'exam__order__visit__patient__uhid', 'findings', 'impression']
    def get_queryset(self):
        qs = RadiologyReport.objects.select_related('exam__order__visit__centre', 'exam__order__visit__patient', 'author')
        if self.request.user.is_superuser: return qs
        return qs.filter(exam__order__visit__centre__user_access__user=self.request.user,
                         exam__order__visit__centre__user_access__can_view=True,
                         exam__order__visit__centre__is_active=True).distinct()
    def perform_create(self, serializer):
        require_workspace(self.request.user, 'radiology')
        exam = serializer.validated_data['exam']; centre = exam.order.visit.centre
        require_centre(self.request.user, centre, 'can_edit')
        if exam.status != 'ACQUIRED': raise ValidationError('Radiographer must end the exam before report entry.')
        if hasattr(exam, 'report'): raise ValidationError('A report already exists for this exam.')
        report = serializer.save(author=self.request.user)
        log_event(self.request, 'RADIOLOGY_REPORT_CREATED', report, centre)
    def perform_update(self, serializer):
        require_workspace(self.request.user, 'radiology')
        report = self.get_object(); require_centre(self.request.user, report.centre, 'can_edit')
        if report.author_id != self.request.user.pk and not self.request.user.is_superuser:
            raise PermissionDenied('Only the report author may edit this report.')
        if 'exam' in serializer.validated_data and serializer.validated_data['exam'].pk != report.exam_id:
            raise ValidationError('A report cannot be moved to another exam.')
        if report.status != 'DRAFT':
            raise ValidationError('Finalised reports require the controlled override action.')
        serializer.save()
        log_event(self.request, 'RADIOLOGY_REPORT_EDITED', report, report.centre)
    @action(detail=True, methods=['post'])
    def finalize(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        report = self.get_object(); require_centre(request.user, report.centre, 'can_approve')
        if report.status != 'DRAFT': raise ValidationError('Only draft reports can be finalised.')
        if not report.findings.strip() or not report.impression.strip():
            raise ValidationError('Findings and impression are required before finalisation.')
        report.status = 'FINAL'; report.finalized_at = timezone.now(); report.finalized_by = request.user
        report.save(update_fields=['status', 'finalized_at', 'finalized_by', 'updated_at'])
        order = report.exam.order
        order.status = 'COMPLETED'; order.save(update_fields=['status', 'updated_at'])
        log_event(request, 'RADIOLOGY_REPORT_FINALIZED', report, report.centre)
        return Response(self.get_serializer(report).data)
    @action(detail=True, methods=['post'])
    def override(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        report = self.get_object(); require_centre(request.user, report.centre, 'can_approve')
        reason = (request.data.get('reason') or '').strip()
        if not reason: raise ValidationError({'reason': 'A reason is required for a controlled report override.'})
        if report.status not in ('FINAL', 'AMENDED'):
            raise ValidationError('Controlled override is only available for finalised reports.')
        fields = ('findings', 'impression', 'recommendations')
        changes = {field: request.data[field] for field in fields if field in request.data}
        if not changes: raise ValidationError('Supply at least one report field to amend.')
        with transaction.atomic():
            old_version = report.version
            snapshot = {'status': report.status, 'findings': report.findings,
                        'impression': report.impression, 'recommendations': report.recommendations,
                        'version': old_version, 'finalized_at': report.finalized_at.isoformat() if report.finalized_at else None}
            RadiologyReportRevision.objects.create(report=report, version=old_version,
                                                   snapshot=snapshot, changed_by=request.user, reason=reason)
            for field, value in changes.items(): setattr(report, field, value)
            report.version = old_version + 1; report.status = 'AMENDED'
            report.last_override_by = request.user; report.last_override_at = timezone.now()
            report.last_override_reason = reason
            report.save(update_fields=[*changes.keys(), 'version', 'status', 'last_override_by',
                                       'last_override_at', 'last_override_reason', 'updated_at'])
            log_event(request, 'RADIOLOGY_REPORT_OVERRIDDEN', report, report.centre,
                      {'previous_version': old_version, 'new_version': report.version, 'reason': reason})
        return Response(self.get_serializer(report).data)
    @action(detail=True, methods=['get'])
    def revisions(self, request, pk=None):
        report = self.get_object()
        return Response(RadiologyReportRevisionSerializer(report.revisions.all(), many=True).data)
