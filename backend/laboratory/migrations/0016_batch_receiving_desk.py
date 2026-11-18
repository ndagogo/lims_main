from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('laboratory', '0015_unique_test_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='samplebatch',
            name='receive_comments',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='samplebatch',
            name='receive_temperature',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Recorded temperature (°C) when the batch was received at the department.',
                max_digits=5,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='batchsample',
            name='receive_disposition',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('SCANNED', 'Scanned'),
                    ('ACCEPTED', 'Accepted'),
                    ('MISSING', 'Missing'),
                    ('REJECTED', 'Rejected'),
                ],
                default='PENDING',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='batchsample',
            name='remarks',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='batchsample',
            name='scanned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='batchsample',
            name='scanned_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='batch_samples_scanned',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
