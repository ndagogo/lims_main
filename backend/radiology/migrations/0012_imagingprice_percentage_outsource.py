from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('radiology', '0011_report_approval'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagingprice',
            name='outsource_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='imagingprice',
            name='percentage',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Discount or markup percentage applied when configuring this tariff line.',
                max_digits=7,
            ),
        ),
    ]
