from django.db import migrations, models


def backfill_tariff_codes(apps, schema_editor):
    Tariff = apps.get_model('laboratory', 'Tariff')
    SequenceCounter = apps.get_model('core', 'SequenceCounter')
    for index, tariff in enumerate(Tariff.objects.all().order_by('pk'), start=1):
        if tariff.code:
            continue
        code = f'TRF_LEGACY_{index:04d}'
        while Tariff.objects.filter(code=code).exists():
            index += 1
            code = f'TRF_LEGACY_{index:04d}'
        tariff.code = code
        if not tariff.status:
            tariff.status = 'APPROVAL' if tariff.is_active else 'INACTIVE'
        tariff.save(update_fields=['code', 'status'])
    SequenceCounter.objects.get_or_create(key='tariff-code-backfill', defaults={'value': Tariff.objects.count()})


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('laboratory', '0013_enterprise_clients_channels_tariffs'),
        ('core', '0016_resultledger_collector_id'),
    ]

    operations = [
        migrations.RunPython(backfill_tariff_codes, noop_reverse),
        migrations.AlterField(
            model_name='tariff',
            name='code',
            field=models.CharField(blank=True, default='', max_length=40, unique=True),
        ),
    ]
