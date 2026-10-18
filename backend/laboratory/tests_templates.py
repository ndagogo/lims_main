"""Result template field types, age/sex ranges, versioning, and auth freeze."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Centre, UserCentreAccess
from laboratory.cbc_seed import ensure_cbc_catalogue
from laboratory.models import (
    LabOrder, LabResult, ReferenceRange, ResultTemplate, ResultValue, Sample,
    SampleCodeConfig, ServiceGroup, Tariff, Test, TestParameter, TestPrice,
)
from laboratory.services import apply_calculated_parameters, evaluate_calculation, matching_range
from laboratory.templates_svc import (
    build_configuration_snapshot, clone_template_version, freeze_result_configuration,
    publish_template, resolve_entry_template,
)
from patients.models import Patient, Visit


class TemplateRequirementsTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code='TPL', name='Template Centre')
        SampleCodeConfig.objects.create(centre=self.centre, numeric_code='9')
        group = ServiceGroup.objects.create(name='Haematology', code='1')
        self.test = Test.objects.create(
            code='CBC', name='Complete Blood Count', department='HAEMATOLOGY',
            department_code='1', service_group=group, service_group_code='1', specimen='EDTA',
        )
        tariff = Tariff.objects.create(name='Standard')
        TestPrice.objects.create(tariff=tariff, test=self.test, price=Decimal('1000'))
        self.template = ensure_cbc_catalogue(publish=True)
        self.patient = Patient.objects.create(
            uhid='TPL-0001', first_name='Ada', last_name='Okeke',
            sex='F', date_of_birth=date(1990, 5, 1),
        )
        self.visit = Visit.objects.create(patient=self.patient, centre=self.centre, visit_id='V-TPL-1')
        self.order = LabOrder.objects.create(visit=self.visit, test=self.test, tariff=tariff, price=Decimal('1000'))
        self.sample = Sample(order=self.order, centre=self.centre, status='PROCESSING')
        self.sample.save()
        User = get_user_model()
        self.user = User.objects.create_user('tpl-user', password='x')
        UserCentreAccess.objects.create(user=self.user, centre=self.centre, can_view=True, can_edit=True, can_approve=True)

    def test_field_types_cover_requirement_set(self):
        types = set(self.test.parameters.values_list('result_type', flat=True))
        for needed in ('NUMBER', 'TEXT', 'MULTI_CHOICE', 'QUALITATIVE', 'TABLE', 'CALCULATED', 'COMMENT', 'SECTION'):
            self.assertIn(needed, types)
        # Single-select is covered by QUALITATIVE (controlled values) and/or CHOICE.
        self.assertTrue({'QUALITATIVE', 'CHOICE'} & types)
        self.assertTrue(self.test.parameters.filter(code='HGB', unit='g/dL').exists())
        self.assertTrue(self.test.parameters.filter(code='NEUT_ABS', result_type='CALCULATED').exists())

    def test_age_sex_ranges_are_catalogue_driven(self):
        hgb = self.test.parameters.get(code='HGB')
        matched = matching_range(hgb, self.patient)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.sex, 'F')
        self.assertEqual(matched.population_label, 'Adult female')
        # Newer version wins for same population match.
        ReferenceRange.objects.create(
            parameter=hgb, population_label='Adult female', sex='F',
            age_min_years=Decimal('18'), low=Decimal('11.5'), high=Decimal('15.0'),
            display_text='11.5–15.0', version=2, is_active=True,
        )
        matched2 = matching_range(hgb, self.patient)
        self.assertEqual(matched2.version, 2)
        self.assertEqual(matched2.display_text, '11.5–15.0')

    def test_calculation_and_template_versioning(self):
        self.assertEqual(evaluate_calculation('WBC * NEUT_PCT / 100', {'WBC': '8.0', 'NEUT_PCT': '50'}), '4')
        wbc = self.test.parameters.get(code='WBC')
        neut = self.test.parameters.get(code='NEUT_PCT')
        abs_n = self.test.parameters.get(code='NEUT_ABS')
        rows = [{'parameter': wbc.pk, 'value': '10'}, {'parameter': neut.pk, 'value': '40'}]
        apply_calculated_parameters(list(self.test.parameters.all()), rows)
        by_id = {int(r['parameter']): r['value'] for r in rows}
        self.assertEqual(by_id[abs_n.pk], '4.0')

        self.assertEqual(self.template.status, 'PUBLISHED')
        clone = clone_template_version(self.template)
        self.assertEqual(clone.version, 2)
        self.assertEqual(clone.status, 'DRAFT')
        self.assertEqual(clone.fields.count(), self.template.fields.count())
        publish_template(clone)
        self.template.refresh_from_db()
        self.assertEqual(self.template.status, 'RETIRED')
        self.assertEqual(resolve_entry_template(self.test).pk, clone.pk)

    def test_authentication_freezes_template_and_range_versions(self):
        hgb = self.test.parameters.get(code='HGB')
        result = LabResult.objects.create(
            sample=self.sample, template=self.template, status='REVIEWED',
            entered_by=self.user, narrative='Within expected limits.',
        )
        ResultValue.objects.create(
            result=result, parameter=hgb, value='13.2', flag='NORMAL',
            reference_display='12.0–15.5',
        )
        freeze_result_configuration(result)
        result.refresh_from_db()
        self.assertEqual(result.template_version, self.template.version)
        snap = result.configuration_snapshot
        self.assertEqual(snap['template']['version'], self.template.version)
        self.assertEqual(snap['template']['name'], 'Standard CBC')
        hgb_snap = next(p for p in snap['parameters'] if p['code'] == 'HGB')
        self.assertIsNotNone(hgb_snap['matched_range_version'])
        self.assertTrue(hgb_snap['reference_ranges'])
        # Snapshot is independent of later catalogue edits.
        hgb.unit = 'g/L'
        hgb.save(update_fields=['unit', 'updated_at'])
        snap2 = build_configuration_snapshot(result)
        live = next(p for p in snap2['parameters'] if p['code'] == 'HGB')
        self.assertEqual(live['unit'], 'g/L')
        self.assertEqual(hgb_snap['unit'], 'g/dL')
        self.assertEqual(result.configuration_snapshot['parameters'][
            next(i for i, p in enumerate(result.configuration_snapshot['parameters']) if p['code'] == 'HGB')
        ]['unit'], 'g/dL')
