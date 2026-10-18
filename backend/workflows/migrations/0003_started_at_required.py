from django.db import migrations, models
from django.utils import timezone


def fill_null_started_at(apps, schema_editor):
    OrderWorkflowInstance = apps.get_model('workflows', 'OrderWorkflowInstance')
    now = timezone.now()
    for row in OrderWorkflowInstance.objects.filter(started_at__isnull=True).iterator():
        row.started_at = getattr(row, 'created_at', None) or now
        row.save(update_fields=['started_at'])


class Migration(migrations.Migration):
    dependencies = [
        ('workflows', '0002_workflow_builder_engine'),
    ]

    operations = [
        migrations.RunPython(fill_null_started_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='orderworkflowinstance',
            name='started_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
