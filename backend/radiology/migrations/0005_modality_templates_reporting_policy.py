import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('radiology', '0004_ris_lifecycle_templates'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyexam',
            name='reporting_substitute',
            field=models.ForeignKey(
                blank=True, help_text='Authorised substitute reporting radiologist under controlled policy.',
                null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_exams_substitute', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='substitute_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='substitute_authorized_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_substitutes_authorized', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='radiologyexam',
            name='substitute_authorized_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name='radiologyexam',
            options={'permissions': [
                ('perform_exam', 'Can start and end an imaging exam'),
                ('authorize_reporting_substitute', 'Can authorise a reporting substitute radiologist'),
            ]},
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='indication_boilerplate',
            field=models.TextField(blank=True, help_text='Clinical indication / clinical information.'),
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='technique_boilerplate',
            field=models.TextField(blank=True, help_text='Acquisition / technique section.'),
        ),
        migrations.AddField(
            model_name='radiologyreporttemplate',
            name='extra_fields',
            field=models.JSONField(
                blank=True, default=list,
                help_text='Approved modality-specific fields: [{"code":"BI_RADS","label":"BI-RADS","boilerplate":""}].',
            ),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='indication',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='technique',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='extra_values',
            field=models.JSONField(blank=True, default=dict, help_text='Values for template extra_fields keyed by field code.'),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='addendum',
            field=models.TextField(
                blank=True,
                help_text='Cumulative formal addenda after sign/release (append-only via controlled action).',
            ),
        ),
        migrations.AlterModelOptions(
            name='radiologyreport',
            options={'permissions': [
                ('finalize_report', 'Can finalise a radiology report'),
                ('release_report', 'Can release a signed radiology report'),
                ('override_report', 'Can amend a final radiology report'),
                ('add_report_addendum', 'Can add a formal addendum to a signed radiology report'),
            ]},
        ),
        migrations.AddField(
            model_name='radiologyreportrevision',
            name='change_type',
            field=models.CharField(
                choices=[('AMENDMENT', 'Amendment'), ('ADDENDUM', 'Addendum')],
                default='AMENDMENT', max_length=12,
            ),
        ),
    ]
