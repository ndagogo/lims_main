"""Normalize and enforce unique laboratory test names."""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower

DUPLICATE_TEST_NAME_MESSAGE = 'This test already exist. You can not proceed!'


def normalize_test_name(name: str) -> str:
    return re.sub(r'\s+', ' ', (name or '').strip())


def test_name_taken(name: str, *, exclude_pk=None) -> bool:
    from laboratory.models import Test

    cleaned = normalize_test_name(name)
    if not cleaned:
        return False
    qs = Test.objects.filter(name__iexact=cleaned)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def assert_test_name_available(name: str, *, exclude_pk=None) -> str:
    cleaned = normalize_test_name(name)
    if not cleaned:
        raise ValidationError('Test name is required.')
    if test_name_taken(cleaned, exclude_pk=exclude_pk):
        raise ValidationError(DUPLICATE_TEST_NAME_MESSAGE)
    return cleaned


def _usage_score(test) -> tuple:
    """Prefer the test with more clinical usage / richer catalogue data."""
    return (
        getattr(test, 'order_count', 0) or 0,
        getattr(test, 'param_count', 0) or 0,
        getattr(test, 'price_count', 0) or 0,
        1 if test.is_active else 0,
        -(test.pk or 0),
    )


def _repoint_or_drop(model, *, dup, keeper, unique_fields=None):
    """Move FK rows from dup -> keeper; drop rows that would violate uniqueness."""
    qs = model.objects.filter(test=dup)
    if not unique_fields:
        qs.update(test=keeper)
        return
    for row in list(qs):
        clash = {field: getattr(row, field) for field in unique_fields}
        if model.objects.filter(test=keeper, **clash).exists():
            row.delete()
        else:
            row.test = keeper
            row.save(update_fields=['test'])


@transaction.atomic
def dedupe_duplicate_test_names(*, dry_run: bool = False) -> list[dict]:
    """
    Collapse case-insensitive duplicate Test.name rows onto one keeper.
    Related rows are re-pointed when possible; colliding unique prices are dropped.
    """
    from laboratory.models import (
        LabOrder, QcMaterial, ResultTemplate, TatRule, Test, TestParameter, TestPrice,
    )

    try:
        from billing.models import InvoiceItem
    except Exception:  # pragma: no cover
        InvoiceItem = None
    try:
        from integrations.models import AnalyzerTestMapping
    except Exception:
        AnalyzerTestMapping = None

    groups = (
        Test.objects.annotate(name_key=Lower('name'))
        .values('name_key')
        .annotate(c=Count('id'))
        .filter(c__gt=1)
        .order_by('name_key')
    )
    report = []
    for row in groups:
        key = row['name_key']
        twins = list(
            Test.objects.filter(name__iexact=key)
            .annotate(
                order_count=Count('laborder', distinct=True),
                param_count=Count('parameters', distinct=True),
                price_count=Count('testprice', distinct=True),
            )
            .order_by('pk')
        )
        if len(twins) < 2:
            continue
        keeper = max(twins, key=_usage_score)
        duplicates = [t for t in twins if t.pk != keeper.pk]
        removed = []
        for dup in duplicates:
            if dry_run:
                removed.append({'pk': dup.pk, 'code': dup.code})
                continue
            LabOrder.objects.filter(test=dup).update(test=keeper)
            _repoint_or_drop(TestParameter, dup=dup, keeper=keeper, unique_fields=['code'])
            _repoint_or_drop(TestPrice, dup=dup, keeper=keeper, unique_fields=['tariff_id'])
            _repoint_or_drop(ResultTemplate, dup=dup, keeper=keeper)
            _repoint_or_drop(QcMaterial, dup=dup, keeper=keeper)
            _repoint_or_drop(TatRule, dup=dup, keeper=keeper)
            if InvoiceItem is not None:
                InvoiceItem.objects.filter(test=dup).update(test=keeper)
            if AnalyzerTestMapping is not None:
                AnalyzerTestMapping.objects.filter(test=dup).update(test=keeper)
            # Clear default_template pointers that still target the duplicate.
            Test.objects.filter(default_template__test=dup).update(default_template=None)
            if hasattr(dup, 'container_types'):
                dup.container_types.clear()
            removed.append({'pk': dup.pk, 'code': dup.code})
            dup.delete()
        cleaned = normalize_test_name(keeper.name)
        if not dry_run and keeper.name != cleaned:
            keeper.name = cleaned
            keeper.save(update_fields=['name'])
        report.append({
            'name': cleaned or keeper.name,
            'keeper_pk': keeper.pk,
            'keeper_code': keeper.code,
            'removed': removed,
        })
    return report
