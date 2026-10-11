"""Template values for the role-based staff navigation."""
from .access import WORKSPACE_NAV, allowed_workspaces, is_accountant, is_platform_admin


def workspace_access(request):
    user = getattr(request, 'user', None)
    allowed = allowed_workspaces(user)
    nav = [row for row in WORKSPACE_NAV if row[0] in allowed]
    labels = [label for _key, label, _url, _icon in nav]
    if 'audit' in allowed:
        labels.append('Audit')
    if not labels:
        title = 'No workspace assigned'
    elif len(labels) == 1:
        title = labels[0]
    else:
        title = 'Your workspaces'
    platform_admin = bool(user and getattr(user, 'is_authenticated', False) and is_platform_admin(user))
    accountant = bool(user and getattr(user, 'is_authenticated', False) and is_accountant(user))
    return {
        'allowed_workspaces': allowed,
        'workspace_nav': nav,
        'workspace_title': title,
        'is_platform_admin': platform_admin,
        'is_accountant': accountant,
    }
