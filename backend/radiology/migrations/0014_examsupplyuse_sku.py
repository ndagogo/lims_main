import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_consumables_enterprise'),
        ('radiology', '0013_procedure_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='examsupplyuse',
            name='sku',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_supply_uses',
                to='inventory.reagentsku',
            ),
        ),
        migrations.AddField(
            model_name='examsupplyuse',
            name='stock_movement',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='radiology_supply_uses',
                to='inventory.stockmovement',
            ),
        ),
    ]
