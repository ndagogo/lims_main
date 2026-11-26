from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0018_parameters_and_formats'),
    ]

    operations = [
        migrations.AddField(
            model_name='labresult',
            name='remarks',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='labresult',
            name='instrument_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='resultvalue',
            name='show_on_report',
            field=models.BooleanField(default=True),
        ),
    ]
