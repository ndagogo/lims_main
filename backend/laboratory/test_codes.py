"""Immutable auto test codes: 2 letters of name + 2 letters of centre/org + 5 digits."""
from __future__ import annotations

import re

from django.db import transaction

from core.models import SequenceCounter


_LETTER_RE = re.compile(r'[^A-Za-z]')


def _letters(value: str, count: int) -> str:
    cleaned = _LETTER_RE.sub('', value or '').upper()
    if len(cleaned) >= count:
        return cleaned[:count]
    return (cleaned + ('X' * count))[:count]


def allocate_test_code(test_name: str, *, centre=None) -> str:
    """Build CODE = NNCC##### where NN=test name, CC=centre/org, #####=serial."""
    from core.organization import organization_short_name

    name_part = _letters(test_name, 2)
    if centre is not None and getattr(centre, 'code', ''):
        centre_part = _letters(centre.code, 2)
    elif centre is not None and getattr(centre, 'name', ''):
        centre_part = _letters(centre.name, 2)
    else:
        centre_part = _letters(organization_short_name() or 'OR', 2)
    prefix = f'{name_part}{centre_part}'
    with transaction.atomic():
        counter, _ = SequenceCounter.objects.select_for_update().get_or_create(
            key=f'test-code-{prefix}',
        )
        counter.value += 1
        counter.save(update_fields=['value'])
        serial = counter.value
        if serial > 99999:
            raise ValueError(f'Test code serial exhausted for prefix {prefix}.')
        return f'{prefix}{serial:05d}'
