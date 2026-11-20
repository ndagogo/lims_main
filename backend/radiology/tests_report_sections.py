from django.test import SimpleTestCase

from radiology.report_sections import (
    apply_sections_to_report_payload,
    compose_report_html,
    default_sections,
    normalize_sections,
)
from radiology.reports import (
    _extra_rows,
    _report_sections,
    _strip_leading_section_label,
    _study_title,
)


class ReportSectionsTests(SimpleTestCase):
    def test_default_sequence_and_compose(self):
        rows = default_sections(indication='Trauma', findings='No fracture')
        keys = [row['key'] for row in rows]
        self.assertEqual(keys[:4], [
            'clinical_information', 'comparison', 'technique', 'findings',
        ])
        html = compose_report_html(rows)
        self.assertIn('CLINICAL INFORMATION', html)
        self.assertIn('Trauma', html)
        self.assertIn('FINDINGS', html)
        self.assertIn('No fracture', html)

    def test_payload_stores_ordered_sections(self):
        payload = apply_sections_to_report_payload({}, [
            {'key': 'impression', 'html': '<p>Normal study</p>'},
            {'key': 'findings', 'html': '<p>Clear lungs</p>'},
        ])
        self.assertEqual(
            [row['key'] for row in payload['extra_values']['report_sections']],
            ['impression', 'findings'],
        )
        self.assertIn('IMPRESSION', payload['findings'])
        self.assertIn('Normal study', payload['impression'])

    def test_normalize_accepts_json_string(self):
        rows = normalize_sections('[{"key":"follow_up","html":"Review in 6 weeks"}]')
        self.assertEqual(rows[0]['key'], 'follow_up')
        self.assertEqual(rows[0]['label'], 'Follow-Up')

    def test_extra_rows_never_dumps_report_sections(self):
        class _R:
            extra_values = {
                'report_sections': [
                    {'key': 'findings', 'label': 'Findings', 'html': '<p>Clear</p>'},
                ],
                'BI_RADS': '2',
            }

        rows = _extra_rows({'extra_values': _R.extra_values}, _R())
        labels = [label.lower() for label, _ in rows]
        self.assertNotIn('report sections', labels)
        self.assertTrue(any(label.replace(' ', '_') == 'bi_rads' for label in labels))

    def test_report_sections_render_as_separate_blocks(self):
        class _R:
            indication = ''
            technique = ''
            findings = '<p><strong>CLINICAL INFORMATION:</strong></p>Pain'
            impression = ''
            recommendations = ''
            addendum = ''
            extra_values = {
                'report_sections': [
                    {'key': 'clinical_information', 'label': 'Clinical Information', 'html': 'Pain today'},
                    {'key': 'technique', 'label': 'Technique', 'html': 'Technique: Standard views'},
                    {'key': 'findings', 'label': 'Findings', 'html': 'No fracture'},
                    {'key': 'impression', 'label': 'Impression', 'html': 'Normal study'},
                ],
            }

        blocks = _report_sections(_R(), {})
        self.assertEqual(
            [heading for heading, _ in blocks],
            ['Clinical Information', 'Technique', 'Findings', 'Impression'],
        )
        self.assertEqual(blocks[1][1], 'Standard views')

    def test_study_title_ignores_procedure_name(self):
        class _Modality:
            def get_modality_type_display(self):
                return 'X-ray'

        class _Order:
            modality = _Modality()
            procedure_id = 1

            class procedure:
                name = 'Abdomen X-ray'

        class _Exam:
            order = _Order()

        class _Report:
            exam = _Exam()

        self.assertEqual(_study_title(_Report(), {'title_radiology': 'Radiology Report'}), 'Radiology Report')
        self.assertEqual(_study_title(_Report(), {}), 'X-ray report')
        self.assertNotEqual(_study_title(_Report(), {}), 'Abdomen X-ray')

    def test_strip_leading_technique_label(self):
        self.assertEqual(
            _strip_leading_section_label('Technique: Standard views', 'Technique'),
            'Standard views',
        )
