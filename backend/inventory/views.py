from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from core.access import is_platform_admin, require_workspace
from inventory.models import ReagentSku


@login_required
def inventory_workspace(request):
    require_workspace(request.user, 'laboratory')
    skus = ReagentSku.objects.filter(is_active=True).select_related('centre').order_by('centre__code', 'sku_code')
    if not request.user.is_superuser and not is_platform_admin(request.user):
        centre_ids = request.user.centre_access.filter(can_view=True).values_list('centre_id', flat=True)
        skus = skus.filter(centre_id__in=centre_ids)
    low = [s for s in skus if s.on_hand <= s.reorder_level]
    expiring = []
    today = timezone.localdate()
    cutoff = today + timedelta(days=30)
    for sku in skus:
        for mv in sku.movements.filter(expires_on__isnull=False, expires_on__lte=cutoff).order_by('expires_on')[:3]:
            expiring.append({'sku': sku, 'movement': mv})
    return render(request, 'inventory/workspace.html', {
        'skus': skus[:100], 'low_stock': low[:20], 'expiring': expiring[:20],
    })