"""Superuser screens for staff accounts, roles, and centre privileges."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.access import is_platform_admin
from core.models import AuditEvent, Centre, UserCentreAccess
from core.paging import PAGE_SIZES, paginate

User = get_user_model()


def superuser_only(request):
    """Allow Django superusers and Platform Administrator group members."""
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in is required.')
    if request.user.is_superuser or is_platform_admin(request.user):
        return
    raise PermissionDenied('Only a platform administrator can manage users and roles.')


def _audit(request, action, target, detail):
    AuditEvent.objects.create(
        actor=request.user, action=action, object_type='User', object_id=str(target.pk),
        path=request.path, method='POST', detail=detail,
    )


def _last_active_superuser(target):
    return target.is_superuser and target.is_active and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).exists()


def _checked(post, name):
    return post.get(name) in ('on', 'true', '1', 'yes')


def handle_user_post(request):
    action = request.POST.get('action')
    if action == 'create':
        return _create_user(request)
    target = get_object_or_404(User, pk=request.POST.get('user'))
    if action == 'suspend':
        return _suspend(request, target)
    if action == 'reactivate':
        return _reactivate(request, target)
    if action == 'unlock':
        return _unlock(request, target)
    if action == 'reset_password':
        return _reset_password(request, target)
    if action == 'save_account':
        return _save_account(request, target)
    if action == 'save_roles':
        return _save_roles(request, target)
    if action == 'save_access':
        return _save_access(request, target)
    if action == 'remove_access':
        return _remove_access(request, target)
    messages.error(request, 'Choose a user management action.')
    return redirect('user-management')


def _choice(value, allowed):
    value = (value or '').strip()
    return value if value in allowed else ''


def _apply_staff_profile(user, post):
    """Name, contact, and demographic fields collected when creating or editing staff."""
    user.title = _choice(post.get('title'), {code for code, _label in User.TITLE_CHOICES})
    user.first_name = (post.get('first_name') or '').strip()[:150]
    user.other_names = (post.get('other_names') or '').strip()[:120]
    user.last_name = (post.get('last_name') or '').strip()[:150]
    user.sex = _choice(post.get('sex'), {code for code, _label in User.SEX_CHOICES})
    user.marital_status = _choice(post.get('marital_status'), {code for code, _label in User.MARITAL_CHOICES})
    raw_dob = (post.get('date_of_birth') or '').strip()
    if raw_dob:
        dob = parse_date(raw_dob)
        if dob is None:
            raise ValidationError('Enter date of birth as YYYY-MM-DD.')
        if dob > timezone.localdate():
            raise ValidationError('Date of birth cannot be in the future.')
        user.date_of_birth = dob
    else:
        user.date_of_birth = None
    user.phone_mobile = (post.get('phone_mobile') or '').strip()[:30]
    user.phone_work = (post.get('phone_work') or '').strip()[:30]
    user.address = (post.get('address') or '').strip()[:255]
    user.staff_id = (post.get('staff_id') or '').strip()[:40]
    user.department = (post.get('department') or '').strip()[:120]
    user.job_title = (post.get('job_title') or '').strip()[:120]
    display_name = (post.get('display_name') or '').strip()
    if not display_name:
        display_name = ' '.join(part for part in (user.title, user.first_name, user.other_names, user.last_name) if part)
    user.display_name = display_name[:180]
    if not user.first_name or not user.last_name:
        raise ValidationError('First name and last name are required.')
    if not user.phone_mobile:
        raise ValidationError('A mobile phone number is required.')


PROFILE_FIELDS = [
    'title', 'first_name', 'other_names', 'last_name', 'display_name', 'sex', 'marital_status',
    'date_of_birth', 'phone_mobile', 'phone_work', 'address', 'staff_id', 'department', 'job_title',
]


def _create_user(request):
    username = (request.POST.get('username') or '').strip()
    email = (request.POST.get('email') or '').strip().lower()
    password = request.POST.get('password') or ''
    if not username or not email or not password:
        messages.error(request, 'Username, email, and a temporary password are required.')
        return redirect('user-management')
    try:
        validate_email(email)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('That username is already in use.')
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('That email is already in use.')
        candidate = User(username=username, email=email)
        _apply_staff_profile(candidate, request.POST)
        validate_password(password, candidate)
        with transaction.atomic():
            candidate.set_password(password)
            candidate.must_change_password = True
            candidate.is_staff = _checked(request.POST, 'is_staff')
            candidate.is_superuser = _checked(request.POST, 'is_superuser')
            if candidate.is_superuser:
                candidate.is_staff = True
            candidate.save()
            candidate.groups.set(Group.objects.filter(pk__in=request.POST.getlist('groups')))
            _audit(request, 'USER_CREATED', candidate, {
                'username': candidate.username,
                'display_name': candidate.display_name,
                'phone_mobile': candidate.phone_mobile,
                'roles': list(candidate.groups.values_list('name', flat=True)),
            })
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('user-management')
    messages.success(request, f'{candidate.username} can sign in. They must change the temporary password first.')
    return redirect(f'/users/?user={candidate.pk}')


def _suspend(request, target):
    if target.pk == request.user.pk:
        messages.error(request, 'Use another superuser to suspend your own account.')
        return redirect(f'/users/?user={target.pk}')
    if _last_active_superuser(target):
        messages.error(request, 'The last active superuser cannot be suspended.')
        return redirect(f'/users/?user={target.pk}')
    target.is_active = False
    target.save(update_fields=['is_active'])
    _audit(request, 'USER_SUSPENDED', target, {'username': target.username})
    messages.success(request, f'{target.username} is suspended and cannot sign in.')
    return redirect(f'/users/?user={target.pk}')


def _reactivate(request, target):
    target.is_active = True
    target.save(update_fields=['is_active'])
    _audit(request, 'USER_REACTIVATED', target, {'username': target.username})
    messages.success(request, f'{target.username} can sign in again.')
    return redirect(f'/users/?user={target.pk}')


def _unlock(request, target):
    target.failed_login_count = 0
    target.locked_until = None
    target.save(update_fields=['failed_login_count', 'locked_until'])
    _audit(request, 'USER_UNLOCKED', target, {'username': target.username})
    messages.success(request, f'{target.username} is unlocked.')
    return redirect(f'/users/?user={target.pk}')


def _reset_password(request, target):
    password = request.POST.get('password') or ''
    try:
        validate_password(password, target)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect(f'/users/?user={target.pk}')
    target.set_password(password)
    target.must_change_password = True
    target.failed_login_count = 0
    target.locked_until = None
    target.last_password_change = timezone.now()
    target.save(update_fields=['password', 'must_change_password', 'failed_login_count', 'locked_until', 'last_password_change'])
    _audit(request, 'USER_PASSWORD_RESET', target, {'username': target.username})
    messages.success(request, f'A temporary password was set for {target.username}. They must change it at the next sign-in.')
    return redirect(f'/users/?user={target.pk}')


def _save_account(request, target):
    email = (request.POST.get('email') or '').strip().lower()
    display_name = (request.POST.get('display_name') or '').strip()
    make_super = _checked(request.POST, 'is_superuser')
    make_staff = _checked(request.POST, 'is_staff') or make_super
    if not email:
        messages.error(request, 'Email is required.')
        return redirect(f'/users/?user={target.pk}')
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, 'Enter a valid email address.')
        return redirect(f'/users/?user={target.pk}')
    if User.objects.filter(email__iexact=email).exclude(pk=target.pk).exists():
        messages.error(request, 'That email is already in use.')
        return redirect(f'/users/?user={target.pk}')
    if target.pk == request.user.pk and not make_super:
        messages.error(request, 'You cannot remove your own superuser access from this screen.')
        return redirect(f'/users/?user={target.pk}')
    if target.is_superuser and target.is_active and not make_super and _last_active_superuser(target):
        messages.error(request, 'Another active superuser is required before this one can be changed.')
        return redirect(f'/users/?user={target.pk}')
    target.email = email
    try:
        _apply_staff_profile(target, request.POST)
    except ValidationError as exc:
        messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect(f'/users/?user={target.pk}')
    if display_name:
        target.display_name = display_name[:180]
    target.is_superuser = make_super
    target.is_staff = make_staff
    target.must_change_password = _checked(request.POST, 'must_change_password')
    target.save(update_fields=['email', *PROFILE_FIELDS, 'is_superuser', 'is_staff', 'must_change_password'])
    _audit(request, 'USER_ACCOUNT_UPDATED', target, {'username': target.username, 'is_superuser': target.is_superuser, 'is_staff': target.is_staff})
    messages.success(request, f'{target.username} was updated.')
    return redirect(f'/users/?user={target.pk}')


def _save_roles(request, target):
    groups = Group.objects.filter(pk__in=request.POST.getlist('groups'))
    target.groups.set(groups)
    names = list(groups.values_list('name', flat=True))
    _audit(request, 'USER_ROLES_UPDATED', target, {'username': target.username, 'roles': names})
    messages.success(request, f'Roles for {target.username} now: {", ".join(names) or "none"}.')
    return redirect(f'/users/?user={target.pk}')


def _save_access(request, target):
    centre = Centre.objects.filter(pk=request.POST.get('centre')).first()
    if centre is None:
        messages.error(request, 'Choose a centre.')
        return redirect(f'/users/?user={target.pk}')
    access, _created = UserCentreAccess.objects.get_or_create(user=target, centre=centre, defaults={'granted_by': request.user})
    access.can_view = _checked(request.POST, 'can_view')
    access.can_register = _checked(request.POST, 'can_register')
    access.can_edit = _checked(request.POST, 'can_edit')
    access.can_approve = _checked(request.POST, 'can_approve')
    access.is_default = _checked(request.POST, 'is_default')
    access.granted_by = request.user
    if not any((access.can_view, access.can_register, access.can_edit, access.can_approve)):
        messages.error(request, 'Grant at least one centre privilege, or remove the centre.')
        return redirect(f'/users/?user={target.pk}')
    access.save()
    _audit(request, 'USER_CENTRE_ACCESS', target, {'username': target.username, 'centre': centre.code, 'can_view': access.can_view, 'can_register': access.can_register, 'can_edit': access.can_edit, 'can_approve': access.can_approve})
    messages.success(request, f'{centre.code} privileges saved for {target.username}.')
    return redirect(f'/users/?user={target.pk}')


def _remove_access(request, target):
    access = UserCentreAccess.objects.filter(pk=request.POST.get('access'), user=target).select_related('centre').first()
    if access is None:
        messages.error(request, 'That centre grant was not found.')
        return redirect(f'/users/?user={target.pk}')
    code = access.centre.code
    access.delete()
    _audit(request, 'USER_CENTRE_ACCESS_REMOVED', target, {'username': target.username, 'centre': code})
    messages.success(request, f'{target.username} no longer has access to {code}.')
    return redirect(f'/users/?user={target.pk}')


def build_user_context(request):
    now = timezone.now()
    query = (request.GET.get('q') or '').strip()
    status = request.GET.get('status') or 'all'
    users = User.objects.prefetch_related('groups', 'centre_access__centre').order_by('username')
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(email__icontains=query) | Q(display_name__icontains=query)
            | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(other_names__icontains=query)
            | Q(phone_mobile__icontains=query) | Q(staff_id__icontains=query)
        )
    if status == 'suspended':
        users = users.filter(is_active=False)
    elif status == 'locked':
        users = users.filter(is_active=True, locked_until__gt=now)
    elif status == 'active':
        users = users.filter(is_active=True).filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
    directory = paginate(request, users, 'upage')
    for person in directory.object_list:
        person.account_state = _account_state(person, now)
    selected = None
    selected_id = request.GET.get('user')
    if selected_id:
        selected = User.objects.prefetch_related('groups', 'centre_access__centre').filter(pk=selected_id).first()
        if selected:
            selected.account_state = _account_state(selected, now)
            selected.role_ids = set(selected.groups.values_list('pk', flat=True))
    return {
        'directory': directory,
        'page_sizes': PAGE_SIZES,
        'query': query,
        'status': status,
        'selected': selected,
        'roles': Group.objects.order_by('name'),
        'centres': Centre.objects.order_by('name'),
        'title_choices': User.TITLE_CHOICES,
        'sex_choices': User.SEX_CHOICES,
        'marital_choices': User.MARITAL_CHOICES,
        'counts': {
            'active': User.objects.filter(is_active=True).filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now)).count(),
            'suspended': User.objects.filter(is_active=False).count(),
            'locked': User.objects.filter(is_active=True, locked_until__gt=now).count(),
        },
    }


def _account_state(person, now):
    if not person.is_active:
        return 'Suspended'
    if person.locked_until and person.locked_until > now:
        return 'Locked'
    return 'Active'


PROTECTED_ROLE_NAMES = frozenset({'Platform Administrator'})


def _normalize_role_name(raw):
    return ' '.join((raw or '').split()).strip()[:150]


def _create_role(request):
    name = _normalize_role_name(request.POST.get('name'))
    if not name:
        messages.error(request, 'Enter a role name.')
        return redirect('role-management')
    if Group.objects.filter(name__iexact=name).exists():
        messages.error(request, f'A role named “{name}” already exists.')
        return redirect('role-management')
    with transaction.atomic():
        role = Group.objects.create(name=name)
        permission_ids = request.POST.getlist('permissions')
        if permission_ids:
            role.permissions.set(Permission.objects.filter(pk__in=permission_ids))
        AuditEvent.objects.create(
            actor=request.user,
            action='ROLE_CREATED',
            object_type='Group',
            object_id=str(role.pk),
            path=request.path,
            method='POST',
            detail={'role': role.name, 'permissions': role.permissions.count()},
        )
    messages.success(
        request,
        f'Role “{role.name}” was created. Assign privileges below, then select it when creating or editing a user.',
    )
    return redirect(f'/roles/?role={role.pk}')


def _rename_role(request, role):
    name = _normalize_role_name(request.POST.get('name'))
    if not name:
        messages.error(request, 'Enter a role name.')
        return redirect(f'/roles/?role={role.pk}')
    if role.name in PROTECTED_ROLE_NAMES and name.casefold() != role.name.casefold():
        messages.error(request, f'“{role.name}” is a protected system role and cannot be renamed.')
        return redirect(f'/roles/?role={role.pk}')
    if Group.objects.filter(name__iexact=name).exclude(pk=role.pk).exists():
        messages.error(request, f'A role named “{name}” already exists.')
        return redirect(f'/roles/?role={role.pk}')
    previous = role.name
    role.name = name
    role.save(update_fields=['name'])
    AuditEvent.objects.create(
        actor=request.user,
        action='ROLE_RENAMED',
        object_type='Group',
        object_id=str(role.pk),
        path=request.path,
        method='POST',
        detail={'from': previous, 'to': role.name},
    )
    messages.success(request, f'Role renamed to “{role.name}”.')
    return redirect(f'/roles/?role={role.pk}')


def _delete_role(request, role):
    if role.name in PROTECTED_ROLE_NAMES:
        messages.error(request, f'“{role.name}” is a protected system role and cannot be deleted.')
        return redirect(f'/roles/?role={role.pk}')
    member_count = role.user_set.count()
    if member_count and request.POST.get('confirm_delete') != '1':
        messages.error(
            request,
            f'“{role.name}” is assigned to {member_count} user(s). Tick confirm deletion to remove it from those accounts and delete the role.',
        )
        return redirect(f'/roles/?role={role.pk}')
    role_name = role.name
    role_id = str(role.pk)
    role.delete()
    AuditEvent.objects.create(
        actor=request.user,
        action='ROLE_DELETED',
        object_type='Group',
        object_id=role_id,
        path=request.path,
        method='POST',
        detail={'role': role_name, 'members_cleared': member_count},
    )
    messages.success(request, f'Role “{role_name}” was deleted.')
    return redirect('role-management')


def handle_role_post(request):
    action = request.POST.get('action')
    if action == 'create_role':
        return _create_role(request)
    role = get_object_or_404(Group, pk=request.POST.get('role'))
    if action == 'rename_role':
        return _rename_role(request, role)
    if action == 'delete_role':
        return _delete_role(request, role)
    if action == 'save_privileges':
        if request.POST.get('privilege_form') != '1':
            messages.error(request, 'The privilege form was incomplete.')
            return redirect(f'/roles/?role={role.pk}')
        ids = request.POST.getlist('permissions')
        if not ids and request.POST.get('clear_privileges') != '1':
            messages.error(request, 'Select at least one privilege, or confirm that this role should have none.')
            return redirect(f'/roles/?role={role.pk}')
        permissions = Permission.objects.filter(pk__in=ids)
        role.permissions.set(permissions)
        AuditEvent.objects.create(actor=request.user, action='ROLE_PRIVILEGES_UPDATED', object_type='Group', object_id=str(role.pk), path=request.path, method='POST', detail={'role': role.name, 'count': permissions.count()})
        messages.success(request, f'{role.name} now has {permissions.count()} privileges. Everyone in this role is affected.')
        return redirect(f'/roles/?role={role.pk}')
    if action == 'add_member':
        person = User.objects.filter(pk=request.POST.get('user')).first()
        if person is None:
            messages.error(request, 'Choose a user.')
            return redirect(f'/roles/?role={role.pk}')
        person.groups.add(role)
        _audit(request, 'ROLE_MEMBER_ADDED', person, {'username': person.username, 'role': role.name})
        messages.success(request, f'{person.username} is now in {role.name}.')
        return redirect(f'/roles/?role={role.pk}')
    if action == 'remove_member':
        person = User.objects.filter(pk=request.POST.get('user')).first()
        if person is None:
            messages.error(request, 'That user was not found.')
            return redirect(f'/roles/?role={role.pk}')
        person.groups.remove(role)
        _audit(request, 'ROLE_MEMBER_REMOVED', person, {'username': person.username, 'role': role.name})
        messages.success(request, f'{person.username} was removed from {role.name}.')
        return redirect(f'/roles/?role={role.pk}')
    messages.error(request, 'Choose a role action.')
    return redirect('role-management')


def build_role_context(request):
    roles = list(Group.objects.prefetch_related('permissions__content_type', 'user_set').order_by('name'))
    selected = None
    selected_id = request.GET.get('role')
    if selected_id:
        selected = next((role for role in roles if str(role.pk) == selected_id), None)
    if selected is None and roles:
        selected = roles[0]
    permissions = Permission.objects.select_related('content_type').order_by('content_type__app_label', 'content_type__model', 'name')
    members = []
    member_ids = set()
    granted_ids = set()
    if selected:
        members = paginate(request, selected.user_set.order_by('username'), 'mpage')
        member_ids = set(selected.user_set.values_list('pk', flat=True))
        granted_ids = set(selected.permissions.values_list('pk', flat=True))
    return {
        'roles': roles,
        'selected': selected,
        'members': members,
        'candidates': User.objects.exclude(pk__in=member_ids).order_by('username'),
        'permissions': permissions,
        'granted_ids': granted_ids,
    }
