"""Shared role and network checks used by the API and staff screens."""
from django.conf import settings
from django.core.exceptions import PermissionDenied as ScreenDenied
from rest_framework.exceptions import PermissionDenied

ACCOUNTS_ROLES = ('Accounts', 'Centre Administrator', 'Platform Administrator')
CASHIER_ROLES = ('Cashier',)
LAB_SCIENTIST_ROLES = ('Laboratory Scientist', 'Lab Manager')
LAB_MANAGER_ROLES = ('Lab Manager', 'Platform Administrator')
RADIOGRAPHER_ROLES = ('Radiographer', 'Radiologist', 'Platform Administrator')
RADIOLOGIST_ROLES = ('Radiologist', 'Platform Administrator')
PHLEBOTOMY_ROLES = ('Phlebotomist',)
OVERSIGHT_ROLES = ('Centre Administrator', 'Platform Administrator')
PLATFORM_ADMIN_GROUP = 'Platform Administrator'

# A staff screen and the roles that may open it. Several roles show the union of their screens.
WORKSPACE_ROLES = {
    'registration': ('Front Desk',),
    'accounts': ('Accounts', 'Cashier'),
    'phlebotomy': PHLEBOTOMY_ROLES,
    'laboratory': LAB_SCIENTIST_ROLES,
    'radiology': ('Radiographer', 'Radiologist'),
    'audit': ('Auditor',),
}
WORKSPACE_NAV = (
    ('registration', 'Patient registration', '/patients/register/', 'bi-person-vcard'),
    ('accounts', 'Accounts & receipts', '/accounts/', 'bi-receipt'),
    ('phlebotomy', 'Phlebotomy', '/phlebotomy/', 'bi-droplet-half'),
    ('laboratory', 'Laboratory', '/laboratory/', 'bi-droplet'),
    ('radiology', 'Radiology', '/radiology/', 'bi-radioactive'),
)


def client_ip(request):
    """Return the caller IP. Forwarded headers count only when the proxy flag is on."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if settings.AUDIT_TRUST_X_FORWARDED_FOR and forwarded:
        return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


def client_browser(request):
    return request.META.get('HTTP_USER_AGENT', '')[:255]


def is_platform_admin(user):
    """Superusers and Platform Administrator group members may manage catalog setup."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=PLATFORM_ADMIN_GROUP).exists()


def can_approve_discount(user):
    """Accountant privilege: approve/reject discount requests. Cashiers do not get this."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if user.has_perm('billing.approve_discount'):
        return True
    return user.groups.filter(name__in=ACCOUNTS_ROLES).exists()


def is_accountant(user):
    """Full billing controls and financial reports (not the cashier paypoint alone)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if user.has_perm('billing.approve_discount') or user.has_perm('billing.cancel_invoice') or user.has_perm('billing.reverse_payment'):
        return True
    return user.groups.filter(name__in=ACCOUNTS_ROLES).exists()


def allowed_workspaces(user):
    """Screens this person may open. Oversight roles and superusers see every staff screen."""
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    if user.is_superuser or user.groups.filter(name__in=OVERSIGHT_ROLES).exists():
        return {key for key, _roles in WORKSPACE_ROLES.items()}
    names = set(user.groups.values_list('name', flat=True))
    return {key for key, roles in WORKSPACE_ROLES.items() if names.intersection(roles)}


def require_workspace(user, *workspaces):
    """Stop a staff screen or department action that is outside the person's role."""
    if any(name in allowed_workspaces(user) for name in workspaces):
        return
    raise ScreenDenied('This workspace is outside your role.')


def require_any_role(user, roles, perm=None):
    """Allow superusers, a named staff group, or an explicitly granted permission."""
    if not user or not user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if user.is_superuser:
        return
    if perm and user.has_perm(perm):
        return
    if user.groups.filter(name__in=tuple(roles)).exists():
        return
    names = ', '.join(roles)
    raise PermissionDenied(f'This action requires one of these roles: {names}.')
