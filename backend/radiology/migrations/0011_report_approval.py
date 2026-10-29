import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('radiology', '0010_accept_return_and_performance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='radiologyreport',
            name='status',
            field=models.CharField(
                choices=[
                    ('DRAFT', 'Draft'),
                    ('FINAL', 'Reported'),
                    ('APPROVED', 'Approved'),
                    ('RELEASED', 'Dispatched'),
                    ('AMENDED', 'Dispatched (amended)'),
                ],
                default='DRAFT',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='radiologyreport',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_reports_approved', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name='radiologyreport',
            options={
                'permissions': [
                    ('finalize_report', 'Can finalise a radiology report'),
                    ('release_report', 'Can release a signed radiology report'),
                    ('approve_result', 'Can approve a reported radiology result'),
                    ('authenticate_result', 'Can authenticate a reported radiology result'),
                    ('dispatch_result', 'Can dispatch an approved radiology result'),
                    ('modify_result', 'Can return a radiology result for modification'),
                    ('override_report', 'Can amend a final radiology report'),
                    ('add_report_addendum', 'Can add a formal addendum to a signed radiology report'),
                ],
            },
        ),
    ]
