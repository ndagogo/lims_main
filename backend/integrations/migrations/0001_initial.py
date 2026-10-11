import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0001_initial'),
        ('laboratory', '0002_lims_workflow'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='Analyzer',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('manufacturer', models.CharField(blank=True, max_length=100)),
                ('model', models.CharField(blank=True, max_length=100)),
                ('protocol', models.CharField(choices=[('HL7', 'HL7 v2'), ('ASTM', 'ASTM'), ('TCP', 'Vendor TCP'), ('SERIAL', 'Serial/COM'), ('FILE', 'File drop'), ('MANUAL', 'Manual import')], max_length=10)),
                ('host', models.CharField(blank=True, help_text='Use private LAN address only; do not expose analyzer ports publicly.', max_length=255)),
                ('port', models.PositiveIntegerField(blank=True, null=True)),
                ('serial_port', models.CharField(blank=True, max_length=80)),
                ('instrument_identifier', models.CharField(blank=True, max_length=120)),
                ('is_active', models.BooleanField(default=True)),
                ('configuration', models.JSONField(blank=True, default=dict, help_text='Non-secret adapter settings; never store credentials here.')),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('centre', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='analyzers', to='core.centre')),
            ],
            options={'ordering': ['centre__code', 'name']},
        ),
        migrations.CreateModel(
            name='AnalyzerTestMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('instrument_test_code', models.CharField(max_length=100)),
                ('unit_override', models.CharField(blank=True, max_length=40)),
                ('enabled', models.BooleanField(default=True)),
                ('analyzer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mappings', to='integrations.analyzer')),
                ('parameter', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='laboratory.testparameter')),
                ('test', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='laboratory.test')),
            ],
        ),
        migrations.CreateModel(
            name='AnalyzerMessage',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('external_message_id', models.CharField(blank=True, db_index=True, max_length=150)),
                ('raw_payload', models.TextField(help_text='Restricted-access instrument payload; apply retention controls.')),
                ('parsed_payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('RECEIVED', 'Received'), ('PARSED', 'Parsed'), ('MAPPED', 'Mapped'), ('PENDING_REVIEW', 'Pending review'), ('ACCEPTED', 'Accepted'), ('REJECTED', 'Rejected'), ('ERROR', 'Error')], db_index=True, default='RECEIVED', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
                ('analyzer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='messages', to='integrations.analyzer')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('sample', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='analyzer_messages', to='laboratory.sample')),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['-received_at']},
        ),
        migrations.AddConstraint(model_name='analyzer', constraint=models.UniqueConstraint(fields=('centre', 'name'), name='uniq_analyzer_centre_name')),
        migrations.AddConstraint(model_name='analyzertestmapping', constraint=models.UniqueConstraint(fields=('analyzer', 'instrument_test_code'), name='uniq_analyzer_test_code')),
    ]
