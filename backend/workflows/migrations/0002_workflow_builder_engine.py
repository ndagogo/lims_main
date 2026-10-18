import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0001_roadmap_gap_remediation'),
        ('laboratory', '0005_test_service_group_required'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowdefinition',
            name='status',
            field=models.CharField(
                choices=[('DRAFT', 'Draft'), ('PUBLISHED', 'Published'), ('RETIRED', 'Retired')],
                db_index=True, default='DRAFT', max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='workflowdefinition',
            name='expected_tat_minutes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workflowdefinition',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workflowdefinition',
            name='published_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='workflows_published', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='workflowdefinition',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='workflowdefinition',
            name='is_active',
            field=models.BooleanField(
                default=False,
                help_text='Exactly one published version per code should be active for new orders.',
            ),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='department',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='allowed_roles',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='required_fields',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='required_documents',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='expected_tat_minutes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='escalation_after_minutes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='escalation_roles',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='instructions',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='workflowstep',
            name='skip_allowed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='workflowtransition',
            name='label',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='workflowtransition',
            name='condition',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='workflowtransition',
            name='require_fields_complete',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='orderworkflowinstance',
            name='started_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='orderworkflowinstance',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='orderworkflowinstance',
            name='bound_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='orderworkflowinstance',
            name='bound_code',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.CreateModel(
            name='WorkflowAssignment',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('workflow_code', models.CharField(db_index=True, max_length=40)),
                ('priority', models.PositiveSmallIntegerField(default=100)),
                ('is_active', models.BooleanField(default=True)),
                ('service_group', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='workflow_assignments', to='laboratory.servicegroup',
                )),
                ('test', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='workflow_assignments', to='laboratory.test',
                )),
            ],
            options={'ordering': ['priority', 'workflow_code']},
        ),
        migrations.CreateModel(
            name='OrderWorkflowEvent',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(max_length=40)),
                ('note', models.TextField(blank=True)),
                ('detail', models.JSONField(blank=True, default=dict)),
                ('actor', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ('from_step', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to='workflows.workflowstep',
                )),
                ('instance', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='events', to='workflows.orderworkflowinstance',
                )),
                ('to_step', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='+', to='workflows.workflowstep',
                )),
            ],
            options={'ordering': ['created_at']},
        ),
        # Promote existing seeded definitions to published/active so production keeps working.
        migrations.RunSQL(
            sql="UPDATE workflows_workflowdefinition SET status='PUBLISHED', is_active=1 WHERE is_active=1 OR version=1;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
