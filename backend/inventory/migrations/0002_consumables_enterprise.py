# Enterprise consumables: department catalogue + stock ledger references

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_roadmap_gap_remediation'),
    ]

    operations = [
        migrations.AddField(
            model_name='reagentsku',
            name='department',
            field=models.CharField(
                choices=[
                    ('LABORATORY', 'Laboratory'),
                    ('RADIOLOGY', 'Radiology'),
                    ('PHLEBOTOMY', 'Phlebotomy'),
                    ('GENERAL', 'General / Shared'),
                ],
                db_index=True,
                default='LABORATORY',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='reagentsku',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='reagentsku',
            name='category',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Optional grouping e.g. Contrast, Vacutainer, PPE.',
                max_length=80,
            ),
        ),
        migrations.AlterModelOptions(
            name='reagentsku',
            options={
                'ordering': ['department', 'sku_code'],
                'verbose_name': 'Consumable SKU',
                'verbose_name_plural': 'Consumable SKUs',
            },
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reference_type',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reference_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='movement_type',
            field=models.CharField(
                choices=[
                    ('RECEIPT', 'Receipt / restock'),
                    ('ISSUE', 'Issue / usage'),
                    ('ADJUST', 'Adjustment'),
                    ('EXPIRE', 'Expire / wastage'),
                    ('RETURN', 'Return to stock'),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name='stockmovement',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Stock movement',
                'verbose_name_plural': 'Stock movements',
            },
        ),
    ]
