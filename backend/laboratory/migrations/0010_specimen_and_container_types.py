from django.db import migrations, models
import django.db.models.deletion


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


def seed_collection_types(apps, schema_editor):
    SpecimenType = apps.get_model('laboratory', 'SpecimenType')
    ContainerType = apps.get_model('laboratory', 'ContainerType')
    Test = apps.get_model('laboratory', 'Test')
    for name in SPECIMEN_TYPE_NAMES:
        SpecimenType.objects.get_or_create(name=name)
    for name in CONTAINER_TYPE_NAMES:
        ContainerType.objects.get_or_create(name=name)
    for test in Test.objects.all().iterator():
        specimen_name = (test.specimen or '').strip()[:80]
        if specimen_name and not test.specimen_type_id:
            row, _created = SpecimenType.objects.get_or_create(name=specimen_name)
            test.specimen_type_id = row.pk
            test.save(update_fields=['specimen_type'])
        container_name = (test.container or '').strip()[:80]
        if container_name and not test.container_types.exists():
            row, _created = ContainerType.objects.get_or_create(name=container_name)
            test.container_types.add(row)


def unseed_collection_types(apps, schema_editor):
    SpecimenType = apps.get_model('laboratory', 'SpecimenType')
    ContainerType = apps.get_model('laboratory', 'ContainerType')
    SpecimenType.objects.filter(name__in=SPECIMEN_TYPE_NAMES, tests__isnull=True).delete()
    ContainerType.objects.filter(name__in=CONTAINER_TYPE_NAMES, tests__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0009_sample_recollection_print_reject'),
    ]

    operations = [
        migrations.CreateModel(
            name='SpecimenType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='ContainerType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='test',
            name='specimen_type',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='tests', to='laboratory.specimentype',
            ),
        ),
        migrations.AddField(
            model_name='test',
            name='container_types',
            field=models.ManyToManyField(blank=True, related_name='tests', to='laboratory.containertype'),
        ),
        migrations.AlterField(
            model_name='test',
            name='specimen',
            field=models.CharField(blank=True, help_text='Copied from the selected specimen type.', max_length=80),
        ),
        migrations.AlterField(
            model_name='test',
            name='container',
            field=models.CharField(blank=True, help_text='Copied from the selected container types.', max_length=80),
        ),
        migrations.RunPython(seed_collection_types, unseed_collection_types),
    ]
