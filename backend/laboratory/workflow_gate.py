"""Bridge laboratory actions to the versioned workflow engine."""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied, ValidationError


def enforce_workflow(lab_order, action, *, user=None, payload=None):
    """Raise DRF errors when the bound workflow forbids this transition."""
    try:
        from workflows.services import assert_transition
    except ImportError:
        return None
    try:
        return assert_transition(lab_order, action, user=user, payload=payload or {})
    except DjangoValidationError as exc:
        messages = getattr(exc, 'messages', None) or [str(exc)]
        raise ValidationError(' '.join(messages)) from exc
    except DjangoPermissionDenied as exc:
        raise PermissionDenied(str(exc)) from exc
