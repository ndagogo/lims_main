import django.db.models.deletion
from django.db import migrations, models


def seed_service_groups(apps, schema_editor):
    Test = apps.get_model('laboratory', 'Test')
    ServiceGroup = apps.get_model('laboratory', 'ServiceGroup')
    pairs = (
        Test.objects.exclude(service_group_name='')
        .values_list('service_group_name', 'service_group_code')
        .distinct()
    )
    # Also cover rows that only have a code (blank name).
    blank_codes = (
        Test.objects.filter(service_group_name='')
        .values_list('service_group_code', flat=True)
        .distinct()
    )
    group_by_key = {}
    for name, code in pairs:
        group, _ = ServiceGroup.objects.get_or_create(
            name=name, code=code or '1', defaults={'is_active': True},
        )
        group_by_key[(name, code or '1')] = group
    for code in blank_codes:
        key = ('', code or '1')
        if key not in group_by_key:
            group, _ = ServiceGroup.objects.get_or_create(
                name='', code=code or '1', defaults={'is_active': True},
            )
            group_by_key[key] = group
    for test in Test.objects.all().iterator():
        key = (test.service_group_name or '', test.service_group_code or '1')
        group = group_by_key.get(key)
        if group is None:
            group, _ = ServiceGroup.objects.get_or_create(
                name=key[0], code=key[1], defaults={'is_active': True},
            )
            group_by_key[key] = group
        test.service_group = group
        test.service_group_code = group.code
        test.save(update_fields=['service_group', 'service_group_code'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0003_alter_labresult_options_qcmaterial_qcrun_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80)),
                ('code', models.CharField(help_text='Single digit used in sample IDs', max_length=1)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['name', 'code'],
            },
        ),
        migrations.AddConstraint(
            model_name='servicegroup',
            constraint=models.UniqueConstraint(fields=('name', 'code'), name='uniq_service_group_name_code'),
        ),
        migrations.RenameField(
            model_name='test',
            old_name='service_group',
            new_name='service_group_name',
        ),
        migrations.AddField(
            model_name='test',
            name='service_group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tests',
                to='laboratory.servicegroup',
            ),
        ),
        migrations.RunPython(seed_service_groups, noop_reverse),
        migrations.RemoveField(
            model_name='test',
            name='service_group_name',
        ),
    ]
