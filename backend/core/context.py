"""Template values for the role-based staff navigation."""
from .access import (
    WORKSPACE_NAV, allowed_workspaces, can_access_platform_controls,
    can_change_referring_doctor, can_edit_bill, can_modify_patient_data,
    can_refund_payment, is_accountant, is_platform_admin,
)


def workspace_access(request):
    user = getattr(request, 'user', None)
    allowed = allowed_workspaces(user)
    authenticated = bool(user and getattr(user, 'is_authenticated', False))
    nav = [row for row in WORKSPACE_NAV if row[0] in allowed]
    reporting = authenticated and (
        user.is_superuser or user.groups.filter(name__in=('Radiologist', 'Platform Administrator')).exists()
    )
    if reporting and 'radiology' in allowed:
        built = []
        for row in nav:
            built.append(row)
            if row[0] == 'radiology':
                built.append(('templates', 'Templates', '/radiology/templates/', 'bi-file-medical'))
        nav = built
    labels = [label for _key, label, _url, _icon in nav]
    if 'audit' in allowed:
        labels.append('Audit')
    if not labels:
        title = 'No workspace assigned'
    elif len(labels) == 1:
        title = labels[0]
    else:
        title = 'Your workspaces'
    platform_admin = authenticated and is_platform_admin(user)
    accountant = authenticated and is_accountant(user)
    active_centre = None
    if authenticated:
        from .models import UserCentreAccess
        access = (
            UserCentreAccess.objects.filter(user=user, is_default=True, centre__is_active=True)
            .select_related('centre').first()
            or UserCentreAccess.objects.filter(user=user, can_view=True, centre__is_active=True)
            .select_related('centre').order_by('centre__name').first()
        )
        if access:
            active_centre = access.centre
    return {
        'home_url': '/radiology/' if allowed == {'radiology'} else '/',
        'active_centre': active_centre,
        'allowed_workspaces': allowed,
        'workspace_nav': nav,
        'workspace_title': title,
        'is_platform_admin': platform_admin,
        'is_accountant': accountant,
        'can_modify_patient_data': authenticated and can_modify_patient_data(user),
        'can_change_referring_doctor': authenticated and can_change_referring_doctor(user),
        'can_refund_payment': authenticated and can_refund_payment(user),
        'can_edit_bill': authenticated and can_edit_bill(user),
        'can_access_platform_controls': authenticated and can_access_platform_controls(user),
    }
