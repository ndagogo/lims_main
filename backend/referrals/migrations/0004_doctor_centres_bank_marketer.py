from django.conf import settings
from django.db import migrations, models


def copy_home_centre_and_hide_placeholders(apps, schema_editor):
    ReferralDoctor = apps.get_model('referrals', 'ReferralDoctor')
    ReferralDoctor.objects.filter(is_default_placeholder=True).update(appear_on_reports=False)
    for doctor in ReferralDoctor.objects.all().iterator():
        doctor.centres.add(doctor.centre_id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_user_staff_demographics'),
        ('referrals', '0003_default_doctor_and_remap_perm'),
    ]

    operations = [
        migrations.AddField(
            model_name='referraldoctor',
            name='account_name',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
        migrations.AddField(
            model_name='referraldoctor',
            name='account_number',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='referraldoctor',
            name='appear_on_reports',
            field=models.BooleanField(
                default=True,
                help_text='When unticked, this doctor’s name is left off laboratory and radiology reports.',
            ),
        ),
        migrations.AddField(
            model_name='referraldoctor',
            name='bank_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='referraldoctor',
            name='onboarded_by',
            field=models.ForeignKey(
                blank=True,
                help_text='Staff member, usually marketing, who introduced this doctor.',
                null=True,
                on_delete=models.SET_NULL,
                related_name='referral_doctors_onboarded',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='referraldoctor',
            name='centres',
            field=models.ManyToManyField(
                blank=True,
                help_text='Every centre this doctor can be chosen for. The home centre is always included.',
                related_name='assigned_referral_doctors',
                to='core.centre',
            ),
        ),
        migrations.RunPython(copy_home_centre_and_hide_placeholders, noop_reverse),
    ]
