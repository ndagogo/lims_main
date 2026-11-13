from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_organization_ops_alert_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='resultledger',
            name='collector_id',
            field=models.FileField(
                blank=True,
                help_text='Copy of the identification shown by a third party who collected the result.',
                upload_to='collector-ids/%Y/%m/',
            ),
        ),
    ]
