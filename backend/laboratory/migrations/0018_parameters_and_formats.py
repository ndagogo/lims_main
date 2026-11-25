from django.db import migrations, models
import django.db.models.deletion


def seed_and_backfill(apps, schema_editor):
    from laboratory.uom_seed import backfill_format_codes, seed_units
    seed_units(apps, schema_editor)
    backfill_format_codes(apps, schema_editor)
    ResultTemplate = apps.get_model('laboratory', 'ResultTemplate')
    SequenceCounter = apps.get_model('core', 'SequenceCounter')
    highest = 0
    for code in ResultTemplate.objects.values_list('format_code', flat=True):
        digits = ''.join(ch for ch in (code or '') if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    counter, _ = SequenceCounter.objects.get_or_create(key='test-format', defaults={'value': 0})
    if counter.value < highest:
        counter.value = highest
        counter.save(update_fields=['value'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('laboratory', '0017_samplebatch_department_optional'),
    ]

    operations = [
        migrations.CreateModel(
            name='UnitOfMeasure',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('name', models.CharField(max_length=80)),
                ('symbol', models.CharField(max_length=40)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='testparameter',
            name='unit_of_measure',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='parameters', to='laboratory.unitofmeasure'),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='text_size',
            field=models.CharField(choices=[('BIG', 'Big'), ('SMALL', 'Small'), ('SAME', 'Same')], default='SAME', max_length=8),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='result_position',
            field=models.CharField(choices=[('BENEATH', 'Beneath'), ('BESIDE', 'Beside')], default='BENEATH', max_length=8),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='note',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='barcode_suffix',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='method_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='show_in_report',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='parameter_in_bold',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='format_code',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='report_title',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='column_caption_1',
            field=models.CharField(blank=True, default='Test', max_length=80),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='column_caption_2',
            field=models.CharField(blank=True, default='Result', max_length=80),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='column_caption_3',
            field=models.CharField(blank=True, default='Units', max_length=80),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='column_caption_4',
            field=models.CharField(blank=True, default='Biological Reference Interval', max_length=80),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='interpretation',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='is_default_male',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='is_default_female',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='resulttemplatefield',
            name='is_gross',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(seed_and_backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='resulttemplate',
            name='format_code',
            field=models.CharField(blank=True, default='', max_length=20, unique=True),
        ),
    ]
