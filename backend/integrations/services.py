"""Safe protocol helpers. Network listeners and vendor-specific framing belong in a separately deployed gateway."""
import re

def parse_hl7_obx(payload: str) -> list[dict]:
    """Extract basic OBX observations from HL7 v2 text without applying results to a patient record.

    This intentionally does not claim full HL7 conformance. Parsed observations must be mapped,
    matched to a sample, and reviewed by authorised laboratory staff before acceptance.
    """
    observations = []
    for line in re.split(r'[\r\n]+', payload or ''):
        fields = line.split('|')
        if not fields or fields[0] != 'OBX':
            continue
        # OBX-3 identifier, OBX-5 value, OBX-6 units, OBX-7 reference range, OBX-8 abnormal flag
        def get(index): return fields[index] if len(fields) > index else ''
        code = get(3).split('^')[0].strip()
        observations.append({'instrument_test_code': code, 'value': get(5), 'unit': get(6), 'reference_range': get(7), 'flag': get(8), 'value_type': get(2)})
    return observations

def match_sample_from_payload(analyzer, payload: str):
    """Find a centre sample whose 8-digit id appears in the instrument text."""
    from laboratory.models import Sample
    for token in re.findall(r'(?<!\d)(\d{8})(?!\d)', payload or ''):
        sample = Sample.objects.filter(sample_id=token, centre_id=analyzer.centre_id).first()
        if sample is not None:
            return sample
    return None


def mapped_result_values(analyzer, observations):
    """Pair parsed OBX rows with configured parameter mappings. Unmapped codes are returned, not written."""
    mappings = {
        row.instrument_test_code: row
        for row in analyzer.mappings.filter(enabled=True).select_related('parameter')
    }
    values = []
    unmapped = []
    for obs in observations or []:
        code = (obs.get('instrument_test_code') or '').strip()
        mapping = mappings.get(code)
        if mapping is None or not mapping.parameter_id:
            if code:
                unmapped.append(code)
            continue
        values.append({'parameter': mapping.parameter_id, 'value': obs.get('value') or '', 'flag': obs.get('flag') or ''})
    return values, unmapped


def redact_payload_for_logs(payload: str, limit: int = 500) -> str:
    """Return a short redacted excerpt; do not log full patient-linked analyzer messages."""
    text = (payload or '')[:limit]
    text = re.sub(r'(?im)^(PID\|[^\r\n]*)', 'PID|[REDACTED]', text)
    return text
