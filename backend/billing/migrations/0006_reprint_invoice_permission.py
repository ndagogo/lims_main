from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_refund_bill_edit_fields'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='invoice',
            options={
                'permissions': [
                    ('cancel_invoice', 'Can cancel an invoice'),
                    ('approve_discount', 'Can approve invoice discounts'),
                    ('reprint_invoice', 'Can reprint an invoice'),
                ],
            },
        ),
    ]
