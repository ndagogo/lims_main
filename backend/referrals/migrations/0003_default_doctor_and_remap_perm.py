from decimal import Decimal

from django.db import migrations, models


def ensure_defaults(apps, schema_editor):
    Centre = apps.get_model('core', 'Centre')
    ReferralDoctor = apps.get_model('referrals', 'ReferralDoctor')
    for centre in Centre.objects.all():
        if ReferralDoctor.objects.filter(centre=centre, is_default_placeholder=True).exists():
            continue
        code = f'{centre.code}-DEFAULT'
        if ReferralDoctor.objects.filter(doctor_code=code).exists():
            code = f'{centre.code}-DEFAULT-SYS'
        ReferralDoctor.objects.create(
            centre=centre,
            doctor_code=code,
            first_name='DEFAULT',
            last_name='DOCTOR',
            facility_name=centre.name,
            commission_type='PERCENT',
            commission_value=Decimal('0'),
            is_active=True,
            is_default_placeholder=True,
            notes='System placeholder when referring physician is unknown at registration.',
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('referrals', '0002_referralinstitution_referralattribution_institutions_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='referraldoctor',
            name='is_default_placeholder',
            field=models.BooleanField(
                default=False,
                help_text='Centre placeholder used when the referring physician is unknown at registration.',
            ),
        ),
        migrations.AlterModelOptions(
            name='referralattribution',
            options={
                'permissions': [
                    ('change_referring_doctor', 'Can change referring doctor on a visit'),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name='referraldoctor',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_default_placeholder=True),
                fields=('centre',),
                name='uniq_default_referral_doctor_per_centre',
            ),
        ),
        migrations.RunPython(ensure_defaults, noop_reverse),
    ]
