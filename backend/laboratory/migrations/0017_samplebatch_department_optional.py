from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('laboratory', '0016_batch_receiving_desk'),
    ]

    operations = [
        migrations.AlterField(
            model_name='samplebatch',
            name='department',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Derived label (single department or MIXED). Batches are scoped by centre, not department.',
                max_length=80,
            ),
        ),
    ]
