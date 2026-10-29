"""Managed specimen and container catalogues used by the laboratory test form."""

SPECIMEN_TYPE_NAMES = (
    'EDTA whole blood',
    'Whole blood',
    'Serum',
    'Plasma',
    'Urine',
    'Stool',
    'Sputum',
    'Swab',
    'Cerebrospinal fluid',
    'Semen',
    'Tissue',
)

CONTAINER_TYPE_NAMES = (
    'EDTA (lavender)',
    'Serum separator (gold)',
    'Citrate (blue)',
    'Fluoride (grey)',
    'Heparin (green)',
    'Plain tube (red)',
    'Urine container',
    'Sterile universal container',
    'Swab transport medium',
    'Blood culture bottle',
)


def sync_test_collection_labels(test):
    """Copy the chosen catalogue names onto the text fields collection and reports already read."""
    test.specimen = test.specimen_type.name if test.specimen_type_id else ''
    names = list(test.container_types.order_by('name').values_list('name', flat=True))
    test.container = ', '.join(names)[:80]
    test.save(update_fields=['specimen', 'container', 'updated_at'])


def refresh_tests_for_specimen(specimen):
    for test in specimen.tests.all():
        test.specimen = specimen.name[:80]
        test.save(update_fields=['specimen', 'updated_at'])


def refresh_tests_for_container(container):
    for test in container.tests.all():
        names = list(test.container_types.order_by('name').values_list('name', flat=True))
        test.container = ', '.join(names)[:80]
        test.save(update_fields=['container', 'updated_at'])
