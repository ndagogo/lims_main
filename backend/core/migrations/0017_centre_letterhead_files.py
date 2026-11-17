from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_resultledger_collector_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='centrereportformat',
            name='header_file',
            field=models.FileField(
                blank=True,
                help_text='Centre header artwork (PNG, JPG, or a one-page PDF) printed at the top of results.',
                upload_to='centre-letterhead/%Y/%m/',
            ),
        ),
        migrations.AddField(
            model_name='centrereportformat',
            name='footer_file',
            field=models.FileField(
                blank=True,
                help_text='Centre footer artwork (PNG, JPG, or a one-page PDF) printed at the bottom of results.',
                upload_to='centre-letterhead/%Y/%m/',
            ),
        ),
    ]
