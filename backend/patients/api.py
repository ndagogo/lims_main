from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from core.permissions import HasCentreAccess
from core.models import UserCentreAccess, AuditEvent
from .models import Patient, Visit, PatientDocument
from .serializers import PatientSerializer, VisitSerializer, PatientDocumentSerializer

class PatientViewSet(viewsets.ModelViewSet):
    serializer_class=PatientSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]
    search_fields=['uhid','first_name','last_name','phone_number']; filterset_fields=['sex']
    def get_queryset(self):
        qs=Patient.objects.all().order_by('-created_at')
        if self.request.user.is_superuser: return qs
        return qs.filter(visits__centre__user_access__user=self.request.user,visits__centre__user_access__can_view=True,visits__centre__is_active=True).distinct()
    def perform_create(self, serializer):
        if not self.request.user.is_superuser and not UserCentreAccess.objects.filter(user=self.request.user,centre__is_active=True,can_register=True).exists(): raise PermissionDenied('Patient registration permission required.')
        serializer.save()
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        patient=self.get_object()
        visits=patient.visits.select_related('centre','registered_by').order_by('-created_at')
        if not request.user.is_superuser: visits=visits.filter(centre__user_access__user=request.user,centre__user_access__can_view=True,centre__is_active=True).distinct()
        return Response(VisitSerializer(visits,many=True,context={'request':request}).data)

class VisitViewSet(viewsets.ModelViewSet):
    serializer_class=VisitSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]
    search_fields=['visit_id','patient__uhid','patient__phone_number','patient__first_name','patient__last_name']; filterset_fields=['centre','status','patient']
    def get_queryset(self):
        qs=Visit.objects.select_related('patient','centre').prefetch_related('documents').all().order_by('-created_at')
        if self.request.user.is_superuser:return qs
        return qs.filter(centre__user_access__user=self.request.user,centre__user_access__can_view=True,centre__is_active=True).distinct()
    def perform_create(self,serializer): serializer.save(registered_by=self.request.user)
    @action(detail=True, methods=['post'])
    def upload_document(self, request, pk=None):
        visit=self.get_object()
        if not request.user.is_superuser and not UserCentreAccess.objects.filter(user=request.user,centre=visit.centre,can_register=True).exists(): raise PermissionDenied('Document upload permission required.')
        data=request.data.copy(); data['visit']=str(visit.pk)
        ser=PatientDocumentSerializer(data=data,context={'request':request})
        ser.is_valid(raise_exception=True)
        doc=ser.save(uploaded_by=request.user)
        AuditEvent.objects.create(actor=request.user,action='PATIENT_DOCUMENT_UPLOADED',object_type='PatientDocument',object_id=str(doc.pk),path=request.path,method=request.method,centre=visit.centre,detail={'document_type':doc.document_type})
        return Response(PatientDocumentSerializer(doc).data,status=status.HTTP_201_CREATED)

class PatientDocumentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=PatientDocumentSerializer; permission_classes=[IsAuthenticated,HasCentreAccess]
    def get_queryset(self):
        qs=PatientDocument.objects.select_related('visit__centre').all()
        if self.request.user.is_superuser:return qs
        return qs.filter(visit__centre__user_access__user=self.request.user,visit__centre__user_access__can_view=True,visit__centre__is_active=True).distinct()
