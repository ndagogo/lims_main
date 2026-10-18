import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_user_profile_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditevent',
            name='category',
            field=models.CharField(
                choices=[
                    ('AUTH', 'Authentication'),
                    ('ACCESS', 'Access control'),
                    ('CLINICAL', 'Clinical'),
                    ('BILLING', 'Billing / finance'),
                    ('ADMIN', 'Administration'),
                    ('INTEGRATION', 'Integrations'),
                    ('SYSTEM', 'System / HTTP'),
                    ('OTHER', 'Other'),
                ],
                db_index=True,
                default='OTHER',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='department',
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='patient_ref',
            field=models.CharField(blank=True, db_index=True, help_text='UHID or patient identifier', max_length=80),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='visit_ref',
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='order_ref',
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='result_ref',
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='previous_values',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='new_values',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='approval_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='audit_events_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='approval_note',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='evidence_ref',
            field=models.CharField(
                blank=True,
                help_text='Reference to supporting evidence (doc id, file path, ticket).',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='auditevent',
            name='evidence_note',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='auditevent',
            name='object_id',
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AddIndex(
            model_name='auditevent',
            index=models.Index(fields=['-created_at', 'action'], name='core_audite_created_1b2e8a_idx'),
        ),
        migrations.AddIndex(
            model_name='auditevent',
            index=models.Index(fields=['actor', '-created_at'], name='core_audite_actor_i_7c4d2f_idx'),
        ),
        migrations.AddIndex(
            model_name='auditevent',
            index=models.Index(fields=['centre', '-created_at'], name='core_audite_centre__9a1c4e_idx'),
        ),
    ]
