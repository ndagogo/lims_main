from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0005_patient_notification_prefs'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='patient',
            options={
                'permissions': [
                    ('can_modify_patient_data', 'Can modify patient demographic data'),
                ],
            },
        ),
    ]
