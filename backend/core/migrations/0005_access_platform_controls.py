from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_roadmap_gap_remediation'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='centre',
            options={
                'ordering': ['name'],
                'permissions': [
                    ('manage_centre_users', 'Can manage centre user access'),
                    ('access_platform_controls', 'Can access sensitive platform configuration controls'),
                ],
            },
        ),
    ]
