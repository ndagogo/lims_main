from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationoutbox',
            name='idempotency_key',
            field=models.CharField(blank=True, db_index=True, default='', max_length=120),
        ),
    ]
