from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('radiology', '0014_examsupplyuse_sku'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='report_sections',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Ordered rich-text sections: [{"key":"findings","label":"Findings","html":"..."}].',
            ),
        ),
    ]
