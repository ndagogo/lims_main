"""Registration stats such as Urgent, Critical, and Normal."""
import re

from django.core.exceptions import ValidationError

from laboratory.models import StatType

HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def clean_stat_color(value, label):
    color = (value or '').strip().lower()
    if not HEX_COLOR.match(color):
        raise ValidationError(f'{label} must be a colour such as #000000.')
    return color


def default_stat():
    row = StatType.objects.filter(is_active=True, is_default=True).order_by('sort_order', 'name').first()
    if row is None:
        row = StatType.objects.filter(is_active=True).order_by('sort_order', 'name').first()
    return row


def stat_for_choice(stat_id):
    if stat_id:
        row = StatType.objects.filter(pk=stat_id, is_active=True).first()
        if row is None:
            raise ValidationError('Choose an active stat for each test.')
        return row
    row = default_stat()
    if row is None:
        raise ValidationError('No stat types are configured. A platform administrator must add them.')
    return row
