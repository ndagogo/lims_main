import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial=True
    dependencies=[('patients','0001_initial'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='Invoice',fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('invoice_number',models.CharField(max_length=30,unique=True)),('payer_name',models.CharField(blank=True,max_length=180)),('subtotal',models.DecimalField(decimal_places=2,default=0,max_digits=12)),('discount_amount',models.DecimalField(decimal_places=2,default=0,max_digits=12)),('total',models.DecimalField(decimal_places=2,default=0,max_digits=12)),('status',models.CharField(default='UNPAID',max_length=20)),('visit',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='invoices',to='patients.visit'))]),
        migrations.CreateModel(name='Payment',fields=[('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('amount',models.DecimalField(decimal_places=2,max_digits=12)),('method',models.CharField(choices=[('CASH','Cash'),('POS','POS'),('TRANSFER','Transfer'),('CARD','Card'),('HMO','HMO'),('CREDIT','Credit')],max_length=20)),('reference',models.CharField(blank=True,max_length=100)),('receipt_number',models.CharField(max_length=40,unique=True)),('status',models.CharField(default='CONFIRMED',max_length=20)),('invoice',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='payments',to='billing.invoice')),('received_by',models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL))]),
    ]
