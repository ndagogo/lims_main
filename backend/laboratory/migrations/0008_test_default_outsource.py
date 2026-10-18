from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('laboratory', '0007_result_templates_catalogue'),
    ]

    operations = [
        migrations.AddField(
            model_name='test',
            name='default_outsource',
            field=models.BooleanField(
                default=False,
                help_text='When true, new orders for this test default to outsourced pricing/path if configured.',
            ),
        ),
    ]
