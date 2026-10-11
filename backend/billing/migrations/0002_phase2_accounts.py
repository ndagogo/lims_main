import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0001_initial'),
        ('laboratory', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(model_name='invoice', name='invoice_number', field=models.CharField(blank=True, max_length=30, unique=True)),
        migrations.AlterField(model_name='invoice', name='status', field=models.CharField(choices=[('DRAFT','Draft'),('UNPAID','Unpaid'),('PART_PAID','Part paid'),('PAID','Paid'),('CANCELLED','Cancelled')], default='DRAFT', max_length=20)),
        migrations.AddField(model_name='invoice', name='created_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices_created', to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='payment', name='receipt_number', field=models.CharField(blank=True, max_length=40, unique=True)),
        migrations.CreateModel(name='InvoiceItem', fields=[
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('description', models.CharField(blank=True, max_length=200)), ('quantity', models.PositiveSmallIntegerField(default=1)),
            ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)), ('outsource_price', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('line_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='billing.invoice')),
            ('lab_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='invoice_items', to='laboratory.laborder')),
            ('test', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='laboratory.test')),
        ], options={'ordering':['created_at']}),
        migrations.AddField(model_name='invoiceitem', name='tariff', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='laboratory.tariff')),
        migrations.CreateModel(name='DiscountRequest', fields=[
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('discount_type', models.CharField(choices=[('PERCENT','Percentage'),('FIXED','Fixed amount')], max_length=10)),
            ('requested_value', models.DecimalField(decimal_places=2, max_digits=10)), ('reason', models.TextField()),
            ('status', models.CharField(choices=[('PENDING','Pending'),('APPROVED','Approved'),('REJECTED','Rejected'),('CANCELLED','Cancelled')], default='PENDING', max_length=12)),
            ('review_note', models.TextField(blank=True)), ('applied_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='discount_requests', to='billing.invoice')),
            ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='discounts_requested', to=settings.AUTH_USER_MODEL)),
            ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='discounts_reviewed', to=settings.AUTH_USER_MODEL)),
        ]),
    ]
