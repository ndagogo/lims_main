"""Reference ranges, critical flags, and quality-control evaluation."""
from decimal import Decimal, InvalidOperation

from django.utils import timezone


def _sex(value):
    text = (value or '').strip().upper()
    if text in {'M', 'MALE'}:
        return 'M'
    if text in {'F', 'FEMALE'}:
        return 'F'
    return ''


def age_years(patient, on_date=None):
    if not patient or not patient.date_of_birth:
        return None
    today = on_date or timezone.localdate()
    return Decimal((today - patient.date_of_birth).days) / Decimal('365.25')


def matching_range(parameter, patient):
    rows = list(parameter.reference_ranges.all())
    if not rows:
        return None
    sex = _sex(getattr(patient, 'sex', ''))
    age = age_years(patient)

    def fits(row):
        if row.sex and row.sex != sex:
            return False
        if row.age_min_years is not None and (age is None or age < row.age_min_years):
            return False
        if row.age_max_years is not None and (age is None or age > row.age_max_years):
            return False
        return True

    matched = [row for row in rows if fits(row)]
    if not matched:
        return None
    matched.sort(key=lambda row: (0 if row.sex else 1, 0 if row.age_min_years is not None else 1))
    return matched[0]


def classify_value(parameter, raw, patient):
    row = matching_range(parameter, patient)
    if row is None:
        return '', ''
    text = row.display_text
    if not text and (row.low is not None or row.high is not None):
        low = '' if row.low is None else f'{row.low:f}'.rstrip('0').rstrip('.')
        high = '' if row.high is None else f'{row.high:f}'.rstrip('0').rstrip('.')
        text = f'{low}-{high}'
    try:
        number = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError):
        return '', text
    if row.critical_low is not None and number < row.critical_low:
        return 'CRITICAL_LOW', text
    if row.critical_high is not None and number > row.critical_high:
        return 'CRITICAL_HIGH', text
    if row.low is not None and number < row.low:
        return 'LOW', text
    if row.high is not None and number > row.high:
        return 'HIGH', text
    return 'NORMAL', text


def qc_is_acceptable(material, parameter, raw):
    target = material.targets.filter(parameter=parameter).first()
    if target is None:
        return True
    try:
        number = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError):
        return False
    if target.low is not None and number < target.low:
        return False
    if target.high is not None and number > target.high:
        return False
    return True
