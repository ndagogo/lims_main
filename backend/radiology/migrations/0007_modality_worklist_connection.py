from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('radiology', '0006_rich_template_builder'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagingmodality',
            name='worklist_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Paid radiology worklist rows for this room are offered to the modality.',
            ),
        ),
        migrations.AddField(
            model_name='imagingmodality',
            name='last_echo_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='imagingmodality',
            name='last_echo_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='imagingmodality',
            name='last_echo_message',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AlterField(
            model_name='imagingmodality',
            name='ae_title',
            field=models.CharField(
                blank=True, help_text='DICOM AE title of this modality workstation.', max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='imagingmodality',
            name='host',
            field=models.GenericIPAddressField(
                blank=True, help_text='IP address of the modality workstation.', null=True,
            ),
        ),
        migrations.AlterField(
            model_name='imagingmodality',
            name='port',
            field=models.PositiveIntegerField(
                blank=True, help_text='DICOM port on the modality workstation.', null=True,
            ),
        ),
    ]
