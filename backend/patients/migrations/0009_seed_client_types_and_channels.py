from django.db import migrations


def seed_types_and_channels(apps, schema_editor):
    ClientType = apps.get_model('patients', 'ClientType')
    OrganisationClient = apps.get_model('patients', 'OrganisationClient')
    RegistrationChannel = apps.get_model('patients', 'RegistrationChannel')
    Centre = apps.get_model('core', 'Centre')

    defaults = [
        ('CREDIT', 'Credit', False, 1),
        ('ON_PAYMENT', 'On Payment', False, 2),
        ('DEFAULT', 'Default', True, 3),
    ]
    type_map = {}
    for code, name, show, order in defaults:
        row, _ = ClientType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'show_test_prices_default': show,
                'is_system': True,
                'sort_order': order,
                'is_active': True,
            },
        )
        type_map[code] = row

    for client in OrganisationClient.objects.all():
        if client.client_type_code:
            continue
        if client.kind == 'HMO':
            code = 'ON_PAYMENT'
            show = False
        else:
            code = 'CREDIT'
            show = False
        client.client_type_code = code
        client.client_type_id = type_map[code].pk
        client.show_test_prices = show
        if code == 'CREDIT':
            client.billing_type = client.billing_type or 'CREDIT'
        else:
            client.billing_type = client.billing_type or 'ON_PAYMENT'
        client.save()

    channel_specs = [
        ('SELF', 'Self (B2C)', True),
        ('DOCTOR', 'Permanent doctors (B2D)', True),
        ('CREDIT_CLIENT', 'Credit clients (B2B)', False),
        ('ON_PAYMENT_CLIENT', 'On payment clients (B2B)', False),
    ]
    for centre in Centre.objects.all():
        for code, name, show in channel_specs:
            RegistrationChannel.objects.get_or_create(
                centre_id=centre.pk,
                code=code,
                defaults={'name': name, 'show_test_prices': show, 'is_active': True},
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('patients', '0008_enterprise_clients_channels_tariffs'),
        ('core', '0016_resultledger_collector_id'),
    ]

    operations = [
        migrations.RunPython(seed_types_and_channels, noop_reverse),
    ]
