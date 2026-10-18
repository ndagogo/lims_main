"""Staff integration review UI for quarantined analyzer messages."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.access import LAB_MANAGER_ROLES, require_any_role, require_workspace
from core.models import AuditEvent
from integrations.models import AnalyzerMessage


@login_required
def integration_review(request):
    require_workspace(request.user, 'laboratory')
    qs = AnalyzerMessage.objects.filter(status='PENDING_REVIEW').select_related('analyzer__centre', 'sample').order_by('-received_at')
    if not request.user.is_superuser:
        qs = qs.filter(analyzer__centre__user_access__user=request.user, analyzer__centre__user_access__can_view=True)
    return render(request, 'integrations/review.html', {'messages_list': qs[:100]})


@login_required
@require_POST
def integration_review_action(request, pk):
    require_any_role(request.user, LAB_MANAGER_ROLES)
    msg = get_object_or_404(AnalyzerMessage, pk=pk)
    decision = request.POST.get('decision')
    if decision not in ('ACCEPTED', 'REJECTED'):
        messages.error(request, 'Choose Accept or Reject.')
        return redirect('integration-review')
    msg.status = decision
    msg.reviewed_by = request.user
    msg.reviewed_at = timezone.now()
    msg.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
    AuditEvent.objects.create(
        actor=request.user, action=f'ANALYZER_MESSAGE_{decision}', object_type='AnalyzerMessage',
        object_id=str(msg.pk), centre=msg.analyzer.centre,
    )
    messages.success(request, f'Message marked {decision.lower()}.')
    return redirect('integration-review')
