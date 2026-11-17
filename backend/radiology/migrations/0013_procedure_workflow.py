from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('radiology', '0012_imagingprice_percentage_outsource'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagingprocedure',
            name='default_workflow_code',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Published workflow mapped to this investigation.',
                max_length=40,
            ),
        ),
    ]
