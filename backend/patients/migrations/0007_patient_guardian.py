from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0006_patient_can_modify_permission'),
    ]

    operations = [
        migrations.AddField(
            model_name='patient',
            name='guardian_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='patient',
            name='guardian_phone',
            field=models.CharField(blank=True, default='', max_length=30),
        ),
        migrations.AddField(
            model_name='patient',
            name='guardian_relationship',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
