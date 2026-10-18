from django.db import migrations, models


def backfill_job_title(apps, schema_editor):
    User = apps.get_model('core', 'User')
    for row in User.objects.exclude(signature_title='').filter(job_title=''):
        row.job_title = row.signature_title
        row.save(update_fields=['job_title'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_signatures_report_format'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='avatar',
            field=models.ImageField(
                blank=True,
                help_text='Staff profile photograph.',
                null=True,
                upload_to='avatars/%Y/%m/',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='job_title',
            field=models.CharField(
                blank=True,
                help_text='Professional designation, e.g. Consultant Radiologist.',
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='staff_id',
            field=models.CharField(blank=True, help_text='Employee / staff number.', max_length=40),
        ),
        migrations.AddField(
            model_name='user',
            name='department',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='user',
            name='specialty',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='user',
            name='qualifications',
            field=models.CharField(
                blank=True,
                help_text='Credentials shown on reports, e.g. MBBS, FWACS.',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='license_number',
            field=models.CharField(
                blank=True,
                help_text='Regulatory registration number (e.g. MDCN).',
                max_length=80,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='license_authority',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='user',
            name='phone_work',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='user',
            name='phone_mobile',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='user',
            name='office_extension',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='bio',
            field=models.TextField(blank=True, help_text='Short professional profile.'),
        ),
        migrations.AlterField(
            model_name='user',
            name='signature_title',
            field=models.CharField(
                blank=True,
                help_text='Legacy title under signature; prefer job_title. Kept in sync from profile.',
                max_length=120,
            ),
        ),
        migrations.RunPython(backfill_job_title, migrations.RunPython.noop),
    ]
