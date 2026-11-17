from django.db import migrations, models
from django.db.models.functions import Lower


def dedupe_tests(apps, schema_editor):
    Test = apps.get_model('laboratory', 'Test')
    TestPrice = apps.get_model('laboratory', 'TestPrice')
    TestParameter = apps.get_model('laboratory', 'TestParameter')
    LabOrder = apps.get_model('laboratory', 'LabOrder')
    ResultTemplate = apps.get_model('laboratory', 'ResultTemplate')
    QcMaterial = apps.get_model('laboratory', 'QcMaterial')
    TatRule = apps.get_model('laboratory', 'TatRule')
    from django.db.models import Count
    from django.db.models.functions import Lower as LowerFn
    import re

    def normalize(name):
        return re.sub(r'\s+', ' ', (name or '').strip())

    groups = (
        Test.objects.annotate(name_key=LowerFn('name'))
        .values('name_key')
        .annotate(c=Count('id'))
        .filter(c__gt=1)
    )
    for row in groups:
        twins = list(Test.objects.filter(name__iexact=row['name_key']).order_by('pk'))
        if len(twins) < 2:
            continue
        # Prefer active + older id as stable keeper when historical models lack annotations.
        keeper = sorted(twins, key=lambda t: (0 if t.is_active else 1, t.pk))[0]
        for dup in twins:
            if dup.pk == keeper.pk:
                continue
            LabOrder.objects.filter(test_id=dup.pk).update(test_id=keeper.pk)
            for param in list(TestParameter.objects.filter(test_id=dup.pk)):
                if TestParameter.objects.filter(test_id=keeper.pk, code=param.code).exists():
                    param.delete()
                else:
                    param.test_id = keeper.pk
                    param.save(update_fields=['test'])
            for price in list(TestPrice.objects.filter(test_id=dup.pk)):
                if TestPrice.objects.filter(test_id=keeper.pk, tariff_id=price.tariff_id).exists():
                    price.delete()
                else:
                    price.test_id = keeper.pk
                    price.save(update_fields=['test'])
            ResultTemplate.objects.filter(test_id=dup.pk).update(test_id=keeper.pk)
            QcMaterial.objects.filter(test_id=dup.pk).update(test_id=keeper.pk)
            TatRule.objects.filter(test_id=dup.pk).update(test_id=keeper.pk)
            Test.objects.filter(default_template__test_id=dup.pk).update(default_template=None)
            dup.delete()
        cleaned = normalize(keeper.name)
        if cleaned and cleaned != keeper.name:
            keeper.name = cleaned
            keeper.save(update_fields=['name'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('laboratory', '0014_backfill_tariff_codes'),
    ]

    operations = [
        migrations.RunPython(dedupe_tests, noop_reverse),
        migrations.AddConstraint(
            model_name='test',
            constraint=models.UniqueConstraint(Lower('name'), name='uniq_test_name_ci'),
        ),
    ]
