import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('radiology', '0003_roadmap_gap_remediation'),
    ]

    operations = [
        migrations.AddField(
            model_name='radiologyexam',
            name='assigned_radiologist',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_exams_reporting', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name='RadiologyExamActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(max_length=40)),
                ('from_status', models.CharField(blank=True, max_length=12)),
                ('to_status', models.CharField(blank=True, max_length=12)),
                ('note', models.TextField(blank=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('browser', models.CharField(blank=True, max_length=255)),
                ('exam', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='activities', to='radiology.radiologyexam')),
                ('performed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='RadiologyReportTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('modality_type', models.CharField(choices=[('XRAY', 'X-ray'), ('CT', 'CT'), ('MRI', 'MRI'), ('US', 'Ultrasound'), ('MAMMO', 'Mammography'), ('ECG', 'ECG'), ('OTHER', 'Other')], max_length=10)),
                ('name', models.CharField(max_length=120)),
                ('version', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('PUBLISHED', 'Published'), ('RETIRED', 'Retired')], default='DRAFT', max_length=12)),
                ('scope', models.CharField(choices=[('PLATFORM', 'Platform'), ('PERSONAL', 'Personal')], default='PLATFORM', max_length=10)),
                ('instructions', models.TextField(blank=True)),
                ('report_header', models.TextField(blank=True)),
                ('findings_boilerplate', models.TextField(blank=True)),
                ('impression_boilerplate', models.TextField(blank=True)),
                ('recommendations_boilerplate', models.TextField(blank=True)),
                ('report_footer', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('cloned_from', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clones', to='radiology.radiologyreporttemplate')),
                ('owner', models.ForeignKey(blank=True, help_text='Null for platform templates; set for personal radiologist variants.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='radiology_report_templates', to=settings.AUTH_USER_MODEL)),
                ('procedure', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='report_templates', to='radiology.imagingprocedure')),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_templates_published', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['modality_type', 'name', '-version']},
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='template',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reports', to='radiology.radiologyreporttemplate'),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='template_version',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='configuration_snapshot',
            field=models.JSONField(blank=True, default=dict, help_text='Frozen template content at sign-off.'),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='released_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='released_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_reports_released', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='radiologyreport',
            name='status',
            field=models.CharField(choices=[('DRAFT', 'Draft'), ('FINAL', 'Signed'), ('RELEASED', 'Released'), ('AMENDED', 'Amended')], default='DRAFT', max_length=10),
        ),
        migrations.AlterModelOptions(
            name='radiologyreport',
            options={'permissions': [('finalize_report', 'Can finalise a radiology report'), ('release_report', 'Can release a signed radiology report'), ('override_report', 'Can amend a final radiology report')]},
        ),
        migrations.AddConstraint(
            model_name='radiologyreporttemplate',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', True)), fields=('modality_type', 'name', 'version'), name='uniq_platform_rad_report_template'),
        ),
        migrations.AddConstraint(
            model_name='radiologyreporttemplate',
            constraint=models.UniqueConstraint(condition=models.Q(('owner__isnull', False)), fields=('modality_type', 'name', 'version', 'owner'), name='uniq_personal_rad_report_template'),
        ),
    ]
