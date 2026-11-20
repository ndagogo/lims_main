"""Dynamic sequenced rich-text sections for radiology reporting."""
from __future__ import annotations

import json
import re
from html import unescape

REPORT_SECTION_TYPES = [
    ('clinical_information', 'Clinical Information'),
    ('comparison', 'Comparison'),
    ('technique', 'Technique'),
    ('findings', 'Findings'),
    ('impression', 'Impression'),
    ('conclusion', 'Conclusion'),
    ('follow_up', 'Follow-Up'),
]

DEFAULT_SECTION_KEYS = (
    'clinical_information',
    'comparison',
    'technique',
    'findings',
)

FIELD_MAP = {
    'clinical_information': 'indication',
    'technique': 'technique',
    'findings': 'findings',
    'impression': 'impression',
    'follow_up': 'recommendations',
    'conclusion': 'impression',
}

_LABEL_BY_KEY = dict(REPORT_SECTION_TYPES)
_KEY_BY_LABEL = {label.lower(): key for key, label in REPORT_SECTION_TYPES}
_KEY_BY_LABEL.update({
    'clinical indication': 'clinical_information',
    'clinical info': 'clinical_information',
    'recommendations': 'follow_up',
    'recommendation': 'follow_up',
    'follow up': 'follow_up',
    'follow-up': 'follow_up',
    'exam': 'findings',
})


def section_type_choices():
    return list(REPORT_SECTION_TYPES)


def default_sections(*, indication='', technique='', findings='', impression='', recommendations=''):
    seeds = {
        'clinical_information': indication or '',
        'comparison': '',
        'technique': technique or '',
        'findings': findings or '',
        'impression': impression or '',
        'follow_up': recommendations or '',
    }
    rows = []
    for key in DEFAULT_SECTION_KEYS:
        rows.append({
            'key': key,
            'label': _LABEL_BY_KEY[key],
            'html': seeds.get(key) or '',
        })
    # If impression/recommendations already exist separately, append them.
    if (impression or '').strip() and 'impression' not in DEFAULT_SECTION_KEYS:
        rows.append({'key': 'impression', 'label': 'Impression', 'html': impression})
    if (recommendations or '').strip():
        rows.append({'key': 'follow_up', 'label': 'Follow-Up', 'html': recommendations})
    return rows


def normalize_sections(raw):
    """Accept list/dict/JSON string → cleaned ordered section list."""
    if raw is None or raw == '':
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(raw, dict):
        # Allow {key: html} maps; preserve insertion order.
        items = [{'key': k, 'html': v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    allowed = set(_LABEL_BY_KEY)
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip().lower().replace(' ', '_').replace('-', '_')
        label = str(item.get('label') or '').strip()
        if key not in allowed and label:
            key = _KEY_BY_LABEL.get(label.lower(), '')
        if key not in allowed:
            continue
        html = item.get('html')
        if html is None:
            html = item.get('content') or item.get('body') or ''
        cleaned.append({
            'key': key,
            'label': _LABEL_BY_KEY[key],
            'html': str(html or ''),
        })
    return cleaned


def compose_report_html(sections):
    """Build a single rich HTML document for PDF / findings storage."""
    parts = []
    for row in normalize_sections(sections):
        label = row['label'].upper()
        body = (row.get('html') or '').strip()
        if not body:
            parts.append(f'<p><strong>{label}:</strong></p>')
            continue
        # Avoid duplicating the heading if the body already starts with it.
        plain_start = re.sub(r'<[^>]+>', '', body)[:80].strip().upper()
        if plain_start.startswith(label):
            parts.append(body)
            continue
        if '<' not in body:
            body = f'<p>{escape_minimal(body)}</p>'
        parts.append(f'<p><strong>{label}:</strong></p>{body}')
    return '\n'.join(parts)


def escape_minimal(text):
    return (
        str(text or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('\n', '<br>')
    )


def map_sections_to_fields(sections):
    """Map section HTML into classic report fields (first match wins for impression)."""
    fields = {
        'indication': '',
        'technique': '',
        'findings': '',
        'impression': '',
        'recommendations': '',
    }
    for row in normalize_sections(sections):
        mapped = FIELD_MAP.get(row['key'])
        if not mapped:
            continue
        html = row.get('html') or ''
        if mapped == 'impression' and fields['impression']:
            continue
        fields[mapped] = html
    # Always keep a composed findings document for PDF/signing compatibility.
    composed = compose_report_html(sections)
    if composed.strip():
        fields['findings'] = composed
    return fields


def sections_from_report(report):
    """Load sequenced sections from a saved report (extra_values or legacy fields)."""
    extras = dict(getattr(report, 'extra_values', None) or {})
    stored = extras.get('report_sections')
    sections = normalize_sections(stored)
    if sections:
        return sections
    # Legacy single-document findings HTML: try to split by bold headings.
    findings = (report.findings or '').strip()
    if findings and _looks_like_sectioned_html(findings):
        split = _split_legacy_html(findings)
        if split:
            return split
    return default_sections(
        indication=report.indication or '',
        technique=report.technique or '',
        findings='' if _looks_like_sectioned_html(findings) else findings,
        impression=report.impression or '',
        recommendations=report.recommendations or '',
    )


def apply_sections_to_report_payload(data, sections):
    """Merge normalized sections into API/workspace payload dict."""
    sections = normalize_sections(sections)
    fields = map_sections_to_fields(sections)
    payload = dict(data or {})
    payload.update(fields)
    extras = dict(payload.get('extra_values') or {})
    if isinstance(extras, str):
        try:
            extras = json.loads(extras) or {}
        except (TypeError, ValueError, json.JSONDecodeError):
            extras = {}
    extras['report_sections'] = sections
    payload['extra_values'] = extras
    return payload


def default_template_sections(*, modality_label='', body_part=''):
    """Starter sequence for a new radiology report template."""
    part = (body_part or '—').strip() or '—'
    modality = (modality_label or 'Study').strip() or 'Study'
    return [
        {
            'key': 'clinical_information',
            'label': 'Clinical Information',
            'html': '<p>Clinical history not provided.</p>',
        },
        {
            'key': 'comparison',
            'label': 'Comparison',
            'html': '<p>No prior studies available for comparison.</p>',
        },
        {'key': 'technique', 'label': 'Technique', 'html': ''},
        {
            'key': 'findings',
            'label': 'Findings',
            'html': f'<p><strong>EXAM:</strong> {modality} — {part}</p>',
        },
        {'key': 'impression', 'label': 'Impression', 'html': ''},
    ]


def sections_from_template(template):
    """Load ordered sections from a RadiologyReportTemplate (new or legacy)."""
    stored = normalize_sections(getattr(template, 'report_sections', None))
    if stored:
        return stored
    body = (getattr(template, 'body_html', None) or '').strip()
    if body and _looks_like_sectioned_html(body):
        split = _split_legacy_html(body)
        if split:
            return split
    return default_sections(
        indication=getattr(template, 'indication_boilerplate', '') or '',
        technique=getattr(template, 'technique_boilerplate', '') or '',
        findings=(getattr(template, 'findings_boilerplate', '') or body or ''),
        impression=getattr(template, 'impression_boilerplate', '') or '',
        recommendations=getattr(template, 'recommendations_boilerplate', '') or '',
    )


def apply_sections_to_template(template, sections):
    """Persist sequenced sections onto a template and sync classic fields."""
    sections = normalize_sections(sections)
    template.report_sections = sections
    by_key = {row['key']: row.get('html') or '' for row in sections}
    template.indication_boilerplate = by_key.get('clinical_information', '')
    template.technique_boilerplate = by_key.get('technique', '')
    template.findings_boilerplate = by_key.get('findings', '')
    template.impression_boilerplate = by_key.get('impression') or by_key.get('conclusion', '')
    template.recommendations_boilerplate = by_key.get('follow_up', '')
    template.body_html = compose_report_html(sections)
    return template


def _looks_like_sectioned_html(text):
    plain = re.sub(r'<[^>]+>', ' ', text or '').upper()
    hits = sum(1 for label in (
        'CLINICAL INFORMATION', 'TECHNIQUE', 'FINDINGS', 'IMPRESSION', 'COMPARISON', 'FOLLOW',
    ) if label in plain)
    return hits >= 2


def _split_legacy_html(html):
    """Best-effort split of heading-labelled rich HTML into sections."""
    pattern = re.compile(
        r'(?:<p[^>]*>\s*)?<strong>\s*([^:<]{2,40})\s*:?\s*</strong>\s*(?:</p>)?',
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(html or ''))
    if len(matches) < 2:
        return []
    rows = []
    for index, match in enumerate(matches):
        label = unescape(match.group(1) or '').strip().rstrip(':').strip()
        key = _KEY_BY_LABEL.get(label.lower())
        if not key:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        body = (html[start:end] or '').strip()
        rows.append({'key': key, 'label': _LABEL_BY_KEY[key], 'html': body})
    return rows
