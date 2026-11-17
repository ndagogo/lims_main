from django.db import migrations


def seed_self_tariffs(apps, schema_editor):
    Centre = apps.get_model('core', 'Centre')
    Tariff = apps.get_model('laboratory', 'Tariff')
    RegistrationChannel = apps.get_model('patients', 'RegistrationChannel')
    ClientType = apps.get_model('patients', 'ClientType')
    from django.utils import timezone

    for code, name, show, order in [
        ('CREDIT', 'Credit', False, 1),
        ('ON_PAYMENT', 'On Payment', False, 2),
        ('DEFAULT', 'Default', True, 3),
    ]:
        ClientType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'show_test_prices_default': show,
                'is_system': True,
                'sort_order': order,
                'is_active': True,
            },
        )

    channel_specs = [
        ('SELF', 'Self (B2C)', True),
        ('DOCTOR', 'Permanent doctors (B2D)', True),
        ('CREDIT_CLIENT', 'Credit clients (B2B)', False),
        ('ON_PAYMENT_CLIENT', 'On payment clients (B2B)', False),
    ]
    today = timezone.localdate()
    for centre in Centre.objects.filter(is_active=True):
        for code, name, show in channel_specs:
            RegistrationChannel.objects.get_or_create(
                centre_id=centre.pk,
                code=code,
                defaults={'name': name, 'show_test_prices': show, 'is_active': True},
            )
        tariff_name = f'{centre.code}_SELF'
        tariff = Tariff.objects.filter(name__iexact=tariff_name).first()
        if tariff is None:
            tariff = Tariff.objects.create(
                name=tariff_name,
                centre_id=centre.pk,
                code=f'TRF_{centre.code}_SELF',
                effective_from=today,
                status='APPROVAL',
                is_active=True,
                version=1,
            )
        self_channel = RegistrationChannel.objects.filter(centre_id=centre.pk, code='SELF').first()
        if self_channel and not self_channel.tariff_id:
            self_channel.tariff_id = tariff.pk
            self_channel.save(update_fields=['tariff'])
        doctor_channel = RegistrationChannel.objects.filter(centre_id=centre.pk, code='DOCTOR').first()
        if doctor_channel and not doctor_channel.tariff_id:
            doctor_channel.tariff_id = tariff.pk
            doctor_channel.save(update_fields=['tariff'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('patients', '0009_seed_client_types_and_channels'),
        ('laboratory', '0014_backfill_tariff_codes'),
    ]

    operations = [
        migrations.RunPython(seed_self_tariffs, noop_reverse),
    ]
