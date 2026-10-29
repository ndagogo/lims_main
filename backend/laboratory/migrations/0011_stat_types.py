from django.db import migrations, models
import django.db.models.deletion


STAT_SEEDS = (
    ('Urgent', '#000000', '#ffffff', 10, False),
    ('Critical', '#dc3545', '#ffffff', 20, False),
    ('Normal', '#198754', '#ffffff', 30, True),
)


def seed_stat_types(apps, schema_editor):
    StatType = apps.get_model('laboratory', 'StatType')
    LabOrder = apps.get_model('laboratory', 'LabOrder')
    for name, color, text_color, sort_order, is_default in STAT_SEEDS:
        StatType.objects.get_or_create(
            name=name,
            defaults={
                'color': color,
                'text_color': text_color,
                'sort_order': sort_order,
                'is_default': is_default,
                'is_active': True,
            },
        )
    normal = StatType.objects.filter(name='Normal').first()
    if normal is not None:
        LabOrder.objects.filter(stat__isnull=True).update(stat=normal)


def unseed_stat_types(apps, schema_editor):
    StatType = apps.get_model('laboratory', 'StatType')
    StatType.objects.filter(name__in=[name for name, *_rest in STAT_SEEDS], lab_orders__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0010_specimen_and_container_types'),
    ]

    operations = [
        migrations.CreateModel(
            name='StatType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=40, unique=True)),
                ('color', models.CharField(default='#198754', max_length=7)),
                ('text_color', models.CharField(default='#ffffff', max_length=7)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_default', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['sort_order', 'name']},
        ),
        migrations.AddField(
            model_name='laborder',
            name='stat',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='lab_orders', to='laboratory.stattype',
            ),
        ),
        migrations.RunPython(seed_stat_types, unseed_stat_types),
    ]
