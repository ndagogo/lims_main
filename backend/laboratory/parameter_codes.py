"""Auto parameter codes: LP + first two letters of the test name + DDMMYY, plus a sequence when needed."""
from django.utils import timezone

from laboratory.models import TestParameter


def _letters(name):
    chars = ''.join(ch for ch in (name or '') if ch.isalpha()).upper()
    return (chars[:2] or 'XX').ljust(2, 'X')


def allocate_parameter_code(test, when=None):
    """LP + two letters of the test name + day, month, and two-digit year.

    The first parameter for that test on that day uses the bare code. Further
    parameters the same day append 02, 03, … so each code stays unique.
    """
    when = when or timezone.localdate()
    base = f'LP{_letters(test.name)}{when.strftime("%d%m%y")}'
    if not TestParameter.objects.filter(test=test, code__iexact=base).exists():
        return base
    n = 2
    while TestParameter.objects.filter(test=test, code__iexact=f'{base}{n:02d}').exists():
        n += 1
    return f'{base}{n:02d}'
