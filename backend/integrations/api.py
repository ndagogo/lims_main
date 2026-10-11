from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from core.models import AuditEvent
from .models import Analyzer, AnalyzerTestMapping, AnalyzerMessage
from .serializers import AnalyzerSerializer, AnalyzerTestMappingSerializer, AnalyzerMessageSerializer
from .services import mapped_result_values, match_sample_from_payload, parse_hl7_obx

class CentreScopedMixin:
    def centre_ids(self):
        u=self.request.user
        if u.is_superuser: return None
        return u.centre_access.filter(can_view=True, centre__is_active=True).values_list('centre_id', flat=True)
    def scope(self, qs, field='centre_id'):
        ids=self.centre_ids()
        return qs if ids is None else qs.filter(**{f'{field}__in': ids})

class AnalyzerViewSet(CentreScopedMixin, viewsets.ModelViewSet):
    serializer_class=AnalyzerSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self): return self.scope(Analyzer.objects.select_related('centre').all())
    def perform_create(self, serializer):
        centre=serializer.validated_data['centre']
        if not self.request.user.is_superuser and not self.request.user.centre_access.filter(centre=centre,can_view=True,can_edit=True).exists():
            raise PermissionDenied('You do not have edit access to this centre.')
        obj=serializer.save()
        AuditEvent.objects.create(actor=self.request.user,action='ANALYZER_CREATED',object_type='Analyzer',object_id=str(obj.pk),path=self.request.path,method=self.request.method,centre=obj.centre,detail={'protocol':obj.protocol})

class AnalyzerTestMappingViewSet(CentreScopedMixin, viewsets.ModelViewSet):
    serializer_class=AnalyzerTestMappingSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self): return self.scope(AnalyzerTestMapping.objects.select_related('analyzer','analyzer__centre').all(),'analyzer__centre_id')

    def perform_create(self, serializer):
        analyzer=serializer.validated_data['analyzer']
        if not self.request.user.is_superuser and not self.request.user.centre_access.filter(centre=analyzer.centre,can_view=True,can_edit=True).exists():
            raise PermissionDenied('You do not have edit access to this centre.')
        serializer.save()

class AnalyzerMessageViewSet(CentreScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class=AnalyzerMessageSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self): return self.scope(AnalyzerMessage.objects.select_related('analyzer','analyzer__centre','sample').all(),'analyzer__centre_id')
    @action(detail=False, methods=['post'], url_path='ingest')
    def ingest(self, request):
        # Authenticated gateway ingress; use TLS and a dedicated service account.
        analyzer_id=request.data.get('analyzer_id')
        raw_payload=request.data.get('raw_payload')
        if not analyzer_id or not isinstance(raw_payload,str) or not raw_payload.strip():
            return Response({'detail':'analyzer_id and non-empty raw_payload are required.'},status=400)
        try:
            analyzer=self.get_queryset().get(pk=analyzer_id,is_active=True)
        except Analyzer.DoesNotExist:
            return Response({'detail':'Active analyzer not found in your permitted centres.'},status=404)
        if not request.user.is_superuser and not request.user.centre_access.filter(centre=analyzer.centre,can_view=True,can_edit=True).exists():
            raise PermissionDenied('Analyzer message ingestion requires edit access to this centre.')
        sample=None
        sample_id=request.data.get('sample_id')
        if sample_id:
            from laboratory.models import Sample
            sample=Sample.objects.filter(sample_id=sample_id,centre=analyzer.centre).first()
            if sample is None:
                return Response({'detail':'Sample was not found at this analyzer centre; message not accepted.'},status=400)
        msg=AnalyzerMessage.objects.create(analyzer=analyzer,sample=sample,external_message_id=str(request.data.get('external_message_id',''))[:150],raw_payload=raw_payload[:200000])
        analyzer.last_seen_at=timezone.now(); analyzer.save(update_fields=['last_seen_at','updated_at'])
        AuditEvent.objects.create(actor=request.user,action='ANALYZER_MESSAGE_RECEIVED',object_type='AnalyzerMessage',object_id=str(msg.pk),path=request.path,method=request.method,centre=analyzer.centre,detail={'payload_chars':len(msg.raw_payload),'sample_matched':bool(sample)})
        return Response(AnalyzerMessageSerializer(msg).data,status=201)

    @action(detail=True, methods=['post'])
    def parse(self, request, pk=None):
        msg=self.get_object()
        if msg.status not in ('RECEIVED','ERROR'):
            return Response({'detail':'Only newly received or errored messages can be parsed.'},status=status.HTTP_409_CONFLICT)
        try:
            msg.parsed_payload={'observations':parse_hl7_obx(msg.raw_payload)}
            msg.status='PARSED' if msg.parsed_payload['observations'] else 'ERROR'
            msg.error_message='' if msg.status=='PARSED' else 'No OBX observations found; confirm protocol and payload framing.'
            msg.save(update_fields=['parsed_payload','status','error_message','updated_at'])
            AuditEvent.objects.create(actor=request.user,action='ANALYZER_MESSAGE_PARSED',object_type='AnalyzerMessage',object_id=str(msg.pk),path=request.path,method=request.method,centre=msg.analyzer.centre,detail={'observation_count':len(msg.parsed_payload['observations'])})
            return Response(self.get_serializer(msg).data)
        except Exception as exc:
            msg.status='ERROR'; msg.error_message=str(exc)[:1000]; msg.save(update_fields=['status','error_message','updated_at'])
            return Response({'detail':'Unable to parse message.'},status=400)
    @action(detail=True, methods=['post'])
    def mark_pending_review(self, request, pk=None):
        msg=self.get_object()
        if msg.status not in ('PARSED','MAPPED'):
            return Response({'detail':'Message must be parsed/mapped first.'},status=409)
        msg.status='PENDING_REVIEW'; msg.save(update_fields=['status','updated_at'])
        AuditEvent.objects.create(actor=request.user,action='ANALYZER_PENDING_REVIEW',object_type='AnalyzerMessage',object_id=str(msg.pk),path=request.path,method=request.method,centre=msg.analyzer.centre)
        return Response(self.get_serializer(msg).data)

    @action(detail=True, methods=['post'], url_path='apply-to-draft')
    def apply_to_draft(self, request, pk=None):
        """Write mapped observations onto a draft result. This never authenticates or releases."""
        msg = self.get_object()
        if msg.status == 'ACCEPTED':
            return Response({'detail': 'This message was already applied to a draft result.'}, status=409)
        if msg.status not in ('PARSED', 'PENDING_REVIEW', 'MAPPED'):
            return Response({'detail': 'Parse the message before applying it to a draft result.'}, status=409)
        observations = (msg.parsed_payload or {}).get('observations') or parse_hl7_obx(msg.raw_payload)
        if msg.sample_id is None:
            msg.sample = match_sample_from_payload(msg.analyzer, msg.raw_payload)
            if msg.sample is None:
                return Response({'detail': 'No sample at this centre matches an id in the message.'}, status=400)
            msg.save(update_fields=['sample', 'updated_at'])
        values, unmapped = mapped_result_values(msg.analyzer, observations)
        if unmapped or not values:
            msg.status = 'PARSED'
            msg.error_message = 'Unmapped instrument codes: ' + ', '.join(unmapped or ['none'])
            msg.save(update_fields=['status', 'error_message', 'updated_at'])
            return Response({'detail': msg.error_message, 'unmapped': unmapped}, status=400)
        from core.staff_api import call_action
        from laboratory.api import SampleViewSet
        status_code, body = call_action(
            SampleViewSet, 'enter_result', request.user, msg.sample_id,
            {'values': values, 'narrative': 'Imported from an analyzer message and waiting for scientific review.'},
        )
        if status_code >= 400:
            return Response(body if body is not None else {'detail': 'Draft result was not saved.'}, status=status_code)
        msg.parsed_payload = {'observations': observations}
        msg.status = 'ACCEPTED'
        msg.error_message = ''
        msg.reviewed_by = request.user
        msg.reviewed_at = timezone.now()
        msg.save(update_fields=['parsed_payload', 'status', 'error_message', 'reviewed_by', 'reviewed_at', 'updated_at'])
        AuditEvent.objects.create(actor=request.user, action='ANALYZER_DRAFT_APPLIED', object_type='AnalyzerMessage', object_id=str(msg.pk), path=request.path, method=request.method, centre=msg.analyzer.centre, detail={'sample_id': msg.sample.sample_id, 'value_count': len(values)})
        return Response(self.get_serializer(msg).data)
