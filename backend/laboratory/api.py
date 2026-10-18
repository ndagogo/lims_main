from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound
from core.access import LAB_MANAGER_ROLES, require_any_role, require_workspace
from core.models import UserCentreAccess, AuditEvent
from core.permissions import HasCentreAccess
from patients.models import PatientDocument
from billing.models import InvoiceItem
from .models import (Test,Tariff,TestPrice,LabOrder,Sample,SampleActivity,SampleBarcodePrint,SampleBatch,BatchSample,SampleProcessing,TestParameter,ResultTemplate,LabResult,ResultValue,ResultReview)
from .serializers import (TestSerializer,TariffSerializer,TestPriceSerializer,LabOrderSerializer,SampleSerializer,SampleActivitySerializer,SampleBatchSerializer,SampleProcessingSerializer,TestParameterSerializer,ResultTemplateSerializer,LabResultSerializer,ResultReviewSerializer)
from .workflow_gate import enforce_workflow

def allowed(user, centre, capability='can_view'):
    return user.is_superuser or UserCentreAccess.objects.filter(user=user,centre=centre,**{capability:True}).exists()
def require(user, centre, capability='can_edit'):
    if not allowed(user,centre,capability): raise PermissionDenied(f'You do not have {capability.replace("can_", "")} permission at this centre.')
def request_meta(request):
    from core.access import client_browser, client_ip
    return client_ip(request), client_browser(request)
def activity(sample,request,action,to_status=None,note=''):
    ip,browser=request_meta(request)
    SampleActivity.objects.create(sample=sample,action=action,from_status=sample.status,to_status=to_status or sample.status,note=note,performed_by=request.user,ip_address=ip,browser=browser)
    AuditEvent.objects.create(actor=request.user,action=f'LAB_{action}',object_type='Sample',object_id=str(sample.pk),path=request.path,method=request.method,ip_address=ip,browser=browser,centre=sample.centre,detail={'sample_id':sample.sample_id,'from_status':sample.status,'to_status':to_status or sample.status})
def set_status(sample,request,new_status,action,note=''):
    old=sample.status; sample.status=new_status; sample.save(update_fields=['status','updated_at'])
    ip,browser=request_meta(request)
    SampleActivity.objects.create(sample=sample,action=action,from_status=old,to_status=new_status,note=note,performed_by=request.user,ip_address=ip,browser=browser)
    AuditEvent.objects.create(actor=request.user,action=f'LAB_{action}',object_type='Sample',object_id=str(sample.pk),path=request.path,method=request.method,ip_address=ip,browser=browser,centre=sample.centre,detail={'sample_id':sample.sample_id,'from_status':old,'to_status':new_status,'note':note})
def scoped_samples(user):
    qs=Sample.objects.select_related('centre','order__visit__patient','order__test').all()
    if user.is_superuser:return qs
    return qs.filter(centre__user_access__user=user,centre__user_access__can_view=True,centre__is_active=True).distinct()

def assert_sample_actionable(sample, *, allow_rejected=False):
    """Block further workflow on released/cancelled/superseded (and rejected unless allowed)."""
    terminal = set(Sample.TERMINAL_STATUSES)
    if allow_rejected:
        terminal.discard('REJECTED')
    if sample.status in terminal:
        raise ValidationError(
            f'Sample {sample.sample_id} is {sample.get_status_display().lower()} and cannot continue this workflow.'
        )

def record_barcode_print(sample, request, *, source='LABEL_PAGE'):
    """Append a print-history row; does not change sample status."""
    ip, browser = request_meta(request)
    source = (source or 'LABEL_PAGE').upper()
    if source not in {c for c, _ in SampleBarcodePrint.SOURCES}:
        source = 'MANUAL'
    row = SampleBarcodePrint.objects.create(
        sample=sample,
        barcode_value=sample.sample_id,
        printed_by=getattr(request, 'user', None) if getattr(request, 'user', None) and request.user.is_authenticated else None,
        printed_at=timezone.now(),
        source=source,
        ip_address=ip,
        browser=browser or '',
    )
    AuditEvent.objects.create(
        actor=row.printed_by, action='LAB_BARCODE_PRINTED', object_type='Sample',
        object_id=str(sample.pk), path=getattr(request, 'path', ''), method=getattr(request, 'method', 'GET'),
        ip_address=ip, browser=browser, centre=sample.centre,
        detail={'sample_id': sample.sample_id, 'source': source, 'print_id': row.pk},
    )
    return row

def _queue_result_ready(result, user):
    from communications.messaging import EVENT_RESULT_READY, notify_patient
    visit = result.sample.order.visit
    notify_patient(
        event_key=EVENT_RESULT_READY,
        visit=visit,
        user=user,
        idempotency_key=f'result-ready:{result.pk}',
        extra={
            'test_name': result.sample.order.test.name,
            'sample_id': result.sample.sample_id,
        },
    )

def assert_paid(order):
    # Collection is permitted only after a linked invoice is paid and has this order as a line item.
    if not InvoiceItem.objects.filter(lab_order=order,invoice__status='PAID').exists():
        raise ValidationError('A confirmed paid invoice containing this test order is required before collection.')

class TestViewSet(viewsets.ReadOnlyModelViewSet):
    queryset=Test.objects.filter(is_active=True); serializer_class=TestSerializer; permission_classes=[IsAuthenticated]; search_fields=['code','name']; filterset_fields=['department','service_group']
class TariffViewSet(viewsets.ReadOnlyModelViewSet):
    queryset=Tariff.objects.filter(is_active=True); serializer_class=TariffSerializer; permission_classes=[IsAuthenticated]; search_fields=['name']
class TestPriceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset=TestPrice.objects.filter(tariff__is_active=True,test__is_active=True).select_related('test','tariff'); serializer_class=TestPriceSerializer; permission_classes=[IsAuthenticated]; filterset_fields=['tariff','test']
class LabOrderViewSet(viewsets.ModelViewSet):
    serializer_class=LabOrderSerializer; permission_classes=[IsAuthenticated]; filterset_fields=['status','visit','test']
    def get_queryset(self):
        qs=LabOrder.objects.select_related('visit__centre','visit__patient','test').all()
        return qs if self.request.user.is_superuser else qs.filter(visit__centre__user_access__user=self.request.user,visit__centre__user_access__can_view=True,visit__centre__is_active=True).distinct()
    def perform_create(self,serializer):
        visit=serializer.validated_data['visit']; require(self.request.user,visit.centre,'can_register')
        order = serializer.save()
        try:
            from workflows.services import bind_workflow
            bind_workflow(order)
        except Exception:
            pass
    @action(detail=True,methods=['post'],url_path='collect')
    def collect(self,request,pk=None):
        require_workspace(request.user, 'phlebotomy', 'laboratory')
        order=self.get_object(); require(request.user,order.visit.centre,'can_edit'); assert_paid(order)
        if order.status=='CANCELLED': raise ValidationError('Cancelled orders cannot be collected.')
        centre=order.visit.centre
        if not hasattr(centre,'sample_code_config'): raise ValidationError('Configure a numeric sample code for this centre before collection.')
        ip,browser=request_meta(request)
        with transaction.atomic():
            enforce_workflow(order, 'collect', user=request.user, payload={'collector': request.user.get_username()})
            sample=Sample(
                order=order, centre=centre, collected_by=request.user, collected_at=timezone.now(),
                collector_ip=ip, collector_browser=browser, status='COLLECTED',
                specimen_type=(order.test.specimen or '')[:80],
            )
            sample.save()
            order.status='IN_PROGRESS'; order.save(update_fields=['status','updated_at'])
            activity(sample,request,'COLLECTED','COLLECTED',request.data.get('note',''))
        return Response(SampleSerializer(sample,context={'request':request}).data,status=status.HTTP_201_CREATED)
class SampleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=SampleSerializer; permission_classes=[IsAuthenticated]; search_fields=['sample_id','order__visit__visit_id','order__visit__patient__phone_number']; filterset_fields=['status','centre','order__test']
    def get_queryset(self): return scoped_samples(self.request.user)
    @action(detail=True,methods=['post'])
    def recollect(self,request,pk=None):
        """Create a new collected sample and supersede (or leave rejected) the original."""
        require_workspace(request.user, 'phlebotomy', 'laboratory')
        old=self.get_object(); require(request.user,old.centre,'can_edit')
        if old.status in ('RELEASED', 'CANCELLED', 'SUPERSEDED'):
            raise ValidationError('Released, cancelled, or already superseded samples cannot be recollected.')
        assert_paid(old.order)
        reason=(request.data.get('reason') or '').strip() or 'Recollection'
        corrective=(request.data.get('corrective_action') or '').strip()
        disposition=(request.data.get('disposition') or 'SUPERSEDED').strip().upper()
        if disposition not in ('SUPERSEDED', 'REJECTED', 'UNSUITABLE'):
            disposition = 'SUPERSEDED'
        ip,browser=request_meta(request)
        with transaction.atomic():
            new=Sample(
                order=old.order, centre=old.centre, recollection_of=old,
                collected_by=request.user, collected_at=timezone.now(),
                collector_ip=ip, collector_browser=browser, status='COLLECTED',
                specimen_type=(old.specimen_type or old.order.test.specimen or '')[:80],
            )
            new.save()
            # Supersede the original so it cannot continue batching / results / release.
            if old.status != 'REJECTED':
                old_status = old.status
                old.status = 'SUPERSEDED'
                old.disposition = disposition
                old.rejection_reason = reason
                if corrective:
                    old.corrective_action = corrective
                old.rejected_by = request.user
                old.rejected_at = timezone.now()
                old.save(update_fields=[
                    'status', 'disposition', 'rejection_reason', 'corrective_action',
                    'rejected_by', 'rejected_at', 'updated_at',
                ])
                SampleActivity.objects.create(
                    sample=old, action='SUPERSEDED', from_status=old_status, to_status='SUPERSEDED',
                    note=f'{reason} → new sample {new.sample_id}', performed_by=request.user,
                    ip_address=ip, browser=browser,
                )
                AuditEvent.objects.create(
                    actor=request.user, action='LAB_SUPERSEDED', object_type='Sample',
                    object_id=str(old.pk), path=request.path, method=request.method,
                    ip_address=ip, browser=browser, centre=old.centre,
                    detail={
                        'sample_id': old.sample_id, 'from_status': old_status, 'to_status': 'SUPERSEDED',
                        'replacement_sample_id': new.sample_id, 'disposition': disposition, 'note': reason,
                    },
                )
            elif corrective and not old.corrective_action:
                old.corrective_action = corrective
                old.save(update_fields=['corrective_action', 'updated_at'])
            activity(new, request, 'RECOLLECTED', 'COLLECTED', f'{reason} (replaces {old.sample_id})')
        try:
            from communications.messaging import EVENT_RECOLLECTION, notify_patient, notify_recollection_staff
            notify_patient(
                event_key=EVENT_RECOLLECTION,
                visit=old.order.visit,
                user=request.user,
                idempotency_key=f'recollection:{new.pk}',
                extra={
                    'test_name': old.order.test.name,
                    'sample_id': new.sample_id,
                    'reason': reason,
                },
            )
            notify_recollection_staff(old.order.visit, user=request.user, sample_id=new.sample_id, reason=reason)
        except Exception:
            pass
        return Response(SampleSerializer(new,context={'request':request}).data,status=201)
    @action(detail=True,methods=['get'])
    def barcode(self,request,pk=None):
        sample=self.get_object()
        if request.query_params.get('format') == 'svg':
            try:
                import barcode
                from barcode.writer import SVGWriter
                from django.http import HttpResponse
                svg=barcode.get('code128', sample.sample_id, writer=SVGWriter()).render({'write_text': True, 'module_height': 12, 'module_width': 0.35, 'font_size': 10, 'text_distance': 2})
                return HttpResponse(svg, content_type='image/svg+xml')
            except ImportError:
                raise ValidationError('Install the python-barcode dependency to render SVG labels.')
        prints = list(sample.barcode_prints.order_by('-printed_at')[:20].values(
            'id', 'barcode_value', 'printed_at', 'source', 'printed_by_id',
        ))
        return Response({
            'sample_id':sample.sample_id,'symbology':'Code128',
            'barcode_svg_url':request.build_absolute_uri(request.path+'?format=svg'),
            'human_readable':sample.sample_id,'visit_id':sample.order.visit.visit_id,
            'patient_uhid':sample.order.visit.patient.uhid,'test':sample.order.test.name,
            'centre':sample.centre.name,'print_history':prints,
        })
    @action(detail=True, methods=['post'], url_path='record-print')
    def record_print(self, request, pk=None):
        """Log a barcode print event without changing sample lifecycle."""
        require_workspace(request.user, 'phlebotomy', 'laboratory')
        sample = self.get_object()
        require(request.user, sample.centre, 'can_view')
        source = (request.data.get('source') or 'API').strip().upper()
        row = record_barcode_print(sample, request, source=source)
        return Response({
            'id': row.pk, 'sample_id': sample.sample_id, 'barcode_value': row.barcode_value,
            'printed_at': row.printed_at, 'source': row.source,
        }, status=201)
    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        require_workspace(request.user, 'laboratory', 'phlebotomy')
        from .tat import sample_timeline_payload
        return Response(sample_timeline_payload(self.get_object()))
    @action(detail=True, methods=['post'], url_path='tat-pause')
    def tat_pause(self, request, pk=None):
        require_workspace(request.user, 'laboratory')
        from .models import TatPause, TatPauseReason
        from .tat import resolve_tat_rule
        sample = self.get_object()
        require(request.user, sample.centre, 'can_edit')
        rule = resolve_tat_rule(sample.order.test)
        if rule and not rule.allow_pause:
            raise ValidationError('TAT pauses are not allowed for this test rule.')
        if sample.tat_pauses.filter(ended_at__isnull=True).exists():
            raise ValidationError('This sample already has an open TAT pause. End it before starting another.')
        reason_id = request.data.get('reason')
        try:
            reason = TatPauseReason.objects.get(pk=reason_id, is_active=True)
        except (TatPauseReason.DoesNotExist, TypeError, ValueError):
            raise ValidationError({'reason': 'Choose an active pause reason.'})
        ip, browser = request_meta(request)
        pause = TatPause.objects.create(
            sample=sample, reason=reason, note=(request.data.get('note') or '').strip(),
            started_by=request.user, start_ip=ip, start_browser=browser,
        )
        activity(sample, request, 'TAT_PAUSE_START', sample.status, f'{reason.code}: {pause.note}')
        return Response({'id': str(pause.pk), 'reason': reason.name, 'started_at': pause.started_at}, status=201)
    @action(detail=True, methods=['post'], url_path='tat-resume')
    def tat_resume(self, request, pk=None):
        require_workspace(request.user, 'laboratory')
        sample = self.get_object()
        require(request.user, sample.centre, 'can_edit')
        pause = sample.tat_pauses.filter(ended_at__isnull=True).order_by('-started_at').first()
        if not pause:
            raise ValidationError('No open TAT pause exists for this sample.')
        ip, browser = request_meta(request)
        pause.ended_at = timezone.now()
        pause.ended_by = request.user
        pause.end_ip = ip
        pause.end_browser = browser
        if request.data.get('note'):
            pause.note = (pause.note + '\n' + str(request.data.get('note'))).strip()
        pause.save(update_fields=['ended_at', 'ended_by', 'end_ip', 'end_browser', 'note', 'updated_at'])
        activity(sample, request, 'TAT_PAUSE_END', sample.status, pause.reason.code)
        return Response({'id': str(pause.pk), 'ended_at': pause.ended_at})
    @action(detail=True,methods=['post'])
    def reject(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        if sample.status in Sample.TERMINAL_STATUSES:
            raise ValidationError('This sample can no longer be rejected.')
        reason=(request.data.get('reason') or '').strip()
        if not reason: raise ValidationError({'reason':'A rejection reason is required.'})
        corrective=(request.data.get('corrective_action') or '').strip()
        disposition=(request.data.get('disposition') or 'REJECTED').strip().upper()
        if disposition not in ('REJECTED', 'UNSUITABLE'):
            disposition = 'REJECTED'
        sample.rejection_reason = reason
        sample.disposition = disposition
        sample.corrective_action = corrective
        sample.rejected_by = request.user
        sample.rejected_at = timezone.now()
        sample.save(update_fields=[
            'rejection_reason', 'disposition', 'corrective_action',
            'rejected_by', 'rejected_at', 'updated_at',
        ])
        note = reason if not corrective else f'{reason} | corrective: {corrective}'
        set_status(sample, request, 'REJECTED', 'REJECTED', note)
        return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def receive(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        assert_sample_actionable(sample)
        if sample.status!='BATCHED': raise ValidationError('Only batched samples can be received by a department.')
        if not sample.batch_memberships.filter(batch__status='RECEIVED').exists(): raise ValidationError('The containing batch must be received by the department first.')
        enforce_workflow(sample.order, 'receive', user=request.user)
        set_status(sample,request,'RECEIVED','DEPARTMENT_RECEIVED',request.data.get('note','')); return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def start_processing(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        assert_sample_actionable(sample)
        if sample.status!='RECEIVED': raise ValidationError('Sample must be received by the department first.')
        with transaction.atomic():
            enforce_workflow(sample.order, 'start', user=request.user)
            SampleProcessing.objects.create(sample=sample,action='PROCESSING_STARTED',notes=request.data.get('notes',''),performed_by=request.user)
            set_status(sample,request,'PROCESSING','PROCESSING_STARTED',request.data.get('notes',''))
        return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def complete_processing(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        assert_sample_actionable(sample)
        if sample.status!='PROCESSING': raise ValidationError('Only samples in processing can be completed.')
        record=sample.processing_records.filter(completed_at__isnull=True).order_by('-started_at').first()
        if not record: raise ValidationError('No open processing record exists for this sample.')
        record.completed_at=timezone.now(); record.notes=(record.notes+'\n'+request.data.get('notes','')).strip(); record.save(update_fields=['completed_at','notes','updated_at'])
        activity(sample,request,'PROCESSING_COMPLETED',sample.status,request.data.get('notes',''))
        return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def enter_result(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        assert_sample_actionable(sample)
        if sample.status not in ('PROCESSING', 'RESULT_ENTERED'):
            raise ValidationError('Sample must be in processing (or have a draft result) before result entry.')
        if sample.processing_records.filter(completed_at__isnull=True).exists(): raise ValidationError('Complete the current processing activity before entering results.')
        try:
            existing_result = sample.result
        except ObjectDoesNotExist:
            existing_result = None
        if sample.status == 'RESULT_ENTERED' and (not existing_result or existing_result.status != 'DRAFT'):
            raise ValidationError('Only draft results can be corrected here; reviewed/authenticated results require a controlled amendment workflow.')
        if existing_result and existing_result.status not in ['DRAFT']:
            raise ValidationError('Only draft results can be corrected here; reviewed/authenticated results require a controlled amendment workflow.')
        data=request.data; template=None
        from .templates_svc import resolve_entry_template, template_parameters
        if data.get('template'):
            try:
                template=ResultTemplate.objects.get(pk=data['template'],test=sample.order.test,is_active=True)
            except ResultTemplate.DoesNotExist:
                raise ValidationError({'template':'Template does not belong to this test.'})
        else:
            template = resolve_entry_template(sample.order.test)
        values=list(data.get('values') or [])
        narrative=(data.get('narrative') or '').strip()
        if sample.order.test.comments_required and not narrative:
            raise ValidationError({'narrative': 'This test requires a clinical comment / narrative.'})
        catalogue = list(sample.order.test.parameters.all())
        from .services import apply_calculated_parameters, classify_value, format_numeric
        apply_calculated_parameters(catalogue, values)
        if not values and not narrative:
            raise ValidationError('Enter at least one parameter value or a narrative before saving.')
        with transaction.atomic():
            if not existing_result:
                enforce_workflow(sample.order, 'enter_result', user=request.user, payload={'narrative_or_values': True})
            if existing_result:
                result=existing_result; result.template=template; result.narrative=narrative; result.entered_by=request.user; result.save(update_fields=['template','narrative','entered_by','updated_at'])
                result.values.all().delete()
            else:
                result=LabResult.objects.create(sample=sample,template=template,narrative=narrative,entered_by=request.user,status='DRAFT')
            for row in values:
                try: parameter=TestParameter.objects.get(pk=row['parameter'],test=sample.order.test)
                except (TestParameter.DoesNotExist,KeyError,TypeError,ValueError): raise ValidationError({'values':'Each parameter must belong to the sample test.'})
                if parameter.result_type == 'SECTION':
                    continue
                raw_value = str(row.get('value', '')).strip()
                if parameter.result_type in ('NUMBER', 'TABLE', 'CALCULATED') and raw_value:
                    raw_value = format_numeric(raw_value, parameter.decimal_places)
                if parameter.choices and parameter.result_type in ('CHOICE', 'QUALITATIVE', 'BOOLEAN'):
                    allowed = {str(c) for c in parameter.choices}
                    if raw_value and raw_value not in allowed:
                        raise ValidationError({'values': f'{parameter.code} must be one of: {", ".join(sorted(allowed))}.'})
                if parameter.choices and parameter.result_type == 'MULTI_CHOICE' and raw_value:
                    allowed = {str(c) for c in parameter.choices}
                    selected = [part.strip() for part in raw_value.split(',') if part.strip()]
                    bad = [part for part in selected if part not in allowed]
                    if bad:
                        raise ValidationError({'values': f'{parameter.code} has invalid choice(s): {", ".join(bad)}.'})
                    raw_value = ', '.join(selected)
                if parameter.result_type == 'BOOLEAN' and not parameter.choices and raw_value:
                    if raw_value.upper() in ('Y', 'YES', 'TRUE', '1'):
                        raw_value = 'Yes'
                    elif raw_value.upper() in ('N', 'NO', 'FALSE', '0'):
                        raw_value = 'No'
                raw_flag=str(row.get('flag') or '')
                range_text = ''
                if not raw_flag:
                    raw_flag, range_text = classify_value(parameter, raw_value, sample.order.visit.patient)
                else:
                    _, range_text = classify_value(parameter, raw_value, sample.order.visit.patient)
                comment = str(row.get('comment') or '').strip()
                if raw_flag.startswith('CRITICAL') and parameter.critical_comment_required and not comment:
                    raise ValidationError({'values': f'A comment is required for critical {parameter.code}.'})
                if raw_flag in ('LOW', 'HIGH', 'CRITICAL_LOW', 'CRITICAL_HIGH') and parameter.require_comment_if_abnormal and not comment:
                    raise ValidationError({'values': f'A comment is required for abnormal {parameter.code}.'})
                ResultValue.objects.update_or_create(
                    result=result, parameter=parameter,
                    defaults={
                        'value': raw_value,
                        'flag': raw_flag[:20],
                        'comment': comment,
                        'reference_display': (row.get('reference_display') or range_text or parameter.reference_range or '')[:120],
                    },
                )
            # Required set from published template fields when available.
            required_ids = set()
            for parameter, field in template_parameters(template, sample.order.test):
                if parameter.result_type in ('SECTION', 'CALCULATED'):
                    continue
                needed = field.is_required if field and field.is_required is not None else parameter.is_required
                if needed:
                    required_ids.add(parameter.pk)
            supplied=set(result.values.exclude(value='').values_list('parameter_id', flat=True))
            missing = required_ids - supplied
            if missing:
                codes = list(TestParameter.objects.filter(pk__in=missing).values_list('code', flat=True))
                raise ValidationError({'values': 'Missing required parameter values: ' + ', '.join(codes or map(str, missing))})
            set_status(sample,request,'RESULT_ENTERED','RESULT_ENTERED')
        return Response(LabResultSerializer(result,context={'request':request}).data,status=201)
class SampleBatchViewSet(viewsets.ModelViewSet):
    serializer_class=SampleBatchSerializer; permission_classes=[IsAuthenticated]; http_method_names=['get','post','head','options']; filterset_fields=['centre','department','status']
    def get_queryset(self):
        qs=SampleBatch.objects.prefetch_related('items__sample').all()
        return qs if self.request.user.is_superuser else qs.filter(centre__user_access__user=self.request.user,centre__user_access__can_view=True).distinct()
    def perform_create(self,serializer):
        require_workspace(self.request.user, 'laboratory')
        centre=serializer.validated_data['centre']; require(self.request.user,centre,'can_edit')
        ids=serializer.validated_data.pop('sample_ids', self.request.data.get('sample_ids', []))
        if not ids: raise ValidationError({'sample_ids':'Add at least one sample to a batch.'})
        samples=list(scoped_samples(self.request.user).filter(sample_id__in=ids,centre=centre))
        if len(samples)!=len(set(ids)): raise ValidationError('One or more samples are unavailable at this centre.')
        if any(s.status!='COLLECTED' for s in samples): raise ValidationError('Only collected samples can be batched.')
        if any(s.order.test.department != serializer.validated_data['department'] for s in samples): raise ValidationError('All samples in a batch must belong to the selected department.')
        with transaction.atomic():
            batch=serializer.save(created_by=self.request.user)
            for sample in samples:
                enforce_workflow(sample.order, 'batch', user=self.request.user, payload={'collector': self.request.user.get_username()})
                BatchSample.objects.create(batch=batch,sample=sample,added_by=self.request.user); set_status(sample,self.request,'BATCHED','BATCHED',batch.batch_code)
    @action(detail=True, methods=['post'], url_path='dispatch')
    def dispatch_batch(self, request, pk=None):
        require_workspace(request.user, 'laboratory')
        batch=self.get_object(); require(request.user,batch.centre,'can_edit')
        if batch.status!='OPEN' or not batch.items.exists(): raise ValidationError('Only non-empty open batches can be dispatched.')
        with transaction.atomic():
            batch.status='DISPATCHED'; batch.dispatched_at=timezone.now(); batch.save(update_fields=['status','dispatched_at','updated_at'])
            for item in batch.items.select_related('sample'):
                if item.sample.status == 'BATCHED':
                    activity(item.sample, request, 'DISPATCHED', 'BATCHED', batch.batch_code)
        return Response(SampleBatchSerializer(batch).data)
    @action(detail=True,methods=['post'])
    def receive(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        batch=self.get_object(); require(request.user,batch.centre,'can_edit')
        if batch.status!='DISPATCHED': raise ValidationError('Only dispatched batches can be received.')
        with transaction.atomic():
            batch.status='RECEIVED'; batch.received_at=timezone.now(); batch.received_by=request.user; batch.save(update_fields=['status','received_at','received_by','updated_at'])
            for item in batch.items.select_related('sample__order'):
                if item.sample.status=='BATCHED':
                    assert_sample_actionable(item.sample)
                    enforce_workflow(item.sample.order, 'receive', user=request.user)
                    set_status(item.sample,request,'RECEIVED','DEPARTMENT_RECEIVED',batch.batch_code)
        return Response(SampleBatchSerializer(batch).data)
class TestParameterViewSet(viewsets.ReadOnlyModelViewSet): queryset=TestParameter.objects.all(); serializer_class=TestParameterSerializer; permission_classes=[IsAuthenticated]; filterset_fields=['test']
class ResultTemplateViewSet(viewsets.ReadOnlyModelViewSet): queryset=ResultTemplate.objects.filter(is_active=True); serializer_class=ResultTemplateSerializer; permission_classes=[IsAuthenticated]; filterset_fields=['test']
class LabResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=LabResultSerializer; permission_classes=[IsAuthenticated]; filterset_fields=['status','sample__centre','sample__order__test']; search_fields=['sample__sample_id','sample__order__visit__visit_id']
    def get_queryset(self):
        qs=LabResult.objects.select_related('sample__centre','sample__order__test','sample__order__visit__patient').prefetch_related('values__parameter')
        return qs if self.request.user.is_superuser else qs.filter(sample__centre__user_access__user=self.request.user,sample__centre__user_access__can_view=True).distinct()
    @action(detail=True,methods=['post'])
    def submit_review(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        result=self.get_object(); require(request.user,result.sample.centre,'can_edit')
        if result.status!='DRAFT': raise ValidationError('Only draft results can be submitted for review.')
        # Re-submit after a rejected review stays on the REVIEW workflow step.
        already_in_review = False
        try:
            from workflows.models import OrderWorkflowInstance
            inst = OrderWorkflowInstance.objects.filter(lab_order=result.sample.order).select_related('current_step').first()
            already_in_review = bool(inst and inst.current_step and inst.current_step.code == 'REVIEW')
        except ImportError:
            already_in_review = False
        if not already_in_review:
            enforce_workflow(result.sample.order, 'submit', user=request.user, payload={'narrative_or_values': True})
        result.status='SUBMITTED'; result.save(update_fields=['status','updated_at']); ResultReview.objects.create(result=result,action='SUBMITTED',reviewer=request.user,note=request.data.get('note',''))
        set_status(result.sample,request,'UNDER_REVIEW','SUBMITTED_FOR_REVIEW')
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def review(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        result=self.get_object(); require(request.user,result.sample.centre,'can_approve')
        if result.status!='SUBMITTED': raise ValidationError('Only submitted results can be reviewed.')
        if result.entered_by_id==request.user.id: raise PermissionDenied('The result author cannot perform the first review of their own result.')
        approve=request.data.get('decision')=='APPROVE'; note=(request.data.get('note') or '').strip()
        if not approve and not note: raise ValidationError({'note':'A reason is required when rejecting a result.'})
        result.reviewed_by=request.user; result.reviewed_at=timezone.now(); result.status='REVIEWED' if approve else 'DRAFT'; result.save(update_fields=['reviewed_by','reviewed_at','status','updated_at'])
        ResultReview.objects.create(result=result,action='APPROVED' if approve else 'REJECTED',reviewer=request.user,note=note)
        set_status(result.sample,request,'UNDER_REVIEW' if approve else 'RESULT_ENTERED','RESULT_REVIEWED' if approve else 'RESULT_REJECTED',note)
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def authenticate(self,request,pk=None):
        result=self.get_object(); require(request.user,result.sample.centre,'can_approve')
        require_any_role(request.user, LAB_MANAGER_ROLES, perm='laboratory.authenticate_labresult')
        if result.status!='REVIEWED': raise ValidationError('Only reviewed results can be authenticated.')
        if result.entered_by_id==request.user.id: raise PermissionDenied('The result author cannot authenticate their own result.')
        if result.reviewed_by_id==request.user.id: raise PermissionDenied('The first reviewer cannot authenticate the same result.')
        enforce_workflow(result.sample.order, 'authenticate', user=request.user)
        from .templates_svc import freeze_result_configuration
        freeze_result_configuration(result, signer=request.user)
        result.authenticated_by=request.user; result.authenticated_at=timezone.now(); result.status='AUTHENTICATED'
        result.save(update_fields=['authenticated_by','authenticated_at','status','updated_at'])
        ResultReview.objects.create(result=result,action='AUTHENTICATED',reviewer=request.user,note=request.data.get('note',''))
        set_status(result.sample,request,'AUTHENTICATED','RESULT_AUTHENTICATED')
        from communications.messaging import notify_result_authenticated
        notify_result_authenticated(result, user=request.user)
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def release(self,request,pk=None):
        result=self.get_object(); require(request.user,result.sample.centre,'can_edit')
        require_any_role(request.user, LAB_MANAGER_ROLES, perm='laboratory.release_labresult')
        if result.status!='AUTHENTICATED': raise ValidationError('Only authenticated results can be released.')
        if result.authenticated_by_id==request.user.id: raise PermissionDenied('The authenticating user cannot release the same result.')
        from billing.eligibility import assert_lab_result_releasable
        assert_lab_result_releasable(result)
        enforce_workflow(result.sample.order, 'release', user=request.user)
        result.released_by=request.user; result.released_at=timezone.now(); result.status='RELEASED'; result.save(update_fields=['released_by','released_at','status','updated_at'])
        ResultReview.objects.create(result=result,action='RELEASED',reviewer=request.user,note=request.data.get('note',''))
        set_status(result.sample,request,'RELEASED','RESULT_RELEASED')
        result.sample.order.status='COMPLETED'; result.sample.order.save(update_fields=['status','updated_at'])
        _queue_result_ready(result, request.user)
        return Response(LabResultSerializer(result).data)
    @action(detail=True, methods=['post'])
    def amend(self, request, pk=None):
        result=self.get_object()
        require(request.user, result.sample.centre, 'can_approve')
        require_any_role(request.user, LAB_MANAGER_ROLES, perm='laboratory.amend_labresult')
        if result.status not in ('RELEASED', 'AMENDED'):
            raise ValidationError('Only a released result can be amended.')
        reason=(request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A reason is required to amend a released result.'})
        snapshot={'status': result.status, 'narrative': result.narrative, 'version': result.version, 'values': [{'parameter_id': row.parameter_id, 'value': row.value, 'flag': row.flag} for row in result.values.all()]}
        with transaction.atomic():
            ResultReview.objects.create(result=result, action='AMENDED', reviewer=request.user, note=reason, snapshot=snapshot)
            if 'narrative' in request.data:
                result.narrative=request.data.get('narrative') or ''
            result.version += 1
            result.status='AMENDED'
            result.save(update_fields=['narrative', 'version', 'status', 'updated_at'])
            for row in request.data.get('values') or []:
                try:
                    parameter=TestParameter.objects.get(pk=row['parameter'], test=result.sample.order.test)
                except (TestParameter.DoesNotExist, KeyError, TypeError):
                    raise ValidationError({'values': 'Each parameter must belong to the sample test.'})
                ResultValue.objects.update_or_create(result=result, parameter=parameter, defaults={'value': str(row.get('value', '')), 'flag': str(row.get('flag', ''))[:20]})
            activity(result.sample, request, 'RESULT_AMENDED', result.sample.status, reason)
        return Response(LabResultSerializer(result).data)
    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        result=self.get_object()
        if result.status not in ('RELEASED', 'AMENDED'):
            raise ValidationError('Only a released result can be printed.')
        from django.http import HttpResponse
        from .reports import build_result_pdf
        payload=build_result_pdf(result)
        response=HttpResponse(payload, content_type='application/pdf')
        response['Content-Disposition']=f'inline; filename="{result.sample.sample_id}.pdf"'
        return response
    @action(detail=True,methods=['get'])
    def timeline(self,request,pk=None):
        from .tat import sample_timeline_payload
        result=self.get_object()
        return Response(sample_timeline_payload(result.sample))
class SampleActivityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=SampleActivitySerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self):
        qs=SampleActivity.objects.select_related('sample__centre','performed_by')
        return qs if self.request.user.is_superuser else qs.filter(sample__centre__user_access__user=self.request.user,sample__centre__user_access__can_view=True).distinct()


from .models import QcMaterial, QcRun, TatEscalation, TatPauseReason, TatRule
from .serializers import (
    QcMaterialSerializer, QcRunSerializer, TatEscalationSerializer,
    TatPauseReasonSerializer, TatRuleSerializer,
)
from .tat import tat_dashboard_payload


class QcMaterialViewSet(viewsets.ModelViewSet):
    serializer_class = QcMaterialSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['centre', 'test', 'is_active']

    def get_queryset(self):
        qs = QcMaterial.objects.select_related('centre', 'test')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(centre__user_access__user=self.request.user, centre__user_access__can_view=True).distinct()


class QcRunViewSet(viewsets.ModelViewSet):
    serializer_class = QcRunSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']
    filterset_fields = ['material', 'parameter', 'acceptable']

    def get_queryset(self):
        qs = QcRun.objects.select_related('material__centre', 'parameter')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(material__centre__user_access__user=self.request.user, material__centre__user_access__can_view=True).distinct()

    def perform_create(self, serializer):
        require_workspace(self.request.user, 'laboratory')
        serializer.save(entered_by=self.request.user)


class TatRuleViewSet(viewsets.ModelViewSet):
    serializer_class = TatRuleSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['test', 'service_group', 'is_active', 'department']
    search_fields = ['name', 'test__code', 'service_group__name']

    def get_queryset(self):
        return TatRule.objects.select_related('test', 'service_group').order_by('name')

    def perform_create(self, serializer):
        from core.access import can_access_platform_controls
        if not (self.request.user.is_superuser or can_access_platform_controls(self.request.user)):
            raise PermissionDenied('Only platform administrators can create TAT rules.')
        serializer.save()

    def perform_update(self, serializer):
        from core.access import can_access_platform_controls
        if not (self.request.user.is_superuser or can_access_platform_controls(self.request.user)):
            raise PermissionDenied('Only platform administrators can update TAT rules.')
        serializer.save()


class TatPauseReasonViewSet(viewsets.ModelViewSet):
    serializer_class = TatPauseReasonSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['is_active', 'pauses_clock']

    def get_queryset(self):
        return TatPauseReason.objects.order_by('name')


class TatEscalationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TatEscalationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['level', 'sample']

    def get_queryset(self):
        qs = TatEscalation.objects.select_related('sample__centre', 'sample__order__test', 'rule', 'acknowledged_by')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(sample__centre__user_access__user=self.request.user, sample__centre__user_access__can_view=True).distinct()

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        require_workspace(request.user, 'laboratory')
        row = self.get_object()
        require(request.user, row.sample.centre, 'can_approve')
        if row.acknowledged_at:
            raise ValidationError('This escalation is already acknowledged.')
        row.acknowledged_at = timezone.now()
        row.acknowledged_by = request.user
        row.save(update_fields=['acknowledged_at', 'acknowledged_by', 'updated_at'])
        return Response(TatEscalationSerializer(row).data)


from rest_framework.views import APIView


class TatDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        require_workspace(request.user, 'laboratory')
        scan = request.query_params.get('scan', '1') not in ('0', 'false', 'no')
        return Response(tat_dashboard_payload(request.user, scan=scan))
