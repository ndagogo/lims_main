from django.db import migrations, models
import django.db.models.deletion


def dedupe_service_group_names(apps, schema_editor):
    ServiceGroup = apps.get_model('laboratory', 'ServiceGroup')
    seen = {}
    for group in ServiceGroup.objects.order_by('id'):
        key = group.name.strip().lower()
        if key not in seen:
            seen[key] = group.id
            continue
        # Keep the first; rename later duplicates so names stay unique.
        new_name = f'{group.name} ({group.code})'
        suffix = 2
        while ServiceGroup.objects.filter(name__iexact=new_name).exclude(pk=group.pk).exists():
            new_name = f'{group.name} ({group.code}-{suffix})'
            suffix += 1
        group.name = new_name
        group.save(update_fields=['name'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('laboratory', '0004_servicegroup_and_test_fk'),
    ]

    operations = [
        migrations.RunPython(dedupe_service_group_names, noop_reverse),
        migrations.AlterField(
            model_name='test',
            name='service_group',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tests',
                to='laboratory.servicegroup',
            ),
        ),
    ]
