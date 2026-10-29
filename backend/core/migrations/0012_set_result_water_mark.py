from django.db import migrations, models
from django.db.models import Q


def seed_blank_watermarks(apps, schema_editor):
    CentreReportFormat = apps.get_model('core', 'CentreReportFormat')
    CentreReportFormat.objects.filter(Q(watermark_text='') | Q(watermark_text__isnull=True)).update(
        watermark_text='UNVERIFIED',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_audit_location_hint'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='centre',
            options={
                'ordering': ['name'],
                'permissions': [
                    ('manage_centre_users', 'Can manage centre user access'),
                    ('access_platform_controls', 'Can access sensitive platform configuration controls'),
                    ('set_result_water_mark', 'Can set the result watermark text'),
                ],
            },
        ),
        migrations.AlterField(
            model_name='centrereportformat',
            name='watermark_text',
            field=models.CharField(
                blank=True,
                default='UNVERIFIED',
                help_text='Drawn across signed results that are not yet dispatched. Requires set_result_water_mark to change.',
                max_length=80,
            ),
        ),
        migrations.RunPython(seed_blank_watermarks, migrations.RunPython.noop),
    ]
