"""Tariff code: TRF_<ORG>_<BRANCH><DD><MM><serial> style unique identifiers."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import SequenceCounter
from core.organization import organization_short_name


def allocate_tariff_code(*, centre=None) -> str:
    org = (organization_short_name() or 'ORG').upper().replace(' ', '')[:8]
    branch = ''
    if centre is not None:
        branch = (centre.code or '').upper().replace(' ', '')[:6]
    today = timezone.localdate()
    day_month = f'{today.day:02d}{today.month:02d}'
    key = f'tariff-code-{org}-{branch or "X"}-{day_month}'
    with transaction.atomic():
        counter, _ = SequenceCounter.objects.select_for_update().get_or_create(key=key)
        counter.value += 1
        counter.save(update_fields=['value'])
        serial = f'{counter.value:02d}'
    parts = ['TRF', org]
    if branch:
        parts.append(f'{branch}{day_month}{serial}')
    else:
        parts.append(f'{day_month}{serial}')
    return '_'.join(parts)
