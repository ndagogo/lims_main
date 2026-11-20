"""Admin-managed patient titles used on registration and demographics."""
from __future__ import annotations

from patients.models import TITLE_CHOICES, PatientTitle


DEFAULT_TITLES = [
    (code, label, index)
    for index, (code, label) in enumerate(TITLE_CHOICES)
    if code
]


def ensure_default_titles():
    """Seed standard titles once; safe to call repeatedly."""
    for code, label, index in DEFAULT_TITLES:
        PatientTitle.objects.get_or_create(
            code=code,
            defaults={
                'label': label,
                'is_system': True,
                'is_active': True,
                'sort_order': index,
            },
        )


def title_choice_tuples(*, include_blank=True):
    ensure_default_titles()
    rows = list(PatientTitle.objects.filter(is_active=True).order_by('sort_order', 'label'))
    choices = [(row.code, row.label) for row in rows]
    if include_blank:
        return [('', '—')] + choices
    return choices


def is_valid_title(code):
    code = (code or '').strip()
    if not code:
        return True
    return PatientTitle.objects.filter(code=code, is_active=True).exists()
