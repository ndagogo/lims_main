"""Standard laboratory units of measurement."""

DEFAULT_UNITS = [
    ('GDL', 'Grams per decilitre', 'g/dL'),
    ('GL', 'Grams per litre', 'g/L'),
    ('MGDL', 'Milligrams per decilitre', 'mg/dL'),
    ('MGL', 'Milligrams per litre', 'mg/L'),
    ('UGDL', 'Micrograms per decilitre', 'µg/dL'),
    ('UGL', 'Micrograms per litre', 'µg/L'),
    ('NGML', 'Nanograms per millilitre', 'ng/mL'),
    ('PGML', 'Picograms per millilitre', 'pg/mL'),
    ('MMOLL', 'Millimoles per litre', 'mmol/L'),
    ('UMOLL', 'Micromoles per litre', 'µmol/L'),
    ('NMOLL', 'Nanomoles per litre', 'nmol/L'),
    ('UL', 'Units per litre', 'U/L'),
    ('IUL', 'International units per litre', 'IU/L'),
    ('IUML', 'International units per millilitre', 'IU/mL'),
    ('MIUML', 'Milli-international units per millilitre', 'mIU/mL'),
    ('X109', 'Times 10 to the 9 per litre', '10^9/L'),
    ('X1012', 'Times 10 to the 12 per litre', '10^12/L'),
    ('CELLS', 'Cells per microlitre', 'cells/µL'),
    ('PCT', 'Percent', '%'),
    ('FL', 'Femtolitres', 'fL'),
    ('PG', 'Picograms', 'pg'),
    ('MMHR', 'Millimetres per hour', 'mm/hr'),
    ('SEC', 'Seconds', 'sec'),
    ('RATIO', 'Ratio', 'ratio'),
    ('ML', 'Millilitres', 'mL'),
    ('ULIT', 'Microlitres', 'µL'),
    ('CFUML', 'Colony forming units per millilitre', 'CFU/mL'),
    ('COPIES', 'Copies per millilitre', 'copies/mL'),
    ('MMHG', 'Millimetres of mercury', 'mmHg'),
    ('MOSM', 'Milliosmoles per kilogram', 'mOsm/kg'),
    ('TITRE', 'Titre', 'titre'),
    ('PH', 'pH', 'pH'),
    ('NONE', 'No unit', '—'),
]


def seed_units(apps, schema_editor):
    Unit = apps.get_model('laboratory', 'UnitOfMeasure')
    for code, name, symbol in DEFAULT_UNITS:
        Unit.objects.get_or_create(code=code, defaults={'name': name, 'symbol': symbol, 'is_active': True})


def backfill_format_codes(apps, schema_editor):
    ResultTemplate = apps.get_model('laboratory', 'ResultTemplate')
    n = 0
    for row in ResultTemplate.objects.filter(format_code='').order_by('id'):
        n += 1
        row.format_code = f'FMT{n:04d}'
        row.save(update_fields=['format_code'])
