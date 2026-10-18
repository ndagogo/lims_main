import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('laboratory', '0008_test_default_outsource'),
    ]

    operations = [
        migrations.AddField(
            model_name='sample',
            name='specimen_type',
            field=models.CharField(blank=True, help_text='Snapshot of specimen type at collection.', max_length=80),
        ),
        migrations.AddField(
            model_name='sample',
            name='disposition',
            field=models.CharField(
                blank=True,
                choices=[('', '—'), ('REJECTED', 'Rejected'), ('UNSUITABLE', 'Unsuitable'), ('SUPERSEDED', 'Superseded')],
                default='',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='sample',
            name='corrective_action',
            field=models.TextField(blank=True, help_text='Corrective action taken after rejection or unsuitable sample.'),
        ),
        migrations.AddField(
            model_name='sample',
            name='rejected_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='samples_rejected',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='sample',
            name='rejected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='sample',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING_COLLECTION', 'Pending collection'),
                    ('COLLECTED', 'Collected'),
                    ('BATCHED', 'Batched'),
                    ('RECEIVED', 'Department received'),
                    ('PROCESSING', 'Processing'),
                    ('RESULT_ENTERED', 'Result entered'),
                    ('UNDER_REVIEW', 'Under review'),
                    ('AUTHENTICATED', 'Authenticated'),
                    ('RELEASED', 'Released'),
                    ('REJECTED', 'Rejected'),
                    ('SUPERSEDED', 'Superseded by recollection'),
                    ('CANCELLED', 'Cancelled'),
                ],
                default='PENDING_COLLECTION',
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name='SampleBarcodePrint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('barcode_value', models.CharField(help_text='Value encoded in the barcode (usually sample_id).', max_length=32)),
                ('printed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('source', models.CharField(
                    choices=[
                        ('LABEL_PAGE', 'Label page'),
                        ('AUTO_PRINT', 'Auto-print after collect'),
                        ('API', 'API'),
                        ('MANUAL', 'Manual record'),
                    ],
                    default='LABEL_PAGE',
                    max_length=16,
                )),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('browser', models.CharField(blank=True, max_length=255)),
                ('printed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='sample_barcode_prints',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('sample', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='barcode_prints',
                    to='laboratory.sample',
                )),
            ],
            options={'ordering': ['-printed_at']},
        ),
    ]
