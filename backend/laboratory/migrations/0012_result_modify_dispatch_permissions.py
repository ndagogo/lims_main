from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0011_stat_types'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='labresult',
            options={
                'permissions': [
                    ('authenticate_labresult', 'Can authenticate a laboratory result'),
                    ('release_labresult', 'Can release a laboratory result'),
                    ('amend_labresult', 'Can amend a released laboratory result'),
                    ('modify_lab_result', 'Can return a laboratory result for modification'),
                    ('dispatch_lab_result', 'Can dispatch a laboratory result'),
                ],
            },
        ),
    ]
