# Generated for the initial HFDN radiology workflow models.
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0001_initial'),
        ('patients', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='ImagingModality',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=20)),
                ('name', models.CharField(max_length=100)),
                ('modality_type', models.CharField(choices=[('XRAY','X-ray'),('CT','CT'),('MRI','MRI'),('US','Ultrasound'),('MAMMO','Mammography'),('ECG','ECG'),('OTHER','Other')], max_length=10)),
                ('ae_title', models.CharField(blank=True, max_length=16)),
                ('host', models.GenericIPAddressField(blank=True, null=True)),
                ('port', models.PositiveIntegerField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('centre', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='imaging_modalities', to='core.centre')),
            ],
            options={'ordering': ['centre__name', 'name']},
        ),
        migrations.AddConstraint(
            model_name='imagingmodality',
            constraint=models.UniqueConstraint(fields=('centre','code'), name='uniq_modality_code_per_centre'),
        ),
        migrations.CreateModel(
            name='ImagingOrder',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('priority', models.CharField(choices=[('ROUTINE','Routine'),('URGENT','Urgent'),('STAT','STAT')], default='ROUTINE', max_length=10)),
                ('clinical_history', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('ORDERED','Ordered'),('SCHEDULED','Scheduled'),('IN_PROGRESS','In progress'),('COMPLETED','Completed'),('CANCELLED','Cancelled')], default='ORDERED', max_length=12)),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('modality', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orders', to='radiology.imagingmodality')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='imaging_orders_requested', to=settings.AUTH_USER_MODEL)),
                ('visit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='imaging_orders', to='patients.visit')),
            ],
        ),
        migrations.CreateModel(
            name='RadiologyExam',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('ASSIGNED','Assigned'),('IN_PROGRESS','In progress'),('ACQUIRED','Acquired'),('CANCELLED','Cancelled')], default='ASSIGNED', max_length=12)),
                ('acquisition_notes', models.TextField(blank=True)),
                ('assigned_radiographer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_exams_assigned', to=settings.AUTH_USER_MODEL)),
                ('ended_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_exams_ended', to=settings.AUTH_USER_MODEL)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='exam', to='radiology.imagingorder')),
                ('started_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_exams_started', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='RadiologyReport',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('DRAFT','Draft'),('FINAL','Final'),('AMENDED','Amended')], default='DRAFT', max_length=10)),
                ('findings', models.TextField(blank=True)),
                ('impression', models.TextField(blank=True)),
                ('recommendations', models.TextField(blank=True)),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('last_override_at', models.DateTimeField(blank=True, null=True)),
                ('last_override_reason', models.TextField(blank=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='radiology_reports_authored', to=settings.AUTH_USER_MODEL)),
                ('exam', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='report', to='radiology.radiologyexam')),
                ('finalized_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_reports_finalized', to=settings.AUTH_USER_MODEL)),
                ('last_override_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='radiology_reports_overridden', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='RadiologyReportRevision',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField()),
                ('snapshot', models.JSONField(default=dict)),
                ('reason', models.TextField()),
                ('changed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='revisions', to='radiology.radiologyreport')),
            ],
            options={'ordering': ['-version']},
        ),
        migrations.AddConstraint(
            model_name='radiologyreportrevision',
            constraint=models.UniqueConstraint(fields=('report','version'), name='uniq_radiology_report_version'),
        ),
    ]
