import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('radiology', '0013_procedure_workflow'),
        ('workflows', '0004_railway_ready_field_updates'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowassignment',
            name='procedure',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='workflow_assignments',
                to='radiology.imagingprocedure',
            ),
        ),
    ]
