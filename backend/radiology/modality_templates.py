"""Seed configurable platform report templates for CT, X-ray, mammo, US, MRI."""
from radiology.models import RadiologyReportTemplate
from radiology.templates_svc import publish_template


def _body_html(exam_label, technique, findings='Findings:', impression='Impression:', comparison=''):
    cmp_line = comparison or 'No prior studies available for comparison.'
    return (
        f'<p><strong>EXAM:</strong> {exam_label}</p>'
        '<p><strong>CLINICAL INFORMATION:</strong> Clinical history not provided.</p>'
        f'<p><strong>TECHNIQUE:</strong> {technique}</p>'
        f'<p><strong>FINDINGS:</strong> {findings}</p>'
        f'<p><strong>IMPRESSION:</strong> {impression}</p>'
        f'<p><strong>COMPARISON:</strong> {cmp_line}</p>'
    )


DEFAULTS = [
    {
        'modality_type': 'XRAY',
        'name': 'Standard X-ray',
        'body_part': 'General',
        'indication_boilerplate': 'Clinical indication:\n',
        'technique_boilerplate': 'Technique: Plain radiograph(s) as labelled.\n',
        'findings_boilerplate': 'Findings:\n',
        'impression_boilerplate': 'Impression:\n',
        'recommendations_boilerplate': '',
        'body_html': _body_html(
            'Plain radiograph as labelled',
            'Plain radiograph(s) as labelled.',
            comparison='No prior studies available for comparison.',
        ),
        'extra_fields': [
            {'code': 'VIEWS', 'label': 'Views obtained', 'boilerplate': ''},
            {'code': 'COMPARISON', 'label': 'Comparison', 'boilerplate': 'No prior studies available for comparison.'},
        ],
    },
    {
        'modality_type': 'CT',
        'name': 'Standard CT',
        'body_part': 'General',
        'indication_boilerplate': 'Clinical indication / history:\n',
        'technique_boilerplate': (
            'Technique: Multidetector CT as protocolled. Contrast administration and phase timing as documented.\n'
        ),
        'findings_boilerplate': 'Findings:\n',
        'impression_boilerplate': 'Impression:\n',
        'recommendations_boilerplate': 'Recommendations:\n',
        'body_html': _body_html(
            'CT as protocolled',
            'Multidetector CT as protocolled. Contrast administration and phase timing as documented.',
            comparison='',
        ),
        'extra_fields': [
            {'code': 'CONTRAST', 'label': 'Contrast', 'boilerplate': 'IV contrast: '},
            {'code': 'DOSE', 'label': 'Dose / DLP note', 'boilerplate': ''},
            {'code': 'COMPARISON', 'label': 'Comparison', 'boilerplate': ''},
        ],
    },
    {
        'modality_type': 'MRI',
        'name': 'Standard MRI',
        'body_part': 'General',
        'indication_boilerplate': 'Clinical indication:\n',
        'technique_boilerplate': 'Technique: Multiplanar MRI sequences as protocolled.\n',
        'findings_boilerplate': 'Findings:\n',
        'impression_boilerplate': 'Impression:\n',
        'recommendations_boilerplate': 'Recommendations:\n',
        'body_html': _body_html(
            'MRI as protocolled',
            'Multiplanar MRI sequences as protocolled.',
        ),
        'extra_fields': [
            {'code': 'SEQUENCES', 'label': 'Sequences', 'boilerplate': ''},
            {'code': 'CONTRAST', 'label': 'Gadolinium', 'boilerplate': 'Contrast: '},
            {'code': 'COMPARISON', 'label': 'Comparison', 'boilerplate': ''},
        ],
    },
    {
        'modality_type': 'US',
        'name': 'Standard Ultrasound',
        'body_part': 'General',
        'indication_boilerplate': 'Clinical indication:\n',
        'technique_boilerplate': 'Technique: Real-time ultrasound with greyscale ± Doppler as indicated.\n',
        'findings_boilerplate': 'Findings:\n',
        'impression_boilerplate': 'Impression:\n',
        'recommendations_boilerplate': '',
        'body_html': _body_html(
            'Ultrasound as indicated',
            'Real-time ultrasound with greyscale ± Doppler as indicated.',
        ),
        'extra_fields': [
            {'code': 'TRANSDUCER', 'label': 'Transducer / approach', 'boilerplate': ''},
            {'code': 'COMPARISON', 'label': 'Comparison', 'boilerplate': ''},
        ],
    },
    {
        'modality_type': 'MAMMO',
        'name': 'Standard Mammography',
        'body_part': 'Breast',
        'indication_boilerplate': 'Clinical indication / screening or diagnostic:\n',
        'technique_boilerplate': 'Technique: Digital mammography views as labelled.\n',
        'findings_boilerplate': 'Findings:\nBreast composition:\n',
        'impression_boilerplate': 'Impression:\n',
        'recommendations_boilerplate': 'Recommendations / follow-up:\n',
        'body_html': _body_html(
            'Digital mammography as labelled',
            'Digital mammography views as labelled.',
            findings='Breast composition:',
        ),
        'extra_fields': [
            {'code': 'BI_RADS', 'label': 'BI-RADS category', 'boilerplate': ''},
            {'code': 'BREAST_DENSITY', 'label': 'Breast density', 'boilerplate': ''},
            {'code': 'COMPARISON', 'label': 'Comparison', 'boilerplate': ''},
        ],
    },
]


def ensure_modality_report_templates(*, publish=True, user=None):
    created = []
    for spec in DEFAULTS:
        row, was_created = RadiologyReportTemplate.objects.get_or_create(
            modality_type=spec['modality_type'],
            name=spec['name'],
            version=1,
            owner=None,
            defaults={
                'scope': 'PLATFORM',
                'status': 'DRAFT',
                'body_part': spec.get('body_part', 'General'),
                'visibility': 'PUBLIC',
                'indication_boilerplate': spec['indication_boilerplate'],
                'technique_boilerplate': spec['technique_boilerplate'],
                'findings_boilerplate': spec['findings_boilerplate'],
                'impression_boilerplate': spec['impression_boilerplate'],
                'recommendations_boilerplate': spec['recommendations_boilerplate'],
                'body_html': spec.get('body_html', ''),
                'extra_fields': spec['extra_fields'],
                'report_header': f"{spec['name']} report",
                'is_active': True,
            },
        )
        if not was_created:
            # Keep extra_fields / indication / technique current for demo seeds.
            row.indication_boilerplate = spec['indication_boilerplate']
            row.technique_boilerplate = spec['technique_boilerplate']
            row.findings_boilerplate = spec['findings_boilerplate']
            row.impression_boilerplate = spec['impression_boilerplate']
            row.recommendations_boilerplate = spec['recommendations_boilerplate']
            row.extra_fields = spec['extra_fields']
            if not (row.body_html or '').strip():
                row.body_html = spec.get('body_html', '')
            if not (row.body_part or '').strip():
                row.body_part = spec.get('body_part', 'General')
            row.save()
        if publish and row.status != 'PUBLISHED':
            publish_template(row, user)
        created.append(row)
    return created
