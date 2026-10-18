from django.contrib.auth import get_user_model
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .auth_services import attempt_login
from .models import Centre, UserCentreAccess, AuditEvent
from .permissions import HasCentreAccess

User = get_user_model()
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
class CentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Centre
        fields = ['id','code','name','address','phone','email','logo','timezone','is_active']
        read_only_fields = ['id']
class UserCentreAccessSerializer(serializers.ModelSerializer):
    centre_name = serializers.CharField(source='centre.name', read_only=True)
    centre_code = serializers.CharField(source='centre.code', read_only=True)
    class Meta:
        model=UserCentreAccess
        fields=['id','centre','centre_name','centre_code','can_view','can_register','can_edit','can_approve','is_default']
        read_only_fields=['id','centre_name','centre_code']

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    user, error = attempt_login(serializer.validated_data['username'], serializer.validated_data['password'])
    if user is None or not user.is_active:
        AuditEvent.objects.create(actor=None, action='LOGIN_FAILED', object_type='AUTH', ip_address=getattr(request,'audit_ip',None), browser=getattr(request,'audit_browser',''), detail={'username': serializer.validated_data['username'][:150]})
        return Response({'detail': error or 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)
    refresh = RefreshToken.for_user(user)
    access = list(UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True).select_related('centre').values('centre_id','centre__code','centre__name','is_default'))
    AuditEvent.objects.create(actor=user, action='LOGIN_SUCCESS', object_type='AUTH', ip_address=getattr(request,'audit_ip',None), browser=getattr(request,'audit_browser',''))
    return Response({'access':str(refresh.access_token),'refresh':str(refresh),'user':{'id':str(user.pk),'username':user.username,'email':user.email,'display_name':user.display_name or user.get_full_name(),'is_staff':user.is_staff,'must_change_password':user.must_change_password,'groups':list(user.groups.values_list('name',flat=True)),'centres':access}})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    user=request.user
    access=UserCentreAccess.objects.filter(user=user,centre__is_active=True,can_view=True).select_related('centre')
    return Response({
        'id': str(user.pk),
        'username': user.username,
        'email': user.email,
        'display_name': user.display_name or user.get_full_name(),
        'first_name': user.first_name,
        'last_name': user.last_name,
        'job_title': user.job_title,
        'staff_id': user.staff_id,
        'department': user.department,
        'specialty': user.specialty,
        'qualifications': user.qualifications,
        'license_number': user.license_number,
        'phone_work': user.phone_work,
        'phone_mobile': user.phone_mobile,
        'avatar': user.avatar.url if user.avatar else None,
        'has_signature': bool(user.signature),
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'must_change_password': user.must_change_password,
        'mfa_enabled': user.mfa_enabled,
        'groups': list(user.groups.values_list('name', flat=True)),
        'permissions': list(user.get_all_permissions()),
        'centres': UserCentreAccessSerializer(access, many=True).data,
    })

class CentreViewSet(viewsets.ModelViewSet):
    serializer_class=CentreSerializer
    permission_classes=[IsAuthenticated, HasCentreAccess]
    search_fields=['code','name']; ordering_fields=['name','code','created_at']
    def get_queryset(self):
        qs=Centre.objects.filter(is_active=True)
        if self.request.user.is_superuser: return Centre.objects.all()
        return qs.filter(user_access__user=self.request.user,user_access__can_view=True).distinct()
    def get_permissions(self):
        if self.action in ('create','update','partial_update','destroy'): return [IsAdminUser()]
        return super().get_permissions()

class UserCentreAccessViewSet(viewsets.ModelViewSet):
    serializer_class=UserCentreAccessSerializer
    permission_classes=[IsAdminUser]
    queryset=UserCentreAccess.objects.select_related('user','centre').all()
    filterset_fields=['user','centre','is_default']
    def perform_create(self, serializer): serializer.save(granted_by=self.request.user)

class AuditEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, allow_null=True)
    centre_name = serializers.CharField(source='centre.name', read_only=True, allow_null=True)
    approval_by_username = serializers.CharField(source='approval_by.username', read_only=True, allow_null=True)
    class Meta:
        model = AuditEvent
        fields = [
            'id', 'created_at', 'actor', 'actor_username', 'action', 'category',
            'object_type', 'object_id', 'path', 'method', 'ip_address', 'browser',
            'centre', 'centre_name', 'department',
            'patient_ref', 'visit_ref', 'order_ref', 'result_ref',
            'previous_values', 'new_values', 'reason',
            'approval_by', 'approval_by_username', 'approval_note',
            'evidence_ref', 'evidence_note', 'detail',
        ]

class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=AuditEventSerializer
    permission_classes=[IsAdminUser]
    queryset=AuditEvent.objects.select_related('actor','centre').all()
    filterset_fields=['action','object_type','actor','centre']
    search_fields=['path','object_id','actor__username']
    def list(self, request, *args, **kwargs):
        qs=self.filter_queryset(self.get_queryset())
        page=self.paginate_queryset(qs)
        rows=page if page is not None else qs[:100]
        data=[{'id':str(x.id),'created_at':x.created_at,'actor':x.actor.username if x.actor else None,'action':x.action,'object_type':x.object_type,'object_id':x.object_id,'path':x.path,'method':x.method,'ip_address':x.ip_address,'browser':x.browser,'centre':str(x.centre) if x.centre else None,'detail':x.detail} for x in rows]
        return self.get_paginated_response(data) if page is not None else Response(data)
