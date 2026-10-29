from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_result_ledger'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditevent',
            name='location_hint',
            field=models.CharField(
                blank=True,
                help_text='Coarse network location from trusted proxy headers (country/region). Not GPS.',
                max_length=120,
            ),
        ),
    ]
