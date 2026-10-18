import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_roadmap_gap_remediation'),
    ]

    operations = [
        migrations.AddField(
            model_name='billamendment',
            name='added_description',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='billamendment',
            name='removed_description',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='billamendment',
            name='snapshot',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='billamendment',
            name='action',
            field=models.CharField(
                choices=[
                    ('ADD_ITEM', 'Add item'),
                    ('REMOVE_ITEM', 'Remove item'),
                    ('ADJUST_PRICE', 'Adjust price'),
                    ('REPLACE_ITEM', 'Replace item'),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name='billamendment',
            options={
                'ordering': ['-created_at'],
                'permissions': [
                    ('bill_edit', 'Can edit invoice lines (remove/replace tests)'),
                    ('bill_modify', 'Can modify invoice billing details'),
                ],
            },
        ),
        migrations.AddField(
            model_name='refund',
            name='invoice',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='refunds', to='billing.invoice',
            ),
        ),
        migrations.AddField(
            model_name='refund',
            name='invoice_item',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='refunds', to='billing.invoiceitem',
            ),
        ),
        migrations.AddField(
            model_name='refund',
            name='item_description',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='refund',
            name='refund_mode',
            field=models.CharField(
                choices=[
                    ('CANCEL_ONLY', 'Cancel test only'),
                    ('CANCEL_AND_REFUND', 'Cancel and refund'),
                ],
                default='CANCEL_AND_REFUND',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='refund',
            name='payment',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='refunds', to='billing.payment',
            ),
        ),
        migrations.AlterModelOptions(
            name='refund',
            options={
                'ordering': ['-created_at'],
                'permissions': [
                    ('issue_refund', 'Can issue a payment refund'),
                    ('payment_refund', 'Can refund individual billed tests'),
                ],
            },
        ),
    ]
