from rest_framework.permissions import BasePermission, SAFE_METHODS
from .models import UserCentreAccess


class HasRoleOrPerm(BasePermission):
    """Require one of view.required_roles or view.required_perm (superusers always pass)."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        perm = getattr(view, 'required_perm', None)
        if perm and user.has_perm(perm):
            return True
        roles = getattr(view, 'required_roles', ()) or ()
        if roles and user.groups.filter(name__in=tuple(roles)).exists():
            return True
        action_roles = getattr(view, 'action_roles', None) or {}
        action = getattr(view, 'action', None)
        if action and action in action_roles:
            names = action_roles[action]
            if user.groups.filter(name__in=tuple(names)).exists():
                return True
            action_perm = (getattr(view, 'action_perms', None) or {}).get(action)
            if action_perm and user.has_perm(action_perm):
                return True
            return False
        if roles or perm:
            return False
        return True


class HasCentreAccess(BasePermission):
    """Enforces centre access for objects exposing centre or visit.centre."""
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        centre_id = request.query_params.get('centre') or request.data.get('centre')
        if not centre_id:
            access_qs = UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True)
            if request.method == 'POST':
                return access_qs.filter(can_register=True).exists()
            if request.method in ('PUT', 'PATCH', 'DELETE'):
                return access_qs.filter(can_edit=True).exists()
            return access_qs.exists()
        access = UserCentreAccess.objects.filter(user=user, centre_id=centre_id, centre__is_active=True).first()
        if not access or not access.can_view:
            return False
        if request.method not in SAFE_METHODS and request.method not in ('HEAD','OPTIONS'):
            if request.method == 'POST' and not access.can_register:
                return False
            if request.method in ('PUT','PATCH','DELETE') and not access.can_edit:
                return False
        return True
    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        centre = getattr(obj, 'centre', None)
        if centre is None and hasattr(obj, 'visit'):
            centre = getattr(obj.visit, 'centre', None)
        if centre is None and hasattr(obj, 'order') and hasattr(obj.order, 'visit'):
            centre = getattr(obj.order.visit, 'centre', None)
        if centre is None:
            # Patients are shared longitudinal records; access requires at least one centre grant.
            return UserCentreAccess.objects.filter(user=user, centre__is_active=True, can_view=True).exists()
        access = UserCentreAccess.objects.filter(user=user, centre=centre, centre__is_active=True, can_view=True).first()
        if not access: return False
        if request.method in SAFE_METHODS: return True
        if request.method == 'POST': return access.can_register
        return access.can_edit
