import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def seed_pause_reasons(apps, schema_editor):
    TatPauseReason = apps.get_model('laboratory', 'TatPauseReason')
    defaults = [
        ('PATIENT_UNAVAILABLE', 'Patient unavailable', True),
        ('INSTRUMENT_DOWN', 'Instrument downtime', True),
        ('REAGENT_STOCKOUT', 'Reagent / consumable stockout', True),
        ('AWAITING_CLINICAL_INFO', 'Awaiting clinical information', True),
        ('REFLEX_HOLD', 'Reflex / confirmatory hold', True),
        ('OUTSOURCE_PENDING', 'Outsourced test pending', True),
        ('DOCUMENTATION_ONLY', 'Documentation note (clock continues)', False),
    ]
    for code, name, pauses in defaults:
        TatPauseReason.objects.get_or_create(
            code=code,
            defaults={'name': name, 'pauses_clock': pauses, 'is_active': True},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('laboratory', '0005_test_service_group_required'),
    ]

    operations = [
        migrations.CreateModel(
            name='TatPauseReason',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('pauses_clock', models.BooleanField(default=True, help_text='When false the reason is logged but does not stop the TAT clock.')),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='TatRule',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('department', models.CharField(blank=True, default='', help_text='Optional department filter when matching service-group rules.', max_length=80)),
                ('clock_start', models.CharField(choices=[('REGISTRATION', 'Visit registration'), ('PAYMENT', 'Payment confirmation'), ('COLLECTION', 'Sample collection'), ('RECEIVE', 'Department receiving')], default='PAYMENT', max_length=20)),
                ('clock_end', models.CharField(choices=[('RELEASE', 'Controlled result release'), ('AUTHENTICATE', 'Result authentication'), ('REVIEW', 'First review complete')], default='RELEASE', max_length=20)),
                ('expected_minutes', models.PositiveIntegerField(help_text='Target turnaround in minutes (pause time excluded when pauses are allowed).')),
                ('warning_minutes', models.PositiveIntegerField(blank=True, help_text='Optional early warning threshold. Defaults to 80% of expected.', null=True)),
                ('escalation_minutes', models.PositiveIntegerField(blank=True, help_text='Minutes after which an overdue escalation alert is raised. Defaults to expected.', null=True)),
                ('escalation_roles', models.JSONField(blank=True, default=list, help_text='Group names notified / responsible for escalation.')),
                ('allow_pause', models.BooleanField(default=True)),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
                ('service_group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tat_rules', to='laboratory.servicegroup')),
                ('test', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tat_rules', to='laboratory.test')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='TatPause',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('note', models.TextField(blank=True)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('start_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('start_browser', models.CharField(blank=True, max_length=255)),
                ('end_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('end_browser', models.CharField(blank=True, max_length=255)),
                ('ended_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tat_pauses_ended', to=settings.AUTH_USER_MODEL)),
                ('reason', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pauses', to='laboratory.tatpausereason')),
                ('sample', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tat_pauses', to='laboratory.sample')),
                ('started_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tat_pauses_started', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-started_at']},
        ),
        migrations.CreateModel(
            name='TatEscalation',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('level', models.CharField(choices=[('WARNING', 'Warning'), ('OVERDUE', 'Overdue'), ('ESCALATED', 'Escalated')], max_length=12)),
                ('message', models.CharField(blank=True, max_length=255)),
                ('elapsed_minutes', models.PositiveIntegerField(default=0)),
                ('expected_minutes', models.PositiveIntegerField(default=0)),
                ('fired_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('detail', models.JSONField(blank=True, default=dict)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tat_escalations_acked', to=settings.AUTH_USER_MODEL)),
                ('rule', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='escalations', to='laboratory.tatrule')),
                ('sample', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tat_escalations', to='laboratory.sample')),
            ],
            options={'ordering': ['-fired_at']},
        ),
        migrations.AddConstraint(
            model_name='tatrule',
            constraint=models.CheckConstraint(condition=models.Q(('test__isnull', False), ('service_group__isnull', False), _connector='OR'), name='tat_rule_requires_test_or_group'),
        ),
        migrations.AddConstraint(
            model_name='tatescalation',
            constraint=models.UniqueConstraint(condition=models.Q(('acknowledged_at__isnull', True)), fields=('sample', 'level'), name='uniq_open_tat_escalation_level'),
        ),
        migrations.RunPython(seed_pause_reasons, noop_reverse),
    ]
