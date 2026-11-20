"""Consumables dashboard — catalogue, receipts, issues, and stock history."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.access import is_platform_admin, require_workspace
from core.models import Centre, UserCentreAccess
from core.paging import PAGE_SIZES, paginate
from inventory.models import ConsumableDepartment, ReagentSku, StockMovement
from inventory.services import adjust_stock, receive_stock


def _user_centres(user):
    if user.is_superuser or is_platform_admin(user):
        return Centre.objects.filter(is_active=True).order_by('code')
    ids = UserCentreAccess.objects.filter(user=user, can_view=True).values_list('centre_id', flat=True)
    return Centre.objects.filter(pk__in=ids, is_active=True).order_by('code')


def _can_manage_catalogue(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or is_platform_admin(user):
        return True
    return user.groups.filter(name__in=('Centre Administrator', 'Lab Manager')).exists()


def _scoped_skus(user):
    skus = ReagentSku.objects.select_related('centre').order_by('centre__code', 'department', 'name')
    if not user.is_superuser and not is_platform_admin(user):
        centre_ids = UserCentreAccess.objects.filter(user=user, can_view=True).values_list('centre_id', flat=True)
        skus = skus.filter(centre_id__in=centre_ids)
    return skus


@login_required
def inventory_workspace(request):
    require_workspace(request.user, 'laboratory', 'radiology')
    can_manage = _can_manage_catalogue(request.user)
    centres = _user_centres(request.user)

    if request.method == 'POST':
        return _handle_post(request, can_manage=can_manage)

    department = (request.GET.get('department') or '').upper()
    if department and department not in dict(ConsumableDepartment.CHOICES):
        department = ''
    centre_id = request.GET.get('centre') or ''
    query = (request.GET.get('q') or '').strip()
    tab = (request.GET.get('tab') or 'stock').lower()
    if tab not in ('stock', 'history', 'receive'):
        tab = 'stock'

    skus = _scoped_skus(request.user).filter(is_active=True)
    if department:
        skus = skus.filter(department=department)
    if centre_id:
        skus = skus.filter(centre_id=centre_id)
    if query:
        skus = skus.filter(
            Q(sku_code__icontains=query) | Q(name__icontains=query) | Q(category__icontains=query)
            | Q(description__icontains=query)
        )

    sku_page = paginate(request, skus, 'page')
    # Annotate display helpers without N+1 where possible via property still OK for page size.
    low_stock = [s for s in skus if s.is_low][:40]

    movements = StockMovement.objects.select_related(
        'sku', 'sku__centre', 'recorded_by',
    ).order_by('-created_at')
    if not request.user.is_superuser and not is_platform_admin(request.user):
        centre_ids = centres.values_list('pk', flat=True)
        movements = movements.filter(sku__centre_id__in=centre_ids)
    if department:
        movements = movements.filter(sku__department=department)
    if centre_id:
        movements = movements.filter(sku__centre_id=centre_id)
    if query:
        movements = movements.filter(
            Q(sku__sku_code__icontains=query) | Q(sku__name__icontains=query) | Q(note__icontains=query)
            | Q(lot_number__icontains=query)
        )
    history_page = paginate(request, movements, 'hpage')

    today = timezone.localdate()
    cutoff = today + timedelta(days=30)
    expiring = []
    for sku in skus[:200]:
        for mv in sku.movements.filter(
            expires_on__isnull=False, expires_on__lte=cutoff, quantity__gt=0,
        ).order_by('expires_on')[:2]:
            expiring.append({'sku': sku, 'movement': mv})
            if len(expiring) >= 20:
                break
        if len(expiring) >= 20:
            break

    dept_summary = []
    for code, label in ConsumableDepartment.CHOICES:
        qs = _scoped_skus(request.user).filter(is_active=True, department=code)
        if centre_id:
            qs = qs.filter(centre_id=centre_id)
        count = qs.count()
        if count:
            dept_summary.append({'code': code, 'label': label, 'count': count})

    return render(request, 'inventory/workspace.html', {
        'tab': tab,
        'skus': sku_page,
        'history': history_page,
        'low_stock': low_stock,
        'expiring': expiring,
        'centres': centres,
        'department': department,
        'centre_id': centre_id,
        'query': query,
        'department_choices': ConsumableDepartment.CHOICES,
        'dept_summary': dept_summary,
        'can_manage': can_manage,
        'page_sizes': PAGE_SIZES,
        'movement_types': StockMovement._meta.get_field('movement_type').choices,
    })


def _handle_post(request, *, can_manage):
    action = request.POST.get('action')
    allowed_centres = {str(c.pk) for c in _user_centres(request.user)}

    if action == 'create_sku':
        if not can_manage:
            messages.error(request, 'Only catalogue managers can create consumables.')
            return redirect('inventory-workspace')
        centre = get_object_or_404(Centre, pk=request.POST.get('centre'))
        if str(centre.pk) not in allowed_centres:
            messages.error(request, 'That centre is outside your access.')
            return redirect('inventory-workspace')
        code = (request.POST.get('sku_code') or '').strip()
        name = (request.POST.get('name') or '').strip()
        department = (request.POST.get('department') or '').upper()
        if department not in dict(ConsumableDepartment.CHOICES):
            department = ConsumableDepartment.LABORATORY
        if not code or not name:
            messages.error(request, 'SKU code and name are required.')
            return redirect('inventory-workspace')
        try:
            reorder = Decimal((request.POST.get('reorder_level') or '0').strip() or '0')
            initial_qty = Decimal((request.POST.get('initial_quantity') or '0').strip() or '0')
        except InvalidOperation:
            messages.error(request, 'Reorder level and initial quantity must be numbers.')
            return redirect('inventory-workspace')
        if initial_qty < 0:
            messages.error(request, 'Initial quantity cannot be negative.')
            return redirect('inventory-workspace')
        try:
            sku = ReagentSku.objects.create(
                centre=centre,
                department=department,
                sku_code=code,
                name=name,
                description=(request.POST.get('description') or '').strip(),
                category=(request.POST.get('category') or '').strip()[:80],
                unit=(request.POST.get('unit') or 'each').strip() or 'each',
                reorder_level=reorder,
                is_active=True,
            )
        except Exception:
            messages.error(request, 'That SKU already exists at this centre.')
            return redirect('inventory-workspace')
        if initial_qty > 0:
            receive_stock(
                sku=sku, quantity=initial_qty, user=request.user,
                note=f'Opening stock for {sku.sku_code}',
            )
        messages.success(
            request,
            f'Consumable {code} created under {dict(ConsumableDepartment.CHOICES)[department]}'
            + (f' with opening stock {initial_qty}.' if initial_qty else '.'),
        )
        return redirect(f'/inventory/?department={department}&tab=stock')

    if action == 'receive':
        sku = get_object_or_404(ReagentSku, pk=request.POST.get('sku'))
        if str(sku.centre_id) not in allowed_centres:
            messages.error(request, 'That consumable is outside your access.')
            return redirect('inventory-workspace')
        try:
            qty = Decimal((request.POST.get('quantity') or '0').strip() or '0')
            receive_stock(
                sku=sku,
                quantity=qty,
                user=request.user,
                lot_number=(request.POST.get('lot_number') or '').strip(),
                expires_on=parse_date(request.POST.get('expires_on') or '') or None,
                note=(request.POST.get('note') or '').strip(),
            )
        except (ValidationError, InvalidOperation) as exc:
            messages.error(request, getattr(exc, 'message', None) or str(exc))
            return redirect('/inventory/?tab=receive')
        messages.success(request, f'Received {qty} {sku.unit} of {sku.sku_code}.')
        return redirect(f'/inventory/?department={sku.department}&tab=history')

    if action == 'adjust':
        if not can_manage:
            messages.error(request, 'Only catalogue managers can adjust stock.')
            return redirect('inventory-workspace')
        sku = get_object_or_404(ReagentSku, pk=request.POST.get('sku'))
        if str(sku.centre_id) not in allowed_centres:
            messages.error(request, 'That consumable is outside your access.')
            return redirect('inventory-workspace')
        try:
            qty = Decimal((request.POST.get('quantity') or '0').strip() or '0')
            mtype = (request.POST.get('movement_type') or 'ADJUST').upper()
            if mtype not in ('ADJUST', 'EXPIRE', 'RETURN'):
                mtype = 'ADJUST'
            adjust_stock(
                sku=sku,
                quantity=qty,
                user=request.user,
                note=(request.POST.get('note') or '').strip(),
                movement_type=mtype,
            )
        except (ValidationError, InvalidOperation) as exc:
            messages.error(request, getattr(exc, 'message', None) or str(exc))
            return redirect('inventory-workspace')
        messages.success(request, f'Stock {mtype.lower()} recorded for {sku.sku_code}.')
        return redirect(f'/inventory/?department={sku.department}&tab=history')

    if action == 'toggle':
        if not can_manage:
            messages.error(request, 'Only catalogue managers can activate or deactivate consumables.')
            return redirect('inventory-workspace')
        sku = get_object_or_404(ReagentSku, pk=request.POST.get('sku'))
        if str(sku.centre_id) not in allowed_centres:
            messages.error(request, 'That consumable is outside your access.')
            return redirect('inventory-workspace')
        sku.is_active = not sku.is_active
        sku.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'{sku.sku_code} marked {"active" if sku.is_active else "inactive"}.')
        return redirect(f'/inventory/?department={sku.department}')

    return redirect('inventory-workspace')
