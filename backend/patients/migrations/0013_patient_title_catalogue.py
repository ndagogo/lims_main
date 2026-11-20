from django.db import migrations, models


DEFAULT_TITLES = [
    ('Mr', 'Mr', 1),
    ('Mrs', 'Mrs', 2),
    ('Miss', 'Miss', 3),
    ('Ms', 'Ms', 4),
    ('Dr', 'Dr', 5),
    ('Chief', 'Chief', 6),
    ('Alhaji', 'Alhaji', 7),
    ('Alhaja', 'Alhaja', 8),
    ('Prof', 'Prof', 9),
    ('Master', 'Master', 10),
]


def seed_titles(apps, schema_editor):
    PatientTitle = apps.get_model('patients', 'PatientTitle')
    for code, label, sort_order in DEFAULT_TITLES:
        PatientTitle.objects.get_or_create(
            code=code,
            defaults={
                'label': label,
                'is_system': True,
                'is_active': True,
                'sort_order': sort_order,
            },
        )


def unseed_titles(apps, schema_editor):
    PatientTitle = apps.get_model('patients', 'PatientTitle')
    PatientTitle.objects.filter(code__in=[row[0] for row in DEFAULT_TITLES], is_system=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('patients', '0012_backfill_wallet_ledger_created_by'),
    ]

    operations = [
        migrations.CreateModel(
            name='PatientTitle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('label', models.CharField(max_length=40)),
                ('is_system', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'ordering': ['sort_order', 'label'],
            },
        ),
        migrations.AlterField(
            model_name='patient',
            name='title',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Honorific from PatientTitle catalogue (admin-managed).',
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_titles, unseed_titles),
    ]
