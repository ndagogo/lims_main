from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.access import RADIOGRAPHER_ROLES, RADIOLOGIST_ROLES, require_any_role, require_workspace
from core.models import AuditEvent, UserCentreAccess
from radiology.activity import record_exam_activity
from radiology.models import (
    ImagingModality, ImagingOrder, RadiologyExam, RadiologyReport, RadiologyReportRevision,
    RadiologyReportTemplate,
)
from radiology.serializers import (
    ImagingModalitySerializer, ImagingOrderSerializer, RadiologyExamSerializer,
    RadiologyReportSerializer, RadiologyReportRevisionSerializer, RadiologyReportTemplateSerializer,
)
from radiology.templates_svc import freeze_report_configuration, resolve_report_templates


def require_centre(user, centre, capability='can_view'):
    if user.is_superuser:
        return
    filters = {
        'user': user,
        'centre': centre,
        'centre__is_active': True,
        'can_view': True,
    }
    if capability and capability != 'can_view':
        filters[capability] = True
    if not UserCentreAccess.objects.filter(**filters).exists():
        raise PermissionDenied(
            f'{capability.replace("can_", "").replace("_", " ").capitalize()} access is required for this centre.'
        )


def log_event(request, action, obj, centre, detail=None, **kwargs):
    from core.audit import record_audit
    record_audit(
        request=request,
        actor=request.user,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        centre=centre,
        detail=detail or {},
        patient_ref=kwargs.get('patient_ref', ''),
        visit_ref=kwargs.get('visit_ref', ''),
        order_ref=kwargs.get('order_ref', ''),
        result_ref=kwargs.get('result_ref', ''),
        previous_values=kwargs.get('previous_values'),
        new_values=kwargs.get('new_values'),
        reason=kwargs.get('reason', ''),
        approval_by=kwargs.get('approval_by'),
        approval_note=kwargs.get('approval_note', ''),
        evidence_ref=kwargs.get('evidence_ref', ''),
        evidence_note=kwargs.get('evidence_note', ''),
    )


def _active_staff(staff_id, centre, *, capability='can_edit'):
    User = get_user_model()
    staff = User.objects.filter(pk=staff_id, is_active=True).first()
    if not staff:
        return None
    if staff.is_superuser:
        return staff
    if not UserCentreAccess.objects.filter(
        user=staff, centre=centre, centre__is_active=True, can_view=True, **{capability: True},
    ).exists():
        return None
    return staff


class ImagingModalityViewSet(viewsets.ModelViewSet):
    serializer_class = ImagingModalitySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['centre', 'modality_type', 'is_active']
    search_fields = ['code', 'name', 'ae_title']

    def get_queryset(self):
        qs = ImagingModality.objects.select_related('centre').all()
        if self.request.user.is_superuser or self.request.user.is_staff:
            return qs
        return qs.filter(
            centre__user_access__user=self.request.user,
            centre__user_access__can_view=True, centre__is_active=True,
        ).distinct()

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
        return qs.filter(
            visit__centre__user_access__user=self.request.user,
            visit__centre__user_access__can_view=True, visit__centre__is_active=True,
        ).distinct()

    def perform_create(self, serializer):
        require_workspace(self.request.user, 'radiology')
        visit = serializer.validated_data['visit']
        modality = serializer.validated_data['modality']
        require_centre(self.request.user, visit.centre, 'can_edit')
        if not modality.is_active or not visit.centre.is_active:
            raise ValidationError('The centre and modality must be active.')
        with transaction.atomic():
            order = serializer.save(requested_by=self.request.user)
            exam = RadiologyExam.objects.create(order=order)
            record_exam_activity(
                exam, action='CREATED', user=self.request.user,
                to_status=exam.status, note='Worklist entry created', request=self.request,
            )
            log_event(self.request, 'IMAGING_ORDER_CREATED', order, visit.centre,
                      {'modality': modality.code, 'priority': order.priority})
            from radiology.worklist import publish_exam
            publish_exam(exam)

    def perform_update(self, serializer):
        order = self.get_object()
        require_centre(self.request.user, order.visit.centre, 'can_edit')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('Completed or cancelled imaging orders cannot be edited.')
        serializer.save()

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        order = self.get_object()
        require_centre(request.user, order.visit.centre, 'can_edit')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order can no longer be assigned.')
        staff = _active_staff(request.data.get('radiographer'), order.visit.centre)
        if not staff or not staff.groups.filter(name='Radiographer').exists():
            raise ValidationError({'radiographer': 'Select a radiographer. Only radiographer accounts can be assigned.'})
        exam = order.exam
        if exam.pacs_uploaded_at:
            raise ValidationError('This study has already been sent to the PACS server.')
        from_status = exam.status
        exam.assigned_radiographer = staff
        if exam.status not in ('IN_PROGRESS', 'ACQUIRED'):
            exam.status = 'ASSIGNED'
        exam.save(update_fields=['assigned_radiographer', 'status', 'updated_at'])
        order.status = 'SCHEDULED'
        order.scheduled_at = request.data.get('scheduled_at') or order.scheduled_at
        order.save(update_fields=['status', 'scheduled_at', 'updated_at'])
        record_exam_activity(
            exam, action='ASSIGN_RADIOGRAPHER', user=request.user,
            from_status=from_status, to_status='ASSIGNED',
            note=f'Radiographer {staff.get_username()}', request=request,
        )
        log_event(request, 'RADIOLOGY_EXAM_ASSIGNED', exam, order.visit.centre, {'radiographer': staff.username})
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'], url_path='assign-radiologist')
    def assign_radiologist(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        order = self.get_object()
        require_centre(request.user, order.visit.centre, 'can_edit')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order can no longer be assigned.')
        staff = _active_staff(request.data.get('radiologist'), order.visit.centre, capability='can_approve')
        if not staff:
            # Fall back to can_edit for radiologists who may not have approve.
            staff = _active_staff(request.data.get('radiologist'), order.visit.centre, capability='can_edit')
        if not staff or not staff.groups.filter(name='Radiologist').exists():
            raise ValidationError({'radiologist': 'Select a radiologist.'})
        exam = order.exam
        if exam.accepted_at:
            raise ValidationError('This study has already been accepted.')
        from radiology.desk import can_reassign_radiologist
        if exam.assigned_radiologist_id and exam.assigned_radiologist_id != staff.pk and not can_reassign_radiologist(exam):
            raise ValidationError('This study is waiting for the assigned radiologist. Reassignment opens when that time elapses.')
        exam.assigned_radiologist = staff
        exam.accepted_at = None
        fields = ['assigned_radiologist', 'accepted_at', 'updated_at']
        if exam.pacs_uploaded_at:
            exam.radiologist_assigned_at = timezone.now()
            fields.append('radiologist_assigned_at')
        exam.save(update_fields=fields)
        record_exam_activity(
            exam, action='ASSIGN_RADIOLOGIST', user=request.user,
            from_status=exam.status, to_status=exam.status,
            note=f'Radiologist {staff.get_username()}', request=request,
        )
        log_event(request, 'RADIOLOGY_RADIOLOGIST_ASSIGNED', exam, order.visit.centre, {'radiologist': staff.username})
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'], url_path='authorize-substitute')
    def authorize_substitute(self, request, pk=None):
        """Controlled policy: authorise a substitute reporting radiologist."""
        require_workspace(request.user, 'radiology')
        require_any_role(
            request.user, RADIOLOGIST_ROLES,
            perm='radiology.authorize_reporting_substitute',
        )
        order = self.get_object()
        require_centre(request.user, order.visit.centre, 'can_approve')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order can no longer be modified.')
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A clinical/admin reason is required to authorise a substitute.'})
        staff = _active_staff(request.data.get('substitute'), order.visit.centre, capability='can_edit')
        if not staff:
            raise ValidationError({'substitute': 'Select an active radiologist with access to this centre.'})
        exam = order.exam
        if not exam.assigned_radiologist_id:
            raise ValidationError('Assign the primary reporting radiologist before authorising a substitute.')
        if exam.assigned_radiologist_id == staff.pk:
            raise ValidationError('Substitute must be different from the assigned reporting radiologist.')
        exam.reporting_substitute = staff
        exam.substitute_reason = reason
        exam.substitute_authorized_by = request.user
        exam.substitute_authorized_at = timezone.now()
        exam.save(update_fields=[
            'reporting_substitute', 'substitute_reason', 'substitute_authorized_by',
            'substitute_authorized_at', 'updated_at',
        ])
        record_exam_activity(
            exam, action='AUTHORIZE_SUBSTITUTE', user=request.user,
            from_status=exam.status, to_status=exam.status,
            note=f'{staff.get_username()}: {reason}', request=request,
        )
        log_event(request, 'RADIOLOGY_SUBSTITUTE_AUTHORIZED', exam, order.visit.centre, {
            'substitute': staff.username, 'reason': reason,
        })
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        order = self.get_object()
        require_centre(request.user, order.visit.centre, 'can_approve')
        if order.status in ('COMPLETED', 'CANCELLED'):
            raise ValidationError('This order is already closed.')
        order.status = 'CANCELLED'
        order.save(update_fields=['status', 'updated_at'])
        exam = order.exam
        from_status = exam.status
        exam.status = 'CANCELLED'
        exam.mwl_status = 'CANCELLED'
        exam.save(update_fields=['status', 'mwl_status', 'updated_at'])
        record_exam_activity(
            exam, action='CANCELLED', user=request.user,
            from_status=from_status, to_status='CANCELLED',
            note=(request.data.get('reason') or '').strip(), request=request,
        )
        log_event(request, 'IMAGING_ORDER_CANCELLED', order, order.visit.centre, {'reason': request.data.get('reason', '')})
        return Response(ImagingOrderSerializer(order).data)


class RadiologyExamViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RadiologyExamSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['status', 'order__modality', 'assigned_radiographer', 'assigned_radiologist', 'order__visit__centre']
    search_fields = ['order__visit__visit_id', 'order__visit__patient__uhid', 'order__modality__name']

    def get_queryset(self):
        qs = RadiologyExam.objects.select_related(
            'order__visit__centre', 'order__visit__patient', 'order__modality',
            'assigned_radiographer', 'assigned_radiologist',
        )
        if self.request.user.is_superuser:
            return qs
        return qs.filter(
            order__visit__centre__user_access__user=self.request.user,
            order__visit__centre__user_access__can_view=True,
            order__visit__centre__is_active=True,
        ).distinct()

    def _require_radiographer(self, request, exam):
        require_centre(request.user, exam.order.visit.centre, 'can_edit')
        if request.user.is_superuser:
            return
        require_any_role(request.user, ('Radiographer',), perm='radiology.perform_exam')

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object()
        self._require_radiographer(request, exam)
        if exam.status != 'ASSIGNED':
            raise ValidationError('Only a study that has not started can be started.')
        from radiology.desk import EXAM_IN_PROCESS, radiographer_active_exam
        active = radiographer_active_exam(request.user)
        if active is not None and active.pk != exam.pk:
            raise ValidationError(EXAM_IN_PROCESS)
        from_status = exam.status
        now = timezone.now()
        exam.status = 'IN_PROGRESS'
        exam.started_at = now
        exam.started_by = request.user
        if request.user.groups.filter(name='Radiographer').exists():
            exam.assigned_radiographer = request.user
        exam.mwl_status = exam.mwl_status or 'IN_PROGRESS'
        exam.save(update_fields=[
            'status', 'started_at', 'started_by', 'assigned_radiographer', 'mwl_status', 'updated_at',
        ])
        order = exam.order
        order.status = 'IN_PROGRESS'
        order.save(update_fields=['status', 'updated_at'])
        record_exam_activity(
            exam, action='START_EXAM', user=request.user,
            from_status=from_status, to_status='IN_PROGRESS', request=request,
        )
        log_event(request, 'RADIOLOGY_EXAM_STARTED', exam, order.visit.centre)
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object()
        self._require_radiographer(request, exam)
        if exam.status != 'IN_PROGRESS':
            raise ValidationError('Only an in-progress exam can be ended.')
        from radiology.desk import parse_supplies
        from radiology.models import ExamSupplyUse
        supplies = parse_supplies(request.data)
        radiologist_id = request.data.get('radiologist')
        staff = None
        if radiologist_id:
            staff = _active_staff(radiologist_id, exam.order.visit.centre, capability='can_approve')
            if not staff:
                staff = _active_staff(radiologist_id, exam.order.visit.centre, capability='can_edit')
            if not staff or not staff.groups.filter(name='Radiologist').exists():
                raise ValidationError({'radiologist': 'Select a radiologist.'})
        from_status = exam.status
        now = timezone.now()
        with transaction.atomic():
            exam.status = 'ACQUIRED'
            exam.ended_at = now
            exam.ended_by = request.user
            exam.acquisition_notes = (request.data.get('acquisition_notes') or '').strip()
            exam.mwl_status = 'COMPLETED'
            exam.save(update_fields=['status', 'ended_at', 'ended_by', 'acquisition_notes', 'mwl_status', 'updated_at'])
            exam.supplies.all().delete()
            ExamSupplyUse.objects.bulk_create([
                ExamSupplyUse(exam=exam, item_name=name, quantity=qty) for name, qty in supplies
            ])
            if staff is not None:
                from radiology.desk import can_reassign_radiologist
                if exam.assigned_radiologist_id and exam.assigned_radiologist_id != staff.pk and not can_reassign_radiologist(exam):
                    raise ValidationError('This study is waiting for the assigned radiologist. Reassignment opens when that time elapses.')
                exam.assigned_radiologist = staff
                exam.accepted_at = None
                fields = ['assigned_radiologist', 'accepted_at', 'updated_at']
                if exam.pacs_uploaded_at:
                    exam.radiologist_assigned_at = now
                    fields.append('radiologist_assigned_at')
                exam.save(update_fields=fields)
                record_exam_activity(
                    exam, action='ASSIGN_RADIOLOGIST', user=request.user,
                    from_status='ACQUIRED', to_status='ACQUIRED',
                    note=f'Radiologist {staff.get_username()}', request=request,
                )
        used = ', '.join(f'{name} x{qty}' for name, qty in supplies)
        record_exam_activity(
            exam, action='END_EXAM', user=request.user,
            from_status=from_status, to_status='ACQUIRED',
            note=used or exam.acquisition_notes, request=request,
        )
        log_event(request, 'RADIOLOGY_EXAM_ENDED', exam, exam.order.visit.centre, {'supplies': used})
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'], url_path='upload-pacs')
    def upload_pacs(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object()
        self._require_radiographer(request, exam)
        if exam.status != 'ACQUIRED':
            raise ValidationError('End the exam before uploading to the PACS server.')
        if exam.pacs_uploaded_at:
            raise ValidationError('This study is already on the PACS server.')
        from radiology.orthanc import c_echo, orthanc_configured, reconcile_exam
        note = 'Radiographer marked the study uploaded.'
        if orthanc_configured():
            ok, detail = c_echo()
            matched = reconcile_exam(exam) if ok else False
            note = f'{note} PACS {"matched" if matched else "not matched"}: {detail}'
        exam.pacs_uploaded_at = timezone.now()
        exam.pacs_uploaded_by = request.user
        fields = ['pacs_uploaded_at', 'pacs_uploaded_by', 'updated_at']
        if exam.assigned_radiologist_id and not exam.accepted_at and not exam.radiologist_assigned_at:
            exam.radiologist_assigned_at = exam.pacs_uploaded_at
            fields.append('radiologist_assigned_at')
        exam.save(update_fields=fields)
        record_exam_activity(
            exam, action='UPLOAD_PACS', user=request.user,
            from_status='ACQUIRED', to_status='ACQUIRED', note=note, request=request,
        )
        log_event(request, 'RADIOLOGY_PACS_UPLOAD', exam, exam.order.visit.centre)
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object()
        require_centre(request.user, exam.order.visit.centre, 'can_edit')
        require_any_role(request.user, RADIOLOGIST_ROLES, perm='radiology.finalize_report')
        if not exam.pacs_uploaded_at or exam.status != 'ACQUIRED':
            raise ValidationError('Accept is available after the study is uploaded to the PACS server.')
        from radiology.desk import release_expired_assignments
        release_expired_assignments()
        exam.refresh_from_db()
        if exam.assigned_radiologist_id and exam.assigned_radiologist_id != request.user.pk and not request.user.is_superuser:
            raise PermissionDenied('This study is assigned to another radiologist.')
        now = timezone.now()
        if not exam.assigned_radiologist_id:
            exam.assigned_radiologist = request.user
            exam.radiologist_assigned_at = now
        exam.accepted_at = now
        exam.save(update_fields=['assigned_radiologist', 'radiologist_assigned_at', 'accepted_at', 'updated_at'])
        record_exam_activity(
            exam, action='ACCEPT_STUDY', user=request.user,
            from_status=exam.status, to_status=exam.status, request=request,
        )
        log_event(request, 'RADIOLOGY_STUDY_ACCEPTED', exam, exam.order.visit.centre)
        return Response(RadiologyExamSerializer(exam).data)

    @action(detail=True, methods=['post'])
    def reconcile_pacs(self, request, pk=None):
        require_workspace(request.user, 'radiology')
        exam = self.get_object()
        require_centre(request.user, exam.order.visit.centre, 'can_edit')
        from radiology.orthanc import reconcile_exam, c_echo
        ok, detail = c_echo()
        matched = reconcile_exam(exam) if ok else False
        exam.refresh_from_db()
        record_exam_activity(
            exam, action='PACS_RECONCILE', user=request.user,
            from_status=exam.status, to_status=exam.status,
            note=detail if isinstance(detail, str) else ('matched' if matched else 'failed'),
            request=request,
        )
        log_event(request, 'RADIOLOGY_PACS_RECONCILE', exam, exam.order.visit.centre, {'ok': matched, 'echo': detail})
        return Response({
            'matched': matched, 'echo': detail,
            'pacs_study_uid': exam.pacs_study_uid,
            'pacs_reconciled_at': exam.pacs_reconciled_at,
        })


class RadiologyReportViewSet(viewsets.ModelViewSet):
    serializer_class = RadiologyReportSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['status', 'author', 'exam__order__visit__centre']
    search_fields = ['exam__order__visit__visit_id', 'exam__order__visit__patient__uhid', 'findings', 'impression']

    def get_queryset(self):
        qs = RadiologyReport.objects.select_related(
            'exam__order__visit__centre', 'exam__order__visit__patient', 'author', 'template',
        )
        if self.request.user.is_superuser:
            return qs
        return qs.filter(
            exam__order__visit__centre__user_access__user=self.request.user,
            exam__order__visit__centre__user_access__can_view=True,
            exam__order__visit__centre__is_active=True,
        ).distinct()

    def perform_create(self, serializer):
        from radiology.reporting_policy import assert_can_create_report
        require_workspace(self.request.user, 'radiology')
        exam = serializer.validated_data['exam']
        centre = exam.order.visit.centre
        require_centre(self.request.user, centre, 'can_edit')
        # Server-side examination state gate — not UI-only.
        if exam.status != 'ACQUIRED':
            raise ValidationError('Radiographer must end the exam (ACQUIRED) before report entry.')
        if not exam.pacs_uploaded_at:
            raise ValidationError('Upload the study to the PACS server before reporting.')
        if not exam.accepted_at and not self.request.user.is_superuser:
            raise ValidationError('Accept the study before preparing the report.')
        try:
            existing = exam.report
        except ObjectDoesNotExist:
            existing = None
        if existing is not None:
            raise ValidationError('A report already exists for this exam.')
        assert_can_create_report(self.request.user, exam)
        template = serializer.validated_data.get('template')
        if template is None and self.request.data.get('template'):
            template = RadiologyReportTemplate.objects.filter(pk=self.request.data.get('template')).first()
        if template:
            modality_type = exam.order.modality.modality_type
            if template.modality_type != modality_type:
                raise ValidationError({'template': 'Template modality does not match this study.'})
            if template.scope == 'PERSONAL' and template.owner_id not in (None, self.request.user.pk) and not self.request.user.is_superuser:
                raise ValidationError({'template': 'That personal template belongs to another radiologist.'})
            rich_body = (template.body_html or '').strip()
            for field, attr in (
                ('indication', 'indication_boilerplate'),
                ('technique', 'technique_boilerplate'),
                ('findings', 'findings_boilerplate'),
                ('impression', 'impression_boilerplate'),
                ('recommendations', 'recommendations_boilerplate'),
            ):
                if field == 'findings' and rich_body:
                    continue
                if not (serializer.validated_data.get(field) or '').strip():
                    serializer.validated_data[field] = getattr(template, attr, '') or ''
            # Prefer rich HTML body when present (single-document template builder).
            if rich_body and not (serializer.validated_data.get('findings') or '').strip():
                serializer.validated_data['findings'] = template.body_html
            if not serializer.validated_data.get('extra_values') and template.extra_fields:
                extras = {}
                for item in template.extra_fields or []:
                    code = (item or {}).get('code')
                    if code:
                        extras[code] = (item or {}).get('boilerplate') or ''
                serializer.validated_data['extra_values'] = extras
        report = serializer.save(author=self.request.user, template=template)
        record_exam_activity(
            exam, action='REPORT_DRAFT', user=self.request.user,
            from_status=exam.status, to_status=exam.status, request=self.request,
        )
        log_event(self.request, 'RADIOLOGY_REPORT_CREATED', report, centre)

    def perform_update(self, serializer):
        from radiology.reporting_policy import assert_can_edit_unsigned_report
        require_workspace(self.request.user, 'radiology')
        report = self.get_object()
        require_centre(self.request.user, report.centre, 'can_edit')
        if report.exam.status != 'ACQUIRED':
            raise ValidationError('Reporting is blocked until the examination is ACQUIRED on the server.')
        if 'exam' in serializer.validated_data and serializer.validated_data['exam'].pk != report.exam_id:
            raise ValidationError('A report cannot be moved to another exam.')
        if report.status != 'DRAFT':
            raise ValidationError(
                'Signed or released reports require a formal amendment or addendum with an audit trail.'
            )
        assert_can_edit_unsigned_report(self.request.user, report)
        serializer.save()
        log_event(self.request, 'RADIOLOGY_REPORT_EDITED', report, report.centre)

    @action(detail=True, methods=['post'])
    def finalize(self, request, pk=None):
        """Sign the report. Does not release or complete the order."""
        from radiology.reporting_policy import assert_can_sign_report
        require_workspace(request.user, 'radiology')
        report = self.get_object()
        require_centre(request.user, report.centre, 'can_approve')
        assert_can_sign_report(request.user, report)
        if report.exam.status != 'ACQUIRED':
            raise ValidationError('Cannot sign a report until the examination is ACQUIRED.')
        if report.status != 'DRAFT':
            raise ValidationError('Only draft reports can be signed.')
        if not report.findings.strip() or not report.impression.strip():
            raise ValidationError('Findings and impression are required before signing.')
        freeze_report_configuration(report, signer=request.user)
        report.status = 'FINAL'
        report.finalized_at = timezone.now()
        report.finalized_by = request.user
        report.save(update_fields=['status', 'finalized_at', 'finalized_by', 'updated_at'])
        record_exam_activity(
            report.exam, action='REPORT_SIGNED', user=request.user,
            from_status=report.exam.status, to_status=report.exam.status, request=request,
        )
        visit = report.exam.order.visit
        log_event(
            request, 'RADIOLOGY_REPORT_FINALIZED', report, report.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '',
            visit_ref=visit.visit_id,
            order_ref=str(report.exam.order_id),
            result_ref=str(report.pk),
        )
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        """Controlled result release — eligibility checked server-side."""
        require_workspace(request.user, 'radiology')
        report = self.get_object()
        require_centre(request.user, report.centre, 'can_edit')
        require_any_role(request.user, RADIOLOGIST_ROLES, perm='radiology.release_report')
        if report.status != 'FINAL':
            raise ValidationError('Only signed reports can be released.')
        from billing.eligibility import assert_radiology_report_releasable
        assert_radiology_report_releasable(report)
        report.status = 'RELEASED'
        report.released_at = timezone.now()
        report.released_by = request.user
        report.save(update_fields=['status', 'released_at', 'released_by', 'updated_at'])
        order = report.exam.order
        order.status = 'COMPLETED'
        order.save(update_fields=['status', 'updated_at'])
        record_exam_activity(
            report.exam, action='REPORT_RELEASED', user=request.user,
            from_status=report.exam.status, to_status=report.exam.status, request=request,
        )
        visit = report.exam.order.visit
        log_event(
            request, 'RADIOLOGY_REPORT_RELEASED', report, report.centre,
            patient_ref=getattr(visit.patient, 'uhid', '') or '',
            visit_ref=visit.visit_id,
            order_ref=str(report.exam.order_id),
            result_ref=str(report.pk),
        )
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        """Printable PDF using frozen centre format and authenticator signature."""
        require_workspace(request.user, 'radiology')
        report = self.get_object()
        require_centre(request.user, report.centre)
        if report.status not in ('FINAL', 'RELEASED', 'AMENDED'):
            raise ValidationError('Only signed reports can be printed.')
        from radiology.reports import build_radiology_report_pdf
        payload = build_radiology_report_pdf(report)
        visit_id = report.exam.order.visit.visit_id
        response = HttpResponse(payload, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="radiology-{visit_id}-v{report.version}.pdf"'
        return response

    @action(detail=True, methods=['post'])
    def override(self, request, pk=None):
        """Formal amendment after sign/release — audited revision trail."""
        require_workspace(request.user, 'radiology')
        report = self.get_object()
        require_centre(request.user, report.centre, 'can_approve')
        require_any_role(request.user, RADIOLOGIST_ROLES, perm='radiology.override_report')
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A reason is required for a formal amendment.'})
        if report.status not in ('FINAL', 'RELEASED', 'AMENDED'):
            raise ValidationError('Formal amendment is only available for signed or released reports.')
        fields = ('indication', 'technique', 'findings', 'impression', 'recommendations')
        changes = {field: request.data[field] for field in fields if field in request.data}
        if 'extra_values' in request.data and isinstance(request.data.get('extra_values'), dict):
            changes['extra_values'] = request.data['extra_values']
        if not changes:
            raise ValidationError('Supply at least one report field to amend.')
        with transaction.atomic():
            old_version = report.version
            snapshot = {
                'status': report.status,
                'change_type': 'AMENDMENT',
                'indication': report.indication,
                'technique': report.technique,
                'findings': report.findings,
                'impression': report.impression,
                'recommendations': report.recommendations,
                'extra_values': dict(report.extra_values or {}),
                'addendum': report.addendum,
                'version': old_version,
                'finalized_at': report.finalized_at.isoformat() if report.finalized_at else None,
                'configuration_snapshot': report.configuration_snapshot,
            }
            RadiologyReportRevision.objects.create(
                report=report, version=old_version, change_type='AMENDMENT',
                snapshot=snapshot, changed_by=request.user, reason=reason,
            )
            for field, value in changes.items():
                setattr(report, field, value)
            report.version = old_version + 1
            report.status = 'AMENDED'
            report.last_override_by = request.user
            report.last_override_at = timezone.now()
            report.last_override_reason = reason
            report.save(update_fields=[
                *changes.keys(), 'version', 'status', 'last_override_by',
                'last_override_at', 'last_override_reason', 'updated_at',
            ])
            record_exam_activity(
                report.exam, action='REPORT_AMENDMENT', user=request.user,
                from_status=report.exam.status, to_status=report.exam.status,
                note=reason, request=request,
            )
            log_event(request, 'RADIOLOGY_REPORT_AMENDED', report, report.centre, {
                'previous_version': old_version, 'new_version': report.version, 'reason': reason,
            })
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=['post'])
    def addendum(self, request, pk=None):
        """Formal addendum after sign/release — append-only with audit trail."""
        require_workspace(request.user, 'radiology')
        report = self.get_object()
        require_centre(request.user, report.centre, 'can_approve')
        require_any_role(request.user, RADIOLOGIST_ROLES, perm='radiology.add_report_addendum')
        reason = (request.data.get('reason') or '').strip()
        text = (request.data.get('addendum_text') or request.data.get('addendum') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A reason is required for a formal addendum.'})
        if not text:
            raise ValidationError({'addendum_text': 'Addendum text is required.'})
        if report.status not in ('FINAL', 'RELEASED', 'AMENDED'):
            raise ValidationError('Addenda are only available for signed or released reports.')
        with transaction.atomic():
            old_version = report.version
            snapshot = {
                'status': report.status,
                'change_type': 'ADDENDUM',
                'indication': report.indication,
                'technique': report.technique,
                'findings': report.findings,
                'impression': report.impression,
                'recommendations': report.recommendations,
                'extra_values': dict(report.extra_values or {}),
                'addendum': report.addendum,
                'version': old_version,
            }
            RadiologyReportRevision.objects.create(
                report=report, version=old_version, change_type='ADDENDUM',
                snapshot=snapshot, changed_by=request.user, reason=reason,
            )
            stamp = timezone.now().strftime('%Y-%m-%d %H:%M')
            block = f'[{stamp} — {request.user.get_username()} — {reason}]\n{text}'
            report.addendum = (report.addendum.strip() + '\n\n' + block).strip() if report.addendum.strip() else block
            report.version = old_version + 1
            report.status = 'AMENDED'
            report.last_override_by = request.user
            report.last_override_at = timezone.now()
            report.last_override_reason = reason
            report.save(update_fields=[
                'addendum', 'version', 'status', 'last_override_by',
                'last_override_at', 'last_override_reason', 'updated_at',
            ])
            record_exam_activity(
                report.exam, action='REPORT_ADDENDUM', user=request.user,
                from_status=report.exam.status, to_status=report.exam.status,
                note=reason, request=request,
            )
            log_event(request, 'RADIOLOGY_REPORT_ADDENDUM', report, report.centre, {
                'previous_version': old_version, 'new_version': report.version, 'reason': reason,
            })
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=['get'])
    def revisions(self, request, pk=None):
        report = self.get_object()
        return Response(RadiologyReportRevisionSerializer(report.revisions.all(), many=True).data)


class RadiologyReportTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RadiologyReportTemplateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['modality_type', 'scope', 'status', 'owner']

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        qs = RadiologyReportTemplate.objects.filter(is_active=True)
        if user.is_superuser:
            return qs
        return qs.filter(Q(scope='PLATFORM', owner__isnull=True) | Q(scope='PERSONAL', owner=user))

    @action(detail=False, methods=['get'])
    def for_modality(self, request):
        modality_type = (request.query_params.get('modality_type') or '').upper()
        return Response(RadiologyReportTemplateSerializer(
            resolve_report_templates(modality_type, request.user), many=True,
        ).data)
