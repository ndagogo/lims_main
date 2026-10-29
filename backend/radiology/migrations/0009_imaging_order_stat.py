from django.db import migrations, models
import django.db.models.deletion


def backfill_imaging_stats(apps, schema_editor):
    StatType = apps.get_model('laboratory', 'StatType')
    ImagingOrder = apps.get_model('radiology', 'ImagingOrder')
    normal = StatType.objects.filter(name='Normal').first()
    if normal is not None:
        ImagingOrder.objects.filter(stat__isnull=True).update(stat=normal)


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0011_stat_types'),
        ('radiology', '0008_exam_pacs_accept_supplies'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagingorder',
            name='stat',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='imaging_orders', to='laboratory.stattype',
            ),
        ),
        migrations.RunPython(backfill_imaging_stats, migrations.RunPython.noop),
    ]
