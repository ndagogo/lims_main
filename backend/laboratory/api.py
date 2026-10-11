from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
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
from .models import (Test,Tariff,TestPrice,LabOrder,Sample,SampleActivity,SampleBatch,BatchSample,SampleProcessing,TestParameter,ResultTemplate,LabResult,ResultValue,ResultReview)
from .serializers import (TestSerializer,TariffSerializer,TestPriceSerializer,LabOrderSerializer,SampleSerializer,SampleActivitySerializer,SampleBatchSerializer,SampleProcessingSerializer,TestParameterSerializer,ResultTemplateSerializer,LabResultSerializer,ResultReviewSerializer)

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
        visit=serializer.validated_data['visit']; require(self.request.user,visit.centre,'can_register'); serializer.save()
    @action(detail=True,methods=['post'],url_path='collect')
    def collect(self,request,pk=None):
        require_workspace(request.user, 'phlebotomy', 'laboratory')
        order=self.get_object(); require(request.user,order.visit.centre,'can_edit'); assert_paid(order)
        if order.status=='CANCELLED': raise ValidationError('Cancelled orders cannot be collected.')
        centre=order.visit.centre
        if not hasattr(centre,'sample_code_config'): raise ValidationError('Configure a numeric sample code for this centre before collection.')
        ip,browser=request_meta(request)
        with transaction.atomic():
            sample=Sample(order=order,centre=centre,collected_by=request.user,collected_at=timezone.now(),collector_ip=ip,collector_browser=browser,status='COLLECTED'); sample.save()
            order.status='IN_PROGRESS'; order.save(update_fields=['status','updated_at'])
            activity(sample,request,'COLLECTED','COLLECTED',request.data.get('note',''))
        return Response(SampleSerializer(sample,context={'request':request}).data,status=status.HTTP_201_CREATED)
class SampleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=SampleSerializer; permission_classes=[IsAuthenticated]; search_fields=['sample_id','order__visit__visit_id','order__visit__patient__phone_number']; filterset_fields=['status','centre','order__test']
    def get_queryset(self): return scoped_samples(self.request.user)
    @action(detail=True,methods=['post'])
    def recollect(self,request,pk=None):
        require_workspace(request.user, 'phlebotomy', 'laboratory')
        old=self.get_object(); require(request.user,old.centre,'can_edit')
        if old.status in ['RELEASED','CANCELLED']: raise ValidationError('Released or cancelled samples cannot be recollected.')
        assert_paid(old.order); ip,browser=request_meta(request)
        new=Sample(order=old.order,centre=old.centre,recollection_of=old,collected_by=request.user,collected_at=timezone.now(),collector_ip=ip,collector_browser=browser,status='COLLECTED'); new.save()
        activity(new,request,'RECOLLECTED','COLLECTED',request.data.get('reason','Recollection'))
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
        return Response({'sample_id':sample.sample_id,'symbology':'Code128','barcode_svg_url':request.build_absolute_uri(request.path+'?format=svg'),'human_readable':sample.sample_id,'visit_id':sample.order.visit.visit_id,'patient_uhid':sample.order.visit.patient.uhid,'test':sample.order.test.name,'centre':sample.centre.name})
    @action(detail=True,methods=['post'])
    def reject(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        if sample.status in ['RELEASED','CANCELLED']: raise ValidationError('This sample can no longer be rejected.')
        reason=(request.data.get('reason') or '').strip()
        if not reason: raise ValidationError({'reason':'A rejection reason is required.'})
        sample.rejection_reason=reason; sample.save(update_fields=['rejection_reason','updated_at']); set_status(sample,request,'REJECTED','REJECTED',reason)
        return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def receive(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        if sample.status!='BATCHED': raise ValidationError('Only batched samples can be received by a department.')
        if not sample.batch_memberships.filter(batch__status='RECEIVED').exists(): raise ValidationError('The containing batch must be received by the department first.')
        set_status(sample,request,'RECEIVED','DEPARTMENT_RECEIVED',request.data.get('note','')); return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def start_processing(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
        if sample.status!='RECEIVED': raise ValidationError('Sample must be received by the department first.')
        with transaction.atomic():
            SampleProcessing.objects.create(sample=sample,action='PROCESSING_STARTED',notes=request.data.get('notes',''),performed_by=request.user)
            set_status(sample,request,'PROCESSING','PROCESSING_STARTED',request.data.get('notes',''))
        return Response(SampleSerializer(sample,context={'request':request}).data)
    @action(detail=True,methods=['post'])
    def complete_processing(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        sample=self.get_object(); require(request.user,sample.centre,'can_edit')
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
        if sample.status!='PROCESSING': raise ValidationError('Sample must be in processing before result entry.')
        if sample.processing_records.filter(completed_at__isnull=True).exists(): raise ValidationError('Complete the current processing activity before entering results.')
        existing_result = getattr(sample, 'result', None)
        if existing_result and existing_result.status not in ['DRAFT']:
            raise ValidationError('Only draft results can be corrected here; reviewed/authenticated results require a controlled amendment workflow.')
        data=request.data; template=None
        if data.get('template'):
            try: template=ResultTemplate.objects.get(pk=data['template'],test=sample.order.test,is_active=True)
            except ResultTemplate.DoesNotExist: raise ValidationError({'template':'Template does not belong to this test.'})
        with transaction.atomic():
            if existing_result:
                result=existing_result; result.template=template; result.narrative=data.get('narrative',''); result.entered_by=request.user; result.save(update_fields=['template','narrative','entered_by','updated_at'])
                result.values.all().delete()
            else:
                result=LabResult.objects.create(sample=sample,template=template,narrative=data.get('narrative',''),entered_by=request.user,status='DRAFT')
            values=data.get('values',[])
            for row in values:
                try: parameter=TestParameter.objects.get(pk=row['parameter'],test=sample.order.test)
                except (TestParameter.DoesNotExist,KeyError): raise ValidationError({'values':'Each parameter must belong to the sample test.'})
                from .services import classify_value
                raw_flag=str(row.get('flag') or '')
                if not raw_flag:
                    raw_flag, _range_text = classify_value(parameter, row.get('value', ''), sample.order.visit.patient)
                ResultValue.objects.update_or_create(result=result,parameter=parameter,defaults={'value':str(row.get('value','')),'flag':raw_flag[:20]})
            required=set(sample.order.test.parameters.filter(is_required=True).values_list('id',flat=True)); supplied=set(result.values.values_list('parameter_id',flat=True))
            if required-supplied: raise ValidationError({'values':'Missing required parameter values: '+', '.join(map(str,required-supplied))})
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
                BatchSample.objects.create(batch=batch,sample=sample,added_by=self.request.user); set_status(sample,self.request,'BATCHED','BATCHED',batch.batch_code)
    @action(detail=True, methods=['post'], url_path='dispatch')
    def dispatch_batch(self, request, pk=None):
        require_workspace(request.user, 'laboratory')
        batch=self.get_object(); require(request.user,batch.centre,'can_edit')
        if batch.status!='OPEN' or not batch.items.exists(): raise ValidationError('Only non-empty open batches can be dispatched.')
        batch.status='DISPATCHED'; batch.dispatched_at=timezone.now(); batch.save(update_fields=['status','dispatched_at','updated_at'])
        return Response(SampleBatchSerializer(batch).data)
    @action(detail=True,methods=['post'])
    def receive(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        batch=self.get_object(); require(request.user,batch.centre,'can_edit')
        if batch.status!='DISPATCHED': raise ValidationError('Only dispatched batches can be received.')
        with transaction.atomic():
            batch.status='RECEIVED'; batch.received_at=timezone.now(); batch.received_by=request.user; batch.save(update_fields=['status','received_at','received_by','updated_at'])
            for item in batch.items.select_related('sample'):
                if item.sample.status=='BATCHED': set_status(item.sample,request,'RECEIVED','DEPARTMENT_RECEIVED',batch.batch_code)
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
        result.status='SUBMITTED'; result.save(update_fields=['status','updated_at']); ResultReview.objects.create(result=result,action='SUBMITTED',reviewer=request.user,note=request.data.get('note',''))
        set_status(result.sample,request,'UNDER_REVIEW','SUBMITTED_FOR_REVIEW')
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def review(self,request,pk=None):
        require_workspace(request.user, 'laboratory')
        result=self.get_object(); require(request.user,result.sample.centre,'can_approve')
        if result.status!='SUBMITTED': raise ValidationError('Only submitted results can be reviewed.')
        approve=request.data.get('decision')=='APPROVE'; note=(request.data.get('note') or '').strip()
        if not approve and not note: raise ValidationError({'note':'A reason is required when rejecting a result.'})
        result.reviewed_by=request.user; result.reviewed_at=timezone.now(); result.status='REVIEWED' if approve else 'DRAFT'; result.save(update_fields=['reviewed_by','reviewed_at','status','updated_at'])
        ResultReview.objects.create(result=result,action='APPROVED' if approve else 'REJECTED',reviewer=request.user,note=note)
        set_status(result.sample,request,'UNDER_REVIEW' if approve else 'PROCESSING','RESULT_REVIEWED' if approve else 'RESULT_REJECTED',note)
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def authenticate(self,request,pk=None):
        result=self.get_object(); require(request.user,result.sample.centre,'can_approve')
        require_any_role(request.user, LAB_MANAGER_ROLES, perm='laboratory.authenticate_labresult')
        if result.status!='REVIEWED': raise ValidationError('Only reviewed results can be authenticated.')
        if result.entered_by_id==request.user.id: raise PermissionDenied('The result author cannot authenticate their own result.')
        if result.reviewed_by_id==request.user.id: raise PermissionDenied('The first reviewer cannot authenticate the same result.')
        result.authenticated_by=request.user; result.authenticated_at=timezone.now(); result.status='AUTHENTICATED'; result.save(update_fields=['authenticated_by','authenticated_at','status','updated_at'])
        ResultReview.objects.create(result=result,action='AUTHENTICATED',reviewer=request.user,note=request.data.get('note',''))
        set_status(result.sample,request,'AUTHENTICATED','RESULT_AUTHENTICATED')
        return Response(LabResultSerializer(result).data)
    @action(detail=True,methods=['post'])
    def release(self,request,pk=None):
        result=self.get_object(); require(request.user,result.sample.centre,'can_edit')
        require_any_role(request.user, LAB_MANAGER_ROLES, perm='laboratory.release_labresult')
        if result.status!='AUTHENTICATED': raise ValidationError('Only authenticated results can be released.')
        if result.authenticated_by_id==request.user.id: raise PermissionDenied('The authenticating user cannot release the same result.')
        visit=result.sample.order.visit
        if not visit.documents.filter(document_type__in=['REFERRAL','REGISTRATION']).exists(): raise ValidationError('A referral or registration form must be uploaded before result release.')
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
        result=self.get_object()
        return Response({'sample_id':result.sample.sample_id,'result_status':result.status,'events':list(result.reviews.values('action','note','created_at','reviewer__username')),'sample_activities':list(result.sample.activities.values('action','from_status','to_status','note','created_at','performed_by__username'))})
class SampleActivityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=SampleActivitySerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self):
        qs=SampleActivity.objects.select_related('sample__centre','performed_by')
        return qs if self.request.user.is_superuser else qs.filter(sample__centre__user_access__user=self.request.user,sample__centre__user_access__can_view=True).distinct()
