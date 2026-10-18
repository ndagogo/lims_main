"""Reference ranges, critical flags, calculations, and quality-control evaluation."""
from __future__ import annotations

import ast
import operator
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
    """Pick the laboratory-approved active range for this patient.

    Prefers higher ``version``, then sex-specific, then age-bounded rows.
    Ranges are never hardcoded in the frontend — they come from ReferenceRange.
    """
    rows = list(parameter.reference_ranges.filter(is_active=True))
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
    matched.sort(
        key=lambda row: (
            -int(row.version or 1),
            0 if row.sex else 1,
            0 if row.age_min_years is not None or row.age_max_years is not None else 1,
            0 if row.population_label else 1,
        )
    )
    return matched[0]


def range_display(row, fallback=''):
    if row is None:
        return fallback or ''
    if row.display_text:
        return row.display_text
    if row.low is not None or row.high is not None:
        low = '' if row.low is None else f'{row.low:f}'.rstrip('0').rstrip('.')
        high = '' if row.high is None else f'{row.high:f}'.rstrip('0').rstrip('.')
        return f'{low}-{high}'
    return fallback or ''


def classify_value(parameter, raw, patient):
    row = matching_range(parameter, patient)
    if row is None:
        return '', range_display(None, parameter.reference_range)
    text = range_display(row, parameter.reference_range)
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


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate_calculation(expression: str, value_by_code: dict) -> str | None:
    """Safely evaluate a simple arithmetic expression using sibling parameter codes."""
    expr = (expression or '').strip()
    if not expr:
        return None
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        return None

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            raw = value_by_code.get(node.id)
            if raw is None or str(raw).strip() == '':
                raise ValueError('missing operand')
            return Decimal(str(raw).strip())
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](_eval(node.operand))
        raise ValueError('unsupported expression')

    try:
        result = _eval(tree)
    except (ValueError, InvalidOperation, ZeroDivisionError, TypeError, KeyError):
        return None
    if not isinstance(result, Decimal):
        result = Decimal(str(result))
    return format(result.normalize(), 'f')


def format_numeric(raw, decimal_places):
    if raw is None or str(raw).strip() == '':
        return ''
    if decimal_places is None:
        return str(raw).strip()
    try:
        number = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError):
        return str(raw).strip()
    quant = Decimal('1').scaleb(-int(decimal_places))
    return format(number.quantize(quant, rounding=ROUND_HALF_UP), 'f')


def apply_calculated_parameters(parameters, value_rows):
    """Fill CALCULATED parameter values from sibling codes; mutate value_rows in place.

    ``value_rows`` is a list of dicts with ``parameter`` (pk) and ``value``.
    """
    by_id = {int(p.pk): p for p in parameters}
    values = {int(row['parameter']): str(row.get('value', '')) for row in value_rows}
    code_map = {}
    for pk, param in by_id.items():
        code_map[param.code] = values.get(pk, '')

    changed = True
    guard = 0
    while changed and guard < 8:
        changed = False
        guard += 1
        for param in parameters:
            if param.result_type != 'CALCULATED' or not param.calculation:
                continue
            computed = evaluate_calculation(param.calculation, code_map)
            if computed is None:
                continue
            computed = format_numeric(computed, param.decimal_places)
            if values.get(param.pk) != computed:
                values[param.pk] = computed
                code_map[param.code] = computed
                changed = True

    existing = {int(row['parameter']): row for row in value_rows}
    for pk, value in values.items():
        if pk in existing:
            existing[pk]['value'] = value
        else:
            value_rows.append({'parameter': pk, 'value': value})
    return value_rows


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
