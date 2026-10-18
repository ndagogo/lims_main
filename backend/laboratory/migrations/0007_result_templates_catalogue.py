from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('laboratory', '0006_tat_rules_timeline'),
    ]

    operations = [
        migrations.AddField(
            model_name='test',
            name='collection_requirements',
            field=models.TextField(blank=True, help_text='Phlebotomy / collection requirements.'),
        ),
        migrations.AddField(
            model_name='test',
            name='sample_volume',
            field=models.CharField(blank=True, help_text='Required sample volume, e.g. 3 mL.', max_length=40),
        ),
        migrations.AddField(
            model_name='test',
            name='container',
            field=models.CharField(blank=True, help_text='Container / tube type.', max_length=80),
        ),
        migrations.AddField(
            model_name='test',
            name='handling_instructions',
            field=models.TextField(blank=True, help_text='Transport and handling instructions.'),
        ),
        migrations.AddField(
            model_name='test',
            name='default_workflow_code',
            field=models.CharField(blank=True, default='', help_text='Preferred workflow template code (e.g. NORMAL). Assignments still take precedence.', max_length=40),
        ),
        migrations.AddField(
            model_name='test',
            name='tat_target_minutes',
            field=models.PositiveIntegerField(blank=True, help_text='Catalogue turnaround target in minutes. Dedicated TatRule may override.', null=True),
        ),
        migrations.AddField(
            model_name='test',
            name='is_outsourceable',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='test',
            name='comments_required',
            field=models.BooleanField(default=False, help_text='Require a narrative/comment before result submission.'),
        ),
        migrations.AlterField(
            model_name='test',
            name='specimen',
            field=models.CharField(blank=True, help_text='Specimen type, e.g. EDTA whole blood.', max_length=80),
        ),
        migrations.AlterField(
            model_name='testparameter',
            name='result_type',
            field=models.CharField(
                choices=[
                    ('NUMBER', 'Numeric with unit'),
                    ('TEXT', 'Free-text interpretation'),
                    ('CHOICE', 'Single-select'),
                    ('MULTI_CHOICE', 'Multi-select'),
                    ('BOOLEAN', 'Yes / No'),
                    ('QUALITATIVE', 'Qualitative (e.g. Positive/Negative)'),
                    ('CALCULATED', 'Calculated parameter'),
                    ('TABLE', 'Table / differential row'),
                    ('COMMENT', 'Comment / specimen note'),
                    ('SECTION', 'Approved report section heading'),
                ],
                default='NUMBER',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='decimal_places',
            field=models.PositiveSmallIntegerField(blank=True, help_text='Decimal precision for numeric results. Empty = no forced rounding.', null=True),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='choices',
            field=models.JSONField(blank=True, default=list, help_text='Allowed values for CHOICE / MULTI_CHOICE / QUALITATIVE, e.g. ["Positive","Negative"].'),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='calculation',
            field=models.CharField(blank=True, help_text='Simple expression using sibling parameter codes, e.g. NEUT_ABS = WBC * NEUT_PCT / 100.', max_length=255),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='section_code',
            field=models.CharField(blank=True, default='', help_text='Group key for differentials / tables (e.g. DIFF).', max_length=40),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='require_comment_if_abnormal',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='testparameter',
            name='critical_comment_required',
            field=models.BooleanField(default=False, help_text='When a critical flag fires, require a documented comment.'),
        ),
        migrations.AlterField(
            model_name='testparameter',
            name='reference_range',
            field=models.CharField(blank=True, help_text='Legacy display fallback. Prefer age/sex ReferenceRange rows.', max_length=120),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='status',
            field=models.CharField(choices=[('DRAFT', 'Draft'), ('PUBLISHED', 'Published'), ('RETIRED', 'Retired')], default='DRAFT', max_length=12),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='report_header',
            field=models.TextField(blank=True, help_text='Approved header section for clinical reports.'),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='report_footer',
            field=models.TextField(blank=True, help_text='Approved footer / disclaimer section.'),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='published_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='result_templates_published', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='resulttemplate',
            name='cloned_from',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clones', to='laboratory.resulttemplate'),
        ),
        migrations.AlterModelOptions(
            name='resulttemplate',
            options={'ordering': ['test__code', 'name', '-version']},
        ),
        migrations.AddField(
            model_name='test',
            name='default_template',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='default_for_tests', to='laboratory.resulttemplate'),
        ),
        migrations.CreateModel(
            name='ResultTemplateField',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('display_order', models.PositiveSmallIntegerField(default=0)),
                ('is_required', models.BooleanField(blank=True, help_text='Override parameter.is_required when set.', null=True)),
                ('show_reference_range', models.BooleanField(default=True)),
                ('include_in_report', models.BooleanField(default=True)),
                ('section_label', models.CharField(blank=True, max_length=120)),
                ('parameter', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='template_fields', to='laboratory.testparameter')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fields', to='laboratory.resulttemplate')),
            ],
            options={'ordering': ['display_order', 'parameter__name']},
        ),
        migrations.AddConstraint(
            model_name='resulttemplatefield',
            constraint=models.UniqueConstraint(fields=('template', 'parameter'), name='uniq_template_parameter'),
        ),
        migrations.AddField(
            model_name='labresult',
            name='template_version',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='labresult',
            name='configuration_snapshot',
            field=models.JSONField(blank=True, default=dict, help_text='Frozen template, parameters and reference ranges at authentication.'),
        ),
        migrations.AddField(
            model_name='resultvalue',
            name='comment',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='resultvalue',
            name='reference_display',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='referencerange',
            name='population_label',
            field=models.CharField(blank=True, help_text='Laboratory-approved population label, e.g. Adult, Neonate.', max_length=80),
        ),
        migrations.AddField(
            model_name='referencerange',
            name='version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='referencerange',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
    ]
