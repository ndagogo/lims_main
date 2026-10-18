from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('radiology', '0005_modality_templates_reporting_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='variant_name',
            field=models.CharField(blank=True, help_text='Optional variant label, e.g. Adult / Paediatric.', max_length=120),
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='body_part',
            field=models.CharField(blank=True, help_text='Anatomical focus, e.g. Chest, Abdomen.', max_length=120),
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='visibility',
            field=models.CharField(
                choices=[('PUBLIC', 'Public (Shared with Everyone)'), ('PRIVATE', 'Private (Only Me)')],
                default='PUBLIC', max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='body_html',
            field=models.TextField(blank=True, help_text='Rich-text report body (HTML) composed in the template builder.'),
        ),
    ]
