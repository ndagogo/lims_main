from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.exceptions import PermissionDenied
from core.models import AuditEvent
from .models import NotificationOutbox, NotificationTemplate
from .serializers import NotificationOutboxSerializer, NotificationTemplateSerializer

class CentreScopedMixin:
    def scoped(self, qs):
        u=self.request.user
        if u.is_superuser: return qs
        return qs.filter(centre_id__in=u.centre_access.filter(can_view=True,centre__is_active=True).values('centre_id'))

class NotificationOutboxViewSet(CentreScopedMixin, viewsets.ModelViewSet):
    serializer_class=NotificationOutboxSerializer; permission_classes=[IsAuthenticated]; http_method_names=['get','post','head','options']
    def get_queryset(self): return self.scoped(NotificationOutbox.objects.select_related('centre','visit').all())
    def perform_create(self, serializer):
        centre=serializer.validated_data['centre']
        if not self.request.user.is_superuser and not self.request.user.centre_access.filter(centre=centre,can_view=True,can_edit=True).exists():
            raise PermissionDenied('You do not have edit access to this centre.')
        obj=serializer.save(created_by=self.request.user)
        AuditEvent.objects.create(actor=self.request.user,action='NOTIFICATION_QUEUED',object_type='NotificationOutbox',object_id=str(obj.pk),path=self.request.path,method=self.request.method,centre=obj.centre,detail={'channel':obj.channel})

class NotificationTemplateViewSet(viewsets.ModelViewSet):
    serializer_class=NotificationTemplateSerializer; permission_classes=[IsAdminUser]
    queryset=NotificationTemplate.objects.all()
