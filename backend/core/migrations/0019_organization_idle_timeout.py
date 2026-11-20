from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_centre_phones_banks_receipt_footer_and_price_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationprofile',
            name='idle_timeout_minutes',
            field=models.PositiveIntegerField(
                default=10,
                help_text='Minutes of staff inactivity before automatic sign-out. Set 0 to disable.',
            ),
        ),
    ]
